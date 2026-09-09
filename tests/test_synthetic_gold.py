"""Every gold target still names an article in the corpus it points at.

The eval drops an unresolvable one, so rot leaves a green run measuring less.
"""

from __future__ import annotations

import json

import pytest
from lxml import etree

from codify.eval.synthetic_retrieval import GOLD, _manifests

_NS = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
_QUERIES = json.loads(GOLD.read_text(encoding="utf-8"))["queries"]


def _eids_by_work() -> dict[str, set[str]]:
    from codify.pipeline.enrich.bluebell import parse_to_akn

    out: dict[str, set[str]] = {}
    for directory, manifest in _manifests():
        for law in manifest["laws"]:
            akn = parse_to_akn(
                (directory / law["file"]).read_text(encoding="utf-8"),
                manifest["jurisdiction"],
                doctype=law.get("doctype", "act"),
                date=law["date"][:4],
                number=law["number"],
                language=law["language"],
            )
            root = etree.fromstring(akn.encode())
            uri = root.find(".//a:FRBRWork/a:FRBRthis", _NS)
            assert uri is not None
            out[str(uri.get("value"))] = {str(e.get("eId")) for e in root.iter() if e.get("eId")}
    return out


_EIDS = _eids_by_work()


@pytest.mark.parametrize("query", _QUERIES, ids=lambda q: q["id"])
def test_every_gold_target_exists(query: dict) -> None:
    eids = _EIDS.get(query["work_uri"])
    assert eids is not None, f"{query['work_uri']} is not in any shipped manifest"
    missing = [e for e in query["relevant"] if e not in eids]
    assert not missing, f"{query['work_uri']} has no {missing}"


def test_the_set_is_big_enough_to_be_worth_running() -> None:
    """A floor, not a benchmark: small enough to say so, large enough that one
    language regressing is visible."""
    assert len(_QUERIES) >= 39
    languages = {q["language"] for q in _QUERIES}
    assert len(languages) >= 6, languages
