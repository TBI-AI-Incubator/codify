# ruff: noqa: E501  # AKN XML fixtures: line wraps would change tested whitespace
"""Tests for the per-version structural quality grade."""

from __future__ import annotations

from codify.akn import AKN_NS
from codify.pipeline.enrich.validator import validate_akn
from codify.quality.structural_quality_grade import structural_quality_grade


def _act(body: str) -> str:
    return f'''<?xml version="1.0"?>
<akomaNtoso xmlns="{AKN_NS}">
  <act>
    <meta><identification source="#codify"/><references source="#codify"/></meta>
    <body>{body}</body>
  </act>
</akomaNtoso>
'''


def test_error_finding_grades_blocking():
    result = structural_quality_grade(
        [
            {
                "check": "orphan_articles",
                "severity": "error",
                "message": "1 article outside a container",
            }
        ]
    )
    assert result.grade == "blocking"
    assert "orphan_articles" in result.reason


def test_warning_only_grades_warning():
    result = structural_quality_grade(
        [{"check": "ocr_garble", "severity": "warning", "message": "x"}]
    )
    assert result.grade == "warning"
    assert "ocr_garble" in result.reason


def test_error_wins_over_warning():
    result = structural_quality_grade(
        [
            {"check": "ocr_garble", "severity": "warning", "message": "x"},
            {"check": "orphan_articles", "severity": "error", "message": "y"},
        ]
    )
    assert result.grade == "blocking"
    assert "orphan_articles" in result.reason  # the error, not the earlier warning


def test_info_and_empty_grade_clean():
    assert structural_quality_grade([]).grade == "clean"
    # info-severity findings are advisory and never move the grade off clean
    assert (
        structural_quality_grade(
            [{"check": "section_numbering", "severity": "info", "message": "gap"}]
        ).grade
        == "clean"
    )


def test_degraded_grades_ungraded():
    assert structural_quality_grade([], degraded=True).grade == "ungraded"
    # a crash mid-validation is never read as blocking/clean, absence of findings isn't cleanliness
    assert structural_quality_grade([{"severity": "error"}], degraded=True).grade == "ungraded"


def test_rubric_version_rides_on_result():
    assert structural_quality_grade([]).rubric_version == 1


def test_orphan_article_akn_grades_blocking_end_to_end():
    # A real orphan-article AKN, run through validate_akn,
    # grades blocking. An article sits at <body> alongside a <chapter>.
    xml = _act(
        """
    <chapter eId="chp_1"><num>1</num>
      <article eId="chp_1__art_1"><num>1</num></article>
    </chapter>
    <article eId="art_2"><num>2</num></article>
    """
    )
    findings = validate_akn(xml)
    assert any(f["check"] == "orphan_articles" and f["severity"] == "error" for f in findings)
    assert structural_quality_grade(findings).grade == "blocking"
