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


def _roman(n: int) -> str:
    out = ""
    for value, glyph in ((90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX")):
        while n >= value:
            out, n = out + glyph, n - value
    return out + ("", "I", "II", "III", "IV", "V", "VI", "VII", "VIII")[n]


def _anchors(monkeypatch: pytest.MonkeyPatch, text: str) -> list[StructuralAnchor]:
    cfg = _config(opt_in=True)
    monkeypatch.setattr("codify.pipeline.enrich.anchors.load_config", lambda _c: cfg)
    return scan_anchors(text, build_anchor_regex(cfg, "act"), country="xe", doctype="act")


def test_numerals_past_fifty_continue_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "".join(f"{_roman(n)}. HEADING NUMBER\n\nArticle {n}\nText.\n\n" for n in range(1, 92))
    chapters = [a.number for a in _anchors(monkeypatch, text) if a.kind == "chapter"]
    assert chapters == [_roman(n) for n in range(1, 92)]


@pytest.mark.parametrize(
    "contents",
    [
        "I. GENERAL PROVISIONS\nII. ORCHARD COUNCILS\n",
        "I. GENERAL PROVISIONS\n3\nII. ORCHARD COUNCILS\n7\n",
    ],
)
def test_a_contents_list_does_not_take_the_body_run(
    monkeypatch: pytest.MonkeyPatch, contents: str
) -> None:
    body = "I. GENERAL PROVISIONS\n\nArticle 1\nText.\n\nII. ORCHARD COUNCILS\n\nArticle 2\nText.\n"
    text = f"CONTENTS\n{contents}\n{body}"
    anchors = _anchors(monkeypatch, text)
    start = text.index(body)
    assert [a.char_offset >= start for a in anchors if a.kind == "chapter"] == [True, True]
    assert [a.akn_eid for a in anchors if a.kind == "article"] == [
        "chp_I__art_1",
        "chp_II__art_2",
    ]


@pytest.mark.parametrize(
    "heading",
    [
        "II. ORCHARD COUNCILS AND THEIR\nDUTIES OF COORDINATION",
        "II. ORCHARD COUNCILS AND THEIR DU-\nTIES OF COORDINATION",
    ],
)
def test_assembly_prints_a_wrapped_title_once(
    monkeypatch: pytest.MonkeyPatch, heading: str
) -> None:
    from codify.pipeline.enrich.verbatim import text_to_bluebell_verbatim

    cfg = _config(opt_in=True)
    monkeypatch.setattr("codify.pipeline.enrich.anchors.load_config", lambda _c: cfg)
    monkeypatch.setattr(
        "codify.pipeline.enrich.verbatim.cached_regex",
        lambda _c, _d: build_anchor_regex(cfg, "act"),
    )
    text = (
        "I. GENERAL PROVISIONS\n\nArticle 1\nOrchards are shared.\n\n"
        f"{heading}\n\nArticle 2\nEach district has a council.\n"
    )
    bluebell, _summary, _anchors = text_to_bluebell_verbatim(text, country="xe", doctype="act")
    assert bluebell.count("COORDINATION") == 1
    assert "TIES OF" not in bluebell.replace("DUTIES OF", "")
    assert "ORCHARD COUNCILS AND THEIR DUTIES OF COORDINATION" in bluebell


def test_an_appended_restatement_does_not_replace_the_body_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "I. GENERAL PROVISIONS\n\nArticle 1\nText.\n\nII. ORCHARD COUNCILS\n\nArticle 2\nText.\n"
    restated = "I. PROVISIONES GENERALES\n\nItem one.\n\nII. CONSILIA\n\nItem two.\n"
    anchors = _anchors(monkeypatch, f"{body}\n{restated}")
    chapters = [a for a in anchors if a.kind == "chapter"]
    assert [a.char_offset < len(body) for a in chapters] == [True, True]


def test_an_article_caption_does_not_replace_the_title(monkeypatch: pytest.MonkeyPatch) -> None:
    text = (
        "I. GENERAL PROVISIONS\n\nSubject matter\nArticle 1\nText.\n\n"
        "II. ORCHARD COUNCILS\n\nArticle 2\nText.\n"
    )
    headings = [a.heading for a in _anchors(monkeypatch, text) if a.kind == "chapter"]
    assert headings == ["GENERAL PROVISIONS", "ORCHARD COUNCILS"]


def test_a_stray_numeral_does_not_capture_the_next_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (
        "I. GENERAL PROVISIONS\n\nArticle 1\nText.\n\nV. STRAY HEADING\n\nArticle 2\nText.\n\n"
        "II. ORCHARD COUNCILS\n\nArticle 3\nText.\n"
    )
    chapters = [a.number for a in _anchors(monkeypatch, text) if a.kind == "chapter"]
    assert chapters == ["I", "II"]


def test_an_all_caps_sentence_is_not_a_heading(monkeypatch: pytest.MonkeyPatch) -> None:
    text = (
        "I. GENERAL PROVISIONS\n\nArticle 1\nFees apply as follows:\n"
        "II. NO FEE IS PAYABLE BEFORE 2030.\n\n"
        "II. ORCHARD COUNCILS\n\nArticle 2\nText.\n"
    )
    headings = [a.heading for a in _anchors(monkeypatch, text) if a.kind == "chapter"]
    assert headings == ["GENERAL PROVISIONS", "ORCHARD COUNCILS"]
