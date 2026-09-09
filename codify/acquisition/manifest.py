"""Per-jurisdiction corpus manifest loader."""

from __future__ import annotations

import json

from codify.acquisition.base import CorpusManifest
from codify.jurisdictions import JURISDICTIONS_DIR


def load_manifest(jurisdiction_code: str) -> CorpusManifest | None:
    """Read `data/jurisdictions/{code}/corpus.json`. None if absent."""
    if "/" in jurisdiction_code or ".." in jurisdiction_code:
        return None
    path = JURISDICTIONS_DIR / jurisdiction_code / "corpus.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    raw.setdefault("jurisdiction_code", jurisdiction_code)
    return CorpusManifest.model_validate(raw)


__all__ = ["load_manifest"]
