"""Meaning-based retrieval that never leaves this Mac.

`nomic-embed-text` (Apache-2.0, 137M parameters) is served by the local Ollama daemon through its
`/api/embed` endpoint. Vectors are cached in `embeddings.sqlite3` beside the research journal, so a
document is embedded once per snapshot and a lesson once per version. Every helper degrades to
`None` when Ollama or the model is absent; callers keep their keyword ranking in that case, so the
feature is additive and the worker behaves exactly as before on a Mac without embeddings.

Privacy rule: text is only ever posted to a loopback endpoint. A non-local embedding URL is refused.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .security import ensure_within

DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_URL = "http://127.0.0.1:11434"
MAX_TEXT_CHARS = 2_000          # nomic has an 8K-token window; recall texts are previews, not whole documents
BATCH = 16
PROBE_TIMEOUT = 3.0
EMBED_TIMEOUT = 90.0
NEGATIVE_CACHE_S = 120.0        # after a failure, do not retry the daemon on every call
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}

# Cosine similarity → integer boost on the same scale as the keyword scorer (weight × hits).
# Measured on 26 Oct 2026 with nomic-embed-text (query/document prefixes): related pairs scored
# 0.47–0.61, unrelated 0.35–0.49. The floor is set for precision: a hint needs ≈0.56 to enter a
# prompt (MIN_CONTEXT_SCORE 3), a recall row ≈0.52 to count as a match. Passage search ranks
# relatively, so the floor does not apply there.
SIMILARITY_FLOOR = 0.50
SIMILARITY_GAIN = 50
RELATIVE_MARGIN = 0.05          # with several candidates, only those clearly above the median are boosted


def model_name() -> str:
    return os.environ.get("SQUIPI_EMBED_MODEL") or DEFAULT_MODEL


def endpoint() -> str:
    url = (os.environ.get("SQUIPI_OLLAMA_URL") or DEFAULT_URL).strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def enabled() -> bool:
    return (os.environ.get("SQUIPI_EMBEDDINGS") or "on").strip().lower() not in ("off", "0", "false", "no")


def _loopback(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme in ("http", "https") and (parts.hostname or "").lower() in LOOPBACK


def boost(similarity: Optional[float], floor: float = SIMILARITY_FLOOR) -> int:
    """Map cosine similarity onto the keyword score scale (0 when unrelated or unavailable)."""
    if similarity is None or similarity != similarity:
        return 0
    return max(0, int((similarity - floor) * SIMILARITY_GAIN + 0.5))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes) -> List[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


class Embedder:
    """Project-scoped embedding client with a SQLite vector cache."""

    _lock = threading.Lock()
    _state: Dict[str, float] = {"unavailable_until": 0.0}

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = ensure_within(self.root / "embeddings.sqlite3", self.root)
        self.model = model_name()
        self.url = endpoint()
        self._memory: Dict[str, List[float]] = {}

    # ----- availability -------------------------------------------------------------------------
    def available(self) -> bool:
        if not enabled() or not _loopback(self.url):
            return False
        if time.monotonic() < Embedder._state["unavailable_until"]:
            return False
        try:
            self._post([f"search_query: probe"], timeout=PROBE_TIMEOUT)
            return True
        except Exception:  # noqa: BLE001 — any failure means "keyword mode"
            self._mark_unavailable()
            return False

    def status(self, probe: bool = True) -> Dict[str, object]:
        """Configuration and, when `probe` is set, a live check of the local daemon (loads the model)."""
        info: Dict[str, object] = {"enabled": enabled(), "model": self.model, "endpoint": self.url, "local_only": _loopback(self.url)}
        if probe:
            ok = self.available()
            info.update({"available": ok, "mode": "semantic+keyword" if ok else "keyword"})
        return info

    def _mark_unavailable(self):
        Embedder._state["unavailable_until"] = time.monotonic() + NEGATIVE_CACHE_S

    # ----- embedding ----------------------------------------------------------------------------
    def embed(self, texts: Sequence[str], kind: str = "document") -> Optional[List[List[float]]]:
        """Vectors for `texts` (query or document role), or None when embeddings are unavailable."""
        if not texts or not enabled() or not _loopback(self.url):
            return None
        if time.monotonic() < Embedder._state["unavailable_until"]:
            return None
        prefix = "search_query: " if kind == "query" else "search_document: "
        prepared = [prefix + " ".join((t or "").split())[:MAX_TEXT_CHARS] for t in texts]
        keys = [self._key(p) for p in prepared]
        vectors: Dict[str, List[float]] = {}
        missing: List[int] = []
        with self._connect() as conn:
            for index, key in enumerate(keys):
                if key in self._memory:
                    vectors[key] = self._memory[key]
                    continue
                row = conn.execute("SELECT vector FROM vectors WHERE key=?", (key,)).fetchone()
                if row:
                    vectors[key] = _unpack(row[0])
                    self._memory[key] = vectors[key]
                else:
                    missing.append(index)
        for start in range(0, len(missing), BATCH):
            batch = missing[start:start + BATCH]
            try:
                fresh = self._post([prepared[i] for i in batch], timeout=EMBED_TIMEOUT)
            except Exception:  # noqa: BLE001
                self._mark_unavailable()
                return None
            if len(fresh) != len(batch):
                self._mark_unavailable()
                return None
            with self._connect() as conn:
                for i, vector in zip(batch, fresh):
                    vectors[keys[i]] = vector
                    self._memory[keys[i]] = vector
                    conn.execute("INSERT OR REPLACE INTO vectors(key, model, dims, vector, created) VALUES(?,?,?,?,?)",
                                 (keys[i], self.model, len(vector), _pack(vector), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        return [vectors[k] for k in keys]

    def similarities(self, query: str, texts: Sequence[str]) -> Optional[List[float]]:
        """Cosine similarity of one query to each text, or None in keyword mode."""
        if not query.strip() or not texts:
            return None
        q = self.embed([query], kind="query")
        if not q:
            return None
        docs = self.embed(list(texts), kind="document")
        if not docs:
            return None
        return [cosine(q[0], d) for d in docs]

    def boosts(self, query: str, texts: Sequence[str]) -> List[int]:
        """Integer score boosts aligned with `texts`; all zeros in keyword mode."""
        sims = self.similarities(query, texts)
        if not sims:
            return [0] * len(texts)
        floor = SIMILARITY_FLOOR
        if len(sims) >= 4:
            ordered = sorted(sims)
            median = ordered[len(ordered) // 2]
            floor = max(floor, median + RELATIVE_MARGIN)
        return [boost(s, floor) for s in sims]

    # ----- internals ----------------------------------------------------------------------------
    def _key(self, prepared: str) -> str:
        return hashlib.sha256((self.model + "\n" + prepared).encode("utf-8")).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY, model TEXT NOT NULL, dims INTEGER NOT NULL, "
                     "vector BLOB NOT NULL, created TEXT NOT NULL)")
        # The project library: which passage of which document snapshot each vector describes.
        conn.execute("CREATE TABLE IF NOT EXISTS passages(doc_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, kind TEXT NOT NULL, "
                     "offset INTEGER NOT NULL, end_offset INTEGER NOT NULL, key TEXT NOT NULL, model TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', "
                     "PRIMARY KEY(doc_id, snapshot_id, kind, offset))")
        return conn

    # ----- project library (passages of every document, searchable together) ----------------------
    def index_passages(self, doc_id: str, snapshot_id: str, chunks: Sequence[Dict[str, object]]) -> Optional[int]:
        """Embed and record passages (`offset`, `end_offset`, `text`, optional `kind`, `label`) of one document
        snapshot. Idempotent per (doc, snapshot, kind, offset). Returns the number indexed, or None in keyword mode."""
        if not chunks:
            return 0
        with self._connect() as conn:
            have = {(row[0], row[1]) for row in conn.execute("SELECT kind, offset FROM passages WHERE doc_id=? AND snapshot_id=? AND model=?",
                                                              (doc_id, snapshot_id, self.model))}
        todo = [c for c in chunks if (str(c.get("kind", "text")), int(c["offset"])) not in have]
        if not todo:
            return 0
        vectors = self.embed([str(c["text"]) for c in todo], kind="document")
        if vectors is None:
            return None
        prefix = "search_document: "
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._connect() as conn:
            for c, vector in zip(todo, vectors):
                prepared = prefix + " ".join(str(c["text"]).split())[:MAX_TEXT_CHARS]
                key = self._key(prepared)
                # The vector row normally exists from embed()'s cache; make sure the join never dangles.
                conn.execute("INSERT OR IGNORE INTO vectors(key, model, dims, vector, created) VALUES(?,?,?,?,?)",
                             (key, self.model, len(vector), _pack(vector), stamp))
                conn.execute("INSERT OR REPLACE INTO passages(doc_id, snapshot_id, kind, offset, end_offset, key, model, label) VALUES(?,?,?,?,?,?,?,?)",
                             (doc_id, snapshot_id, str(c.get("kind", "text")), int(c["offset"]), int(c["end_offset"]), key, self.model, str(c.get("label", ""))))
        return len(todo)

    def indexed_snapshots(self) -> Dict[str, str]:
        """doc_id → snapshot_id currently held in the library index."""
        with self._connect() as conn:
            return {row[0]: row[1] for row in conn.execute("SELECT DISTINCT doc_id, snapshot_id FROM passages WHERE model=?", (self.model,))}

    def search_passages(self, query: str, doc_ids: Optional[Sequence[str]] = None, limit: int = 8) -> Optional[List[Dict[str, object]]]:
        """Top passages across the indexed library by meaning, or None in keyword mode."""
        q = self.embed([query], kind="query")
        if not q:
            return None
        with self._connect() as conn:
            sql = "SELECT p.doc_id, p.snapshot_id, p.kind, p.offset, p.end_offset, p.label, v.vector FROM passages p JOIN vectors v ON v.key=p.key WHERE p.model=?"
            params: List[object] = [self.model]
            if doc_ids:
                sql += " AND p.doc_id IN (%s)" % ",".join("?" * len(doc_ids))
                params.extend(doc_ids)
            rows = conn.execute(sql, params).fetchall()
        scored = []
        for doc_id, snapshot_id, kind, offset, end, label, blob in rows:
            scored.append((cosine(q[0], _unpack(blob)), doc_id, snapshot_id, kind, offset, end, label))
        scored.sort(key=lambda item: item[0], reverse=True)
        out: List[Dict[str, object]] = []
        for score, doc_id, snapshot_id, kind, offset, end, label in scored:
            if len(out) >= limit:
                break
            if any(o["document_id"] == doc_id and o["kind"] == kind and abs(int(o["offset"]) - offset) < max(1, (end - offset)) // 2 for o in out):
                continue
            out.append({"document_id": doc_id, "snapshot_id": snapshot_id, "kind": kind, "offset": offset, "end_offset": end,
                        "label": label, "score": round(float(score), 4)})
        return out

    def _post(self, inputs: List[str], timeout: float) -> List[List[float]]:
        if not _loopback(self.url):
            raise RuntimeError("embedding endpoint must be loopback")
        body = json.dumps({"model": self.model, "input": inputs, "truncate": True, "keep_alive": "10m"}).encode("utf-8")
        request = urllib.request.Request(self.url + "/api/embed", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        with Embedder._lock:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — loopback only
                payload = json.loads(response.read().decode("utf-8"))
        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or not all(isinstance(v, list) and v for v in vectors):
            raise RuntimeError("malformed embedding response")
        return [[float(x) for x in v] for v in vectors]


def chunk_offsets(total: int, size: int = 1_200, stride: int = 1_000, limit: int = 400) -> List[int]:
    """Window starts covering `total` characters; the stride grows for very long texts so the count stays bounded."""
    if total <= 0:
        return []
    if total <= size:
        return [0]
    while (total - size) // stride + 1 > limit:
        stride *= 2
        size = max(size, stride + 200)
    starts = list(range(0, max(1, total - size) + 1, stride))
    if starts[-1] + size < total:
        starts.append(max(0, total - size))
    return starts


def semantic_passages(embedder: Embedder, query: str, full: str, limit: int = 5, size: int = 1_200) -> Optional[List[Dict[str, object]]]:
    """Top passages of `full` by meaning, or None in keyword mode. Each item: offset, end_offset, score."""
    starts = chunk_offsets(len(full), size=size)
    if not starts:
        return []
    width = size if len(starts) <= 1 else max(size, (starts[1] - starts[0]) + 200)
    chunks = [full[s:s + width] for s in starts]
    sims = embedder.similarities(query, chunks)
    if sims is None:
        return None
    ranked = sorted(zip(sims, starts), key=lambda item: item[0], reverse=True)
    out: List[Dict[str, object]] = []
    for score, start in ranked:
        if len(out) >= limit:
            break
        if any(abs(start - int(o["offset"])) < width // 2 for o in out):
            continue  # neighbouring overlapping window of the same passage
        out.append({"offset": start, "end_offset": min(len(full), start + width), "score": round(float(score), 4)})
    return out
