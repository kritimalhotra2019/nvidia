"""Meaning-based retrieval: local embeddings boost recall, hints and passage search; keyword mode when absent."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from squipi_worker import embeddings, tools  # noqa: F401
from squipi_worker.embeddings import Embedder, boost, chunk_offsets, cosine, semantic_passages
from squipi_worker.learning import LearningStore
from squipi_worker.project import Project
from squipi_worker.registry import JobContext, registry
from squipi_worker.research import ResearchStore
from squipi_worker.security import AllowList


# A deterministic stand-in for nomic: a bag-of-concepts vector so that synonyms land together.
CONCEPTS = {
    "attrition": 0, "follow-up": 0, "retention": 0, "dropout": 0,
    "consumption": 1, "spending": 1, "expenditure": 1,
    "roads": 2, "rehabilitation": 2, "transport": 2,
    "anonymise": 3, "anonymize": 3, "personal": 3, "identif": 3, "pii": 3, "redact": 3,
    "treatment": 4, "control": 4, "effect": 4, "impact": 4,
}


def fake_embed(texts, kind="document"):
    out = []
    for text in texts:
        vector = [0.05] * 6
        folded = text.lower()
        for word, slot in CONCEPTS.items():
            if word in folded:
                vector[slot] += 1.0
        out.append(vector)
    return out


@pytest.fixture
def project(tmp_path):
    return Project(tmp_path / "project", AllowList())


@pytest.fixture
def fake_embedder(monkeypatch):
    monkeypatch.setattr(Embedder, "embed", lambda self, texts, kind="document": fake_embed(texts, kind))
    monkeypatch.setattr(Embedder, "available", lambda self: True)
    monkeypatch.setattr(embeddings.Embedder, "_state", {"unavailable_until": 0.0})
    yield


@pytest.fixture
def no_embedder(monkeypatch):
    monkeypatch.setattr(Embedder, "embed", lambda self, texts, kind="document": None)
    monkeypatch.setattr(Embedder, "available", lambda self: False)
    yield


def call(project, name, **args):
    return registry.call(name, args, JobContext(project, lambda *_: None, lambda: False))


def test_boost_scale_matches_keyword_weights():
    assert boost(None) == 0
    assert boost(0.49) == 0         # the most similar unrelated pair measured on the real model
    assert boost(0.56) == 3         # passes the hint gate (MIN_CONTEXT_SCORE = 3)
    assert boost(0.61) >= 5         # "household spending impact" vs the consumption-effect task
    assert boost(0.80) == 15
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)


def test_non_loopback_endpoint_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("SQUIPI_OLLAMA_URL", "http://embeddings.example.com:11434")
    e = Embedder(tmp_path)
    assert e.available() is False
    assert e.embed(["private survey text"]) is None
    assert e.status(probe=False)["local_only"] is False


def test_disabled_by_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SQUIPI_EMBEDDINGS", "off")
    e = Embedder(tmp_path)
    assert e.embed(["anything"]) is None
    assert e.boosts("q", ["a", "b"]) == [0, 0]


def test_vectors_are_cached_locally(monkeypatch, tmp_path):
    calls = []

    def fake_post(self, inputs, timeout):
        calls.append(list(inputs))
        return [[1.0, 0.0, float(len(t))] for t in inputs]

    monkeypatch.setenv("SQUIPI_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("SQUIPI_EMBEDDINGS", "on")
    monkeypatch.setattr(Embedder, "_post", fake_post)
    monkeypatch.setattr(embeddings.Embedder, "_state", {"unavailable_until": 0.0})
    e = Embedder(tmp_path)
    first = e.embed(["alpha", "beta"])
    again = Embedder(tmp_path).embed(["beta", "alpha", "gamma"])  # new instance: SQLite, not memory
    assert first is not None and again is not None
    assert len(calls) == 2 and calls[1] == ["search_document: gamma"]
    assert (tmp_path / "embeddings.sqlite3").exists()


def test_chunk_offsets_cover_text_and_stay_bounded():
    assert chunk_offsets(0) == []
    assert chunk_offsets(500) == [0]
    starts = chunk_offsets(10_000)
    assert starts[0] == 0 and starts[-1] + 1_200 >= 10_000
    assert len(chunk_offsets(5_000_000)) <= 400


def test_semantic_passages_find_meaning_not_words(fake_embedder, tmp_path):
    filler = "Village roads were rehabilitated under the programme. " * 30
    about_attrition = "Of the baseline sample, 14 percent could not be re-interviewed at endline; loss to follow-up was higher among the poorest. "
    full = filler + about_attrition * 10 + filler
    ranked = semantic_passages(Embedder(tmp_path), "what is the attrition caveat", full, limit=2)
    assert ranked and ranked[0]["offset"] <= len(filler) + 400
    assert ranked[0]["end_offset"] > len(filler)
    assert "score" in ranked[0]


def test_search_document_semantic_mode_and_literal_fallback(project, tmp_path, fake_embedder, monkeypatch):
    doc = tmp_path / "report.md"
    doc.write_text("# Methods\n" + ("The roads programme rehabilitated 120 km of rural roads. " * 40) +
                   "\n# Sample\n" + ("Loss to follow-up reached 14 percent; retention was lowest in the poorest quintile. " * 20) +
                   "\n# Results\n" + ("Consumption rose relative to comparison villages. " * 40))
    project.allow.allow(doc)
    first = call(project, "read_document", path=str(doc))
    semantic = call(project, "search_document", document_id=first["document_id"], query="attrition caveat", mode="semantic", max_matches=2)
    assert semantic["mode"] == "semantic" and semantic["matches"]
    top = semantic["matches"][0]
    window = call(project, "read_document", document_id=first["document_id"], offset=top["offset"], max_chars=top["end_offset"] - top["offset"])
    assert "follow-up" in window["text"]  # the ranked window covers the passage; the host reads from its offset
    assert semantic["content_trust"] == "untrusted_document_data"
    literal = call(project, "search_document", document_id=first["document_id"], query="attrition")
    assert literal["mode"] == "literal" and literal["matches"] == []

    monkeypatch.setattr(Embedder, "embed", lambda self, texts, kind="document": None)
    fallback = call(project, "search_document", document_id=first["document_id"], query="attrition", mode="semantic")
    assert fallback["mode"] == "literal" and "unavailable" in fallback["fallback"]


def test_recall_finds_task_by_meaning(project, fake_embedder):
    store = ResearchStore(project.root)
    t1 = store.start_research_task("Estimate the programme effect on endline consumption for treatment versus control households")["task_id"]
    store.finish_research_task(t1, "completed", "Treatment households consumed more at endline.")
    t2 = store.start_research_task("Rehabilitate the roads transport dataset")["task_id"]
    store.finish_research_task(t2, "completed", "Roads dataset cleaned.")
    found = store.recall_research("household spending impact")
    tasks = [t["task_id"] for t in found["tasks"]]
    assert tasks and tasks[0] == t1


def test_recall_keyword_mode_unchanged_without_embeddings(project, no_embedder):
    store = ResearchStore(project.root)
    t1 = store.start_research_task("Estimate the programme effect on endline consumption")["task_id"]
    store.finish_research_task(t1, "completed", "done")
    assert store.recall_research("household spending")["tasks"] == []
    assert store.recall_research("consumption")["tasks"]


def test_learned_hint_fires_on_synonym(project, fake_embedder):
    store = LearningStore(project.root)
    with store.connect() as c:
        c.execute("INSERT INTO lessons(id,fingerprint,kind,title,body,trigger,tools,checks,evidence,origin_task,state,version,uses,updated) "
                  "VALUES('l1','fp','procedure','Remove personal data before export','Drop identifying columns first.',"
                  "'anonymise household survey',?,?,?,NULL,'trial',1,0,'2026-10-26T00:00:00Z')",
                  (json.dumps(["clean_dataset"]), json.dumps([]), json.dumps([])))
    hints = store.context("redact PII from the beneficiary roster")
    assert [h["id"] for h in hints["lessons"]] == ["l1"]


def test_learned_hint_gate_unchanged_without_embeddings(project, no_embedder):
    store = LearningStore(project.root)
    with store.connect() as c:
        c.execute("INSERT INTO lessons(id,fingerprint,kind,title,body,trigger,tools,checks,evidence,origin_task,state,version,uses,updated) "
                  "VALUES('l1','fp','procedure','Remove personal data before export','Drop identifying columns first.',"
                  "'anonymise household survey',?,?,?,NULL,'trial',1,0,'2026-10-26T00:00:00Z')",
                  (json.dumps(["clean_dataset"]), json.dumps([]), json.dumps([])))
    assert store.context("redact PII from the beneficiary roster")["lessons"] == []
    assert store.context("anonymise this household survey")["lessons"]
