"""Worker tests are deterministic: the optional local embedding model is off unless a test opts in."""
import os

import pytest


@pytest.fixture(autouse=True)
def _keyword_mode_by_default(monkeypatch):
    if "SQUIPI_EMBEDDINGS" not in os.environ:
        monkeypatch.setenv("SQUIPI_EMBEDDINGS", "off")
