"""resolve_year and gregorian_year extract year from metadata or fallback title."""

from codify.pipeline.enrich.metadata import gregorian_year
from codify.pipeline.stages import resolve_year


def test_resolve_year_extracts_from_title_when_no_date_or_year() -> None:
    meta: dict[str, str] = {}
    assert resolve_year(meta, "xe", title="2023-Tariff-Schedule") == "2023"


def test_gregorian_year_extracts_from_title_when_no_date_or_year() -> None:
    meta = {"title": "2023-Tariff-Schedule"}
    assert gregorian_year(meta, "xe") == 2023


def test_gregorian_year_accepts_the_resolved_fallback_title() -> None:
    assert gregorian_year({}, "xe", title="2023-Tariff-Schedule") == 2023


def test_title_year_is_always_gregorian_even_when_calendar_is_local() -> None:
    metadata = {"calendar": "buddhist_era"}
    assert gregorian_year(metadata, "", title="2023-Tariff-Schedule") == 2023


def test_title_token_that_is_the_document_number_is_not_a_year() -> None:
    meta = {"title": "Act No. 2010", "number": "2010"}
    assert resolve_year(meta, "xe", title="Act No. 2010") == ""
    assert gregorian_year(meta, "xe") is None


def test_title_year_survives_a_different_document_number() -> None:
    meta = {"title": "Finance Act 2010", "number": "12"}
    assert resolve_year(meta, "xe", title="Finance Act 2010") == "2010"
    assert gregorian_year(meta, "xe") == 2010


def test_resolve_year_prefers_explicit_year() -> None:
    meta = {"year": "2021"}
    assert resolve_year(meta, "xe", title="2023-Tariff-Schedule") == "2021"
