"""A project is a directory the app owns. The worker keeps a small registry of datasets
(references to the user's original files; originals are never modified), writes every
artifact under artifacts/, and appends an audit line for each tool call."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .security import AllowList, ensure_within


@dataclass
class Document:
    id: str
    name: str
    path: str          # original file, read-only for us
    kind: str          # pdf | docx | pptx | text
    added: float = 0.0
    pages: Optional[int] = None
    words: Optional[int] = None
    text_path: Optional[str] = None   # extracted text, inside the project

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Dataset:
    id: str
    name: str
    path: str          # original file, read-only for us
    format: str        # csv | excel | parquet
    sheet: Optional[str] = None
    added: float = 0.0
    rows: Optional[int] = None
    cols: Optional[int] = None
    overrides: Dict[str, Any] = None   # user corrections to inferred metadata: {column: {"role": "date"}}
    derived_from: Optional[str] = None  # dataset id this one was cleaned/derived from, when it lives in the project
    columns: Optional[List[str]] = None # header names, so a model can plan without profiling first

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["overrides"] = d.get("overrides") or {}
        return d


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")
    return (s or "item")[:n]


class Project:
    def __init__(self, root: str | os.PathLike[str], allow: AllowList):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        self.allow = allow
        # the worker may read back what it wrote (cleaned datasets, extracted text) and nothing else by default
        self.allow.allow(self.root)
        self._registry_path = self.root / "datasets.json"
        self._datasets: Dict[str, Dataset] = {}
        self._documents: Dict[str, Document] = {}
        self._load()

    # ---- datasets
    def _load(self) -> None:
        if self._registry_path.exists():
            try:
                raw = json.loads(self._registry_path.read_text("utf-8"))
                for d in raw.get("datasets", []):
                    self._datasets[d["id"]] = Dataset(**d)
                for d in raw.get("documents", []):
                    self._documents[d["id"]] = Document(**d)
                # files the user gave Squipi earlier (dropped or picked) stay readable in later sessions;
                # the registry is exactly the record of those grants
                for d in list(self._datasets.values()) + list(self._documents.values()):
                    if os.path.exists(d.path):
                        self.allow.allow(d.path)
            except Exception:
                # a damaged registry is not fatal; the user can re-import
                self._datasets = {}
                self._documents = {}

    def _save(self) -> None:
        data = {"version": 2, "datasets": [d.to_json() for d in self._datasets.values()],
                "documents": [d.to_json() for d in self._documents.values()]}
        tmp = self._registry_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")
        os.replace(tmp, self._registry_path)

    def datasets(self) -> List[Dataset]:
        return sorted(self._datasets.values(), key=lambda d: d.added)

    def dataset(self, dataset_id: str) -> Dataset:
        d = self._datasets.get(dataset_id)
        if d is None:
            # allow lookup by name as a convenience for models
            for cand in self._datasets.values():
                if cand.name == dataset_id:
                    return cand
            raise KeyError(f"no dataset '{dataset_id}' in this project; import it first")
        return d

    def register_dataset(self, path: Path, fmt: str, sheet: Optional[str], rows: int, cols: int, columns: Optional[List[str]] = None) -> Dataset:
        for d in self._datasets.values():
            if d.path == str(path) and d.sheet == sheet:
                d.rows, d.cols = rows, cols
                if columns: d.columns = columns[:200]
                d.added = time.time()          # re-dropped: it is the current dataset again
                self._save()
                return d
        ds = Dataset(id=f"ds_{uuid.uuid4().hex[:8]}", name=path.name, path=str(path), format=fmt, sheet=sheet,
                     added=time.time(), rows=rows, cols=cols, overrides={}, columns=(columns or [])[:200] or None)
        self._datasets[ds.id] = ds
        self._save()
        return ds

    def register_derived(self, path: Path, name: str, rows: int, cols: int, derived_from: str, columns: Optional[List[str]] = None) -> Dataset:
        """A dataset the worker wrote itself (e.g. a cleaned copy). It lives inside the project directory."""
        ensure_within(path, self.root)
        ds = Dataset(id=f"ds_{uuid.uuid4().hex[:8]}", name=name, path=str(path), format="csv", sheet=None,
                     added=time.time(), rows=rows, cols=cols, overrides={}, derived_from=derived_from, columns=(columns or [])[:200] or None)
        self._datasets[ds.id] = ds
        self._save()
        return ds

    def record_dataset_sources(self,dataset_id,source_ids):
        path=self.root/'dataset-lineage.json'
        data=json.loads(self.allow.check_readable(path).read_text()) if path.exists() else {}
        data[dataset_id]=list(source_ids)
        self.write_json(path,data)

    def dataset_lineage(self,dataset_id):
        """Registered ancestors, including multi-source joins. No source files are inferred."""
        path=self.root/'dataset-lineage.json'
        links=json.loads(self.allow.check_readable(path).read_text()) if path.exists() else {}
        result=[];visited=set();queue=[dataset_id]
        while queue:
            current=queue.pop(0)
            if current in visited:continue
            if len(visited)>=100:raise ValueError('Dataset lineage exceeds 100 ancestors')
            visited.add(current);ds=self.dataset(current);result.append(ds)
            queue.extend(links.get(current,[]))
            if ds.derived_from:queue.append(ds.derived_from)
        return result

    # ---- documents
    def documents(self) -> List[Document]:
        return sorted(self._documents.values(), key=lambda d: d.added)

    def document(self, doc_id: str) -> Document:
        d = self._documents.get(doc_id)
        if d is None:
            for cand in self._documents.values():
                if cand.name == doc_id:
                    return cand
            raise KeyError(f"no document '{doc_id}' in this project; read it first")
        return d

    def register_document(self, path: Path, kind: str, pages: int, words: int, text_path: Path) -> Document:
        for d in self._documents.values():
            if d.path == str(path):
                d.pages, d.words, d.text_path = pages, words, str(text_path)
                self._save()
                return d
        doc = Document(id=f"doc_{uuid.uuid4().hex[:8]}", name=path.name, path=str(path), kind=kind, added=time.time(),
                       pages=pages, words=words, text_path=str(text_path))
        self._documents[doc.id] = doc
        self._save()
        return doc

    def forget(self, item_id: str) -> bool:
        """Drop a dataset or document from the registry. The user's file is never touched."""
        if item_id in self._datasets:
            del self._datasets[item_id]
        elif item_id in self._documents:
            del self._documents[item_id]
        else:
            return False
        self._save()
        return True

    def set_overrides(self, dataset_id: str, overrides: Dict[str, Any]) -> Dataset:
        ds = self.dataset(dataset_id)
        ds.overrides = {**(ds.overrides or {}), **overrides}
        self._save()
        return ds

    # ---- artifacts
    def new_artifact_dir(self, kind: str, label: str) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        d = self.root / "artifacts" / f"{stamp}_{slug(kind)}_{slug(label)}"
        i = 1
        while d.exists():
            i += 1
            d = self.root / "artifacts" / f"{stamp}_{slug(kind)}_{slug(label)}-{i}"
        d.mkdir(parents=True)
        return ensure_within(d, self.root)

    def write_text(self, path: Path, text: str) -> Path:
        p = ensure_within(path, self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, "utf-8")
        return p

    def write_json(self, path: Path, obj: Any) -> Path:
        return self.write_text(path, json.dumps(obj, indent=2, sort_keys=False, default=_json_default))

    # ---- audit trail
    def audit(self, event: Dict[str, Any]) -> None:
        observed = {"t": time.time(), **event}
        # A host-supplied tag lets the local UI follow its current Hermes run.
        # This is activity metadata, never authorization to execute a tool.
        task_id = os.environ.get("SQUIPI_RESEARCH_TASK_ID")
        if task_id:
            observed["research_task_id"] = task_id
        line = json.dumps(observed, default=_json_default)
        with open(self.root / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _json_default(o: Any):
    try:
        import numpy as np  # noqa
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:
        pass
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)
