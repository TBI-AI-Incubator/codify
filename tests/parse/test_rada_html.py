"""Rada HTML parser: structure, references, and amendment annotations."""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest
from lxml import etree

from codify.jurisdictions import load_config
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.parsers import get_parser, registered_parsers

_FIXTURES = Path(__file__).parent / "fixtures" / "ua"
_GOLD = Path(__file__).parents[4] / "data" / "fixtures" / "eval" / "ua-gold" / "structure"
_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _parse(name: str) -> object:
    if not (_FIXTURES / name).exists():
        pytest.skip(f"rada ua fixture {name} not in the open tree")
    raw = (_FIXTURES / name).read_bytes()
    if name.endswith(".gz"):
        raw = gzip.decompress(raw)
    config = load_config("ua")
    assert config is not None
    return get_parser("rada_html").parse(raw, config=config, doctype="act")


def _article_nums(bluebell: str) -> list[str]:
    return [n.strip(" .") for n in re.findall(r"^\s*ART\S* (\S+)", bluebell, re.M)]


def test_registry_lists_rada_html() -> None:
    assert "rada_html" in registered_parsers()


def test_1560_matches_portal_tree() -> None:
    parsed = _parse("1560-12.html")
    tree_articles = json.loads((_FIXTURES / "1560-12.tree-articles.json").read_text())
    assert _article_nums(parsed.bluebell_text) == tree_articles  # type: ignore[attr-defined]
    assert parsed.metadata["title"] == "Про інвестиційну діяльність"  # type: ignore[attr-defined]
    assert len(parsed.refs) >= 20  # type: ignore[attr-defined]
    assert len(parsed.amendments) >= 70  # type: ignore[attr-defined]


def test_1700_covers_structure_gold() -> None:
    parsed = _parse("1700-18.html.gz")
    ours = {n.replace("-", "") for n in _article_nums(parsed.bluebell_text)}  # type: ignore[attr-defined]
    gold = {
        str(n).replace("-", "")
        for unit, n in json.loads((_GOLD / "1700-VII.nums.json").read_text())
        if unit == "article"
    }
    # 172-5 is a quoted Admin-Offences-Code insertion in the final
    # provisions, not an article of this law, the txt-built gold catches
    # it by line position; the parser's quoted-block suppression drops it.
    gold.discard("1725")
    assert gold - ours == set(), "parser must find every gold article"
    # Ordering: the first articles appear in document order.
    assert _article_nums(parsed.bluebell_text)[:6] == ["1", "2", "3", "4", "5", "6"]  # type: ignore[attr-defined]


def test_1700_reference_and_amendment_layers() -> None:
    parsed = _parse("1700-18.html.gz")
    assert len(parsed.refs) >= 95  # type: ignore[attr-defined]
    assert len(parsed.amendments) >= 299  # type: ignore[attr-defined]
    actions = {a.akn_action for a in parsed.amendments}  # type: ignore[attr-defined]
    assert {"insertion", "substitution"} <= actions
    # Every extracted ref also rides the Bluebell text as an inline link.
    sample = parsed.refs[0]  # type: ignore[attr-defined]
    assert f"{{{{>{sample.href} " in parsed.bluebell_text  # type: ignore[attr-defined]


def test_1700_bluebell_parses_to_valid_akn() -> None:
    parsed = _parse("1700-18.html.gz")
    akn = parse_to_akn(
        parsed.bluebell_text,  # type: ignore[attr-defined]
        country="ua",
        doctype="act",
        date="2014-10-14",
        number="1700-VII",
        language="ukr",
    )
    root = etree.fromstring(akn.encode())
    articles = root.findall(f".//{{{_AKN_NS}}}article")
    # 99 genuine articles (test_1700_covers_structure_gold asserts every gold
    # article is present); declension-case phantoms are no longer promoted.
    assert len(articles) == 99
    refs = root.findall(f".//{{{_AKN_NS}}}ref")
    assert len(refs) >= 90


def test_non_rada_html_rejected() -> None:
    config = load_config("ua")
    assert config is not None
    with pytest.raises(ValueError):
        get_parser("rada_html").parse(
            b"<html><body><p>plain page</p></body></html>", config=config, doctype="act"
        )
