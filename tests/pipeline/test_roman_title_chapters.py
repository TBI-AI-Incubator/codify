"""Chapters headed by a bare Roman numeral and an all-caps title."""

from __future__ import annotations

import pytest

from codify.jurisdictions import HierarchyEntry, JurisdictionConfig
from codify.pipeline.enrich.anchors import (
    StructuralAnchor,
    anchor_coverage,
    build_anchor_regex,
    scan_anchors,
)

ACT = """ACT ON SHARED ORCHARDS

CONTENTS
I. GENERAL PROVISIONS .......... 1
II. ORCHARD COUNCILS ........... 2

I. GENERAL PROVISIONS

Article 1
This Act governs shared orchards. An applicant must:
I. Register the orchard before the harvest begins, and
II. Pay the annual fee to the council

Article 2
The rules of this chapter apply to every orchard listed in the register:
THE COUNCILS OF SECTION II. ORCHARD COUNCILS SHALL ADVISE

II. ORCHARD COUNCILS AND THEIR
DUTIES OF COORDINATION

Article 3
Each district shall have an orchard council.

V. MISPLACED HEADING

Article 4
The heading of the old schedule is replaced by:
“
III. REPLACED HEADING
”

III. FINAL PROVISIONS

Article 5
This Act enters into force on the day after publication.
"""


def _config(*, opt_in: bool) -> JurisdictionConfig:
    return JurisdictionConfig(
        code="xe",
        name="Synthetic",
        tradition=["civil_law"],
        languages=["eng"],
        authoritative_language="eng",
        default_document_class="act",
        document_classes={
            "act": {
                "label": "Act",
                "akn_element": "act",
                "basic_unit": "article",
                "hierarchy": [
                    HierarchyEntry(
                        local_term="Chapter",
                        akn_element="chapter",
                        level="higher",
                        bluebell_keyword="CHAPTER",
                        numbering="roman",
                        marker_form="upper_roman_title" if opt_in else None,
                    ),
                    HierarchyEntry(
                        local_term="Article",
                        akn_element="article",
                        level="basic",
                        bluebell_keyword="ARTICLE",
                        numbering="arabic_continuous",
                    ),
                ],
            }
        },
    )


def _scan(monkeypatch: pytest.MonkeyPatch, *, opt_in: bool) -> list[StructuralAnchor]:
    cfg = _config(opt_in=opt_in)
    monkeypatch.setattr("codify.pipeline.enrich.anchors.load_config", lambda _c: cfg)
    return scan_anchors(ACT, build_anchor_regex(cfg, "act"), country="xe", doctype="act")


def test_bare_roman_headings_become_chapters(monkeypatch: pytest.MonkeyPatch) -> None:
    chapters = [a for a in _scan(monkeypatch, opt_in=True) if a.kind == "chapter"]
    assert [(a.akn_eid, a.heading) for a in chapters] == [
        ("chp_I", "GENERAL PROVISIONS"),
        ("chp_II", "ORCHARD COUNCILS AND THEIR DUTIES OF COORDINATION"),
        ("chp_III", "FINAL PROVISIONS"),
    ]


def test_articles_nest_under_their_chapter(monkeypatch: pytest.MonkeyPatch) -> None:
    articles = [a.akn_eid for a in _scan(monkeypatch, opt_in=True) if a.kind == "article"]
    assert articles == [
        "chp_I__art_1",
        "chp_I__art_2",
        "chp_II__art_3",
        "chp_II__art_4",
        "chp_III__art_5",
    ]


def test_a_hyphen_wrapped_title_is_joined(monkeypatch: pytest.MonkeyPatch) -> None:
    text = ACT.replace("AND THEIR\nDUTIES", "AND THEIR DU-\nTIES")
    cfg = _config(opt_in=True)
    monkeypatch.setattr("codify.pipeline.enrich.anchors.load_config", lambda _c: cfg)
    anchors = scan_anchors(text, build_anchor_regex(cfg, "act"), country="xe", doctype="act")
    headings = [a.heading for a in anchors if a.kind == "chapter"]
    assert headings[1] == "ORCHARD COUNCILS AND THEIR DUTIES OF COORDINATION"


def test_a_single_heading_is_not_a_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "I. GENERAL PROVISIONS\n\nArticle 1\nText.\n\nArticle 2\nText.\n"
    cfg = _config(opt_in=True)
    monkeypatch.setattr("codify.pipeline.enrich.anchors.load_config", lambda _c: cfg)
    anchors = scan_anchors(text, build_anchor_regex(cfg, "act"), country="xe", doctype="act")
    assert [a.kind for a in anchors] == ["article", "article"]


def test_chapter_coverage_counts_the_same_headings(monkeypatch: pytest.MonkeyPatch) -> None:
    anchors = _scan(monkeypatch, opt_in=True)
    coverage = anchor_coverage(ACT, anchors, _config(opt_in=True), "act", "chapter")
    assert coverage.ratio == 1.0
    assert coverage.expected == frozenset({"I", "II", "III"})


def test_a_level_without_the_form_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = _scan(monkeypatch, opt_in=False)
    assert [a.kind for a in plain] == ["article"] * 5
    assert [a.akn_eid for a in plain] == [f"art_{n}" for n in range(1, 6)]
