"""Deterministic scaffold helpers, provision iteration, eId index, summary."""

from __future__ import annotations

from datetime import date

from codify.akn.document import Document
from codify.akn.elements import Article, Chapter, Paragraph, Section
from codify.compare.scaffold import (
    ASSESSABLE_KINDS,
    article_label,
    classify_alignment_level,
    effective_headings,
    iter_assessable,
    provision_text,
    summarise,
)
from codify.compare.types import Citation, ProvisionAlignment


def test_iter_assessable_filters_to_articles_and_sections(directive_doc: Document) -> None:
    eids = [p.akn_eid for p in iter_assessable(directive_doc)]
    assert eids == ["art_1", "art_2"]
    assert "chp_1" not in eids


def test_iter_assessable_kinds_match_constant() -> None:
    assert "article" in ASSESSABLE_KINDS
    assert "chapter" not in ASSESSABLE_KINDS


def test_provision_text_concatenates_intro_text_wrapup(directive_doc: Document) -> None:
    art = next(iter_assessable(directive_doc))
    assert "trade secrets" in provision_text(art)


def _alignment(eid: str, verdict: str, *, needs_review: bool = False) -> ProvisionAlignment:
    return ProvisionAlignment(
        directive_eid=eid,
        directive_heading=None,
        verdict=verdict,  # type: ignore[arg-type]
        confidence=0.9,
        note="",
        citations=[Citation(frbr_uri="x", akn_eid="y", quote="z")] if verdict != "gap" else [],
        needs_review=needs_review,
    )


def test_summarise_counts_and_percentages() -> None:
    results = [
        _alignment("a", "aligned"),
        _alignment("b", "aligned"),
        _alignment("c", "partial", needs_review=True),
        _alignment("d", "gap"),
    ]
    s = summarise(results)
    assert (s.aligned, s.partial, s.gap, s.total) == (2, 1, 1, 4)
    assert s.aligned_pct == 50.0
    assert s.needs_review == 1


def test_summarise_empty_results_gives_zero_pct() -> None:
    s = summarise([])
    assert s.total == 0
    assert s.aligned_pct == 0.0


def test_effective_headings_inherits_article_not_section() -> None:
    # Paragraph under a headed article inherits the article heading; a bare
    # article under a generic "Section 1" gets nothing (no structural label).
    doc = Document(
        frbr_work_uri="/akn/eu/act/dir/2017/1132",
        frbr_expression_uri="/akn/eu/act/dir/2017/1132/eng@2017-06-14",
        language="eng",
        expression_date=date(2017, 6, 14),
        body=[
            Chapter(
                akn_eid="chp_1",
                akn_type="chapter",
                position=0,
                children=[
                    Section(
                        akn_eid="sec_1",
                        akn_type="section",
                        position=0,
                        heading="Section 1",
                        children=[
                            Article(
                                akn_eid="art_5",
                                akn_type="article",
                                position=0,
                                heading="Disclosure",
                                children=[
                                    Paragraph(
                                        akn_eid="art_5__para_1",
                                        akn_type="paragraph",
                                        position=0,
                                        text="x",
                                    )
                                ],
                            ),
                            Article(
                                akn_eid="art_6",
                                akn_type="article",
                                position=1,
                                children=[
                                    Paragraph(
                                        akn_eid="art_6__para_1",
                                        akn_type="paragraph",
                                        position=0,
                                        text="y",
                                    )
                                ],
                            ),
                        ],
                    )
                ],
            )
        ],
    )
    h = effective_headings(doc)
    assert h["art_5__para_1"] == "Disclosure"  # inherits its article
    assert "art_6__para_1" not in h  # bare article → no "Section 1" fallthrough
    assert h["sec_1"] == "Section 1"  # section keeps its own label


def test_article_label_from_eid() -> None:
    assert article_label("tit_1__chp_2__sec_2__art_9__para_1") == "Article 9"
    assert article_label("art_51b__para_3") == "Article 51b"
    assert article_label("chp_1") is None


def test_classify_alignment_level_thresholds() -> None:
    # Below the assessable floor (default 5) => inconclusive, never fully. The
    # scale is a verdict only; "not_assessed" is not one of its values.
    assert classify_alignment_level(0, 0, 0) == "inconclusive"
    assert classify_alignment_level(4, 0, 0) == "inconclusive"
    assert classify_alignment_level(3, 1, 0) == "inconclusive"
    assert classify_alignment_level(2, 1, 1) == "inconclusive"
    # At or above the floor, the EC scale applies.
    assert classify_alignment_level(5, 0, 0) == "fully_aligned"
    assert classify_alignment_level(9, 0, 1) == "largely_aligned"
    assert classify_alignment_level(5, 2, 3) == "partially_aligned"
    assert classify_alignment_level(1, 1, 8) == "not_aligned"
    # It never claims a subject was unassessed; that is the assessment count.
    for a, p, g in [(0, 0, 0), (4, 0, 0), (100, 0, 0), (0, 0, 100)]:
        assert classify_alignment_level(a, p, g) != "not_assessed"  # type: ignore[comparison-overlap]


def test_classify_alignment_level_share_boundaries() -> None:
    # The server owns these thresholds; pin both exactly so a change trips here.
    # 0.85: at-or-above is largely_aligned, just below is partially_aligned.
    assert classify_alignment_level(17, 0, 3) == "largely_aligned"  # 17/20 = 0.85
    assert classify_alignment_level(16, 0, 4) == "partially_aligned"  # 16/20 = 0.80
    # 0.50: at-or-above is partially_aligned, just below is not_aligned.
    assert classify_alignment_level(5, 0, 5) == "partially_aligned"  # 5/10 = 0.50
    assert classify_alignment_level(4, 0, 6) == "not_aligned"  # 4/10 = 0.40
