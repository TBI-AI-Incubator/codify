"""Numeric citation recognition: what fires, and what deliberately does not."""

from __future__ import annotations

import pytest

from codify.storage.citations import parse_citation


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("5/2010", ("5", 2010)),
        ("Law No. 5 of 2010", ("5", 2010)),
        ("Law 5/2010", ("5", 2010)),
        ("قرار بقانون رقم 9 لسنة 2025", ("9", 2025)),
        # Arabic-Indic digits, which is how the gazette prints them.
        ("قانون رقم ٩ لسنة ٢٠٢٥", ("9", 2025)),
        # Year first is the same citation.
        ("2010/5", ("5", 2010)),
        # Leading zeros are a typing habit, not a different instrument.
        ("Law No. 05 of 2010", ("5", 2010)),
    ],
)
def test_citation_forms_that_resolve(query: str, expected: tuple[str, int]) -> None:
    assert parse_citation(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        # One number: a title search that happens to name a year. Hijacking this
        # into a structural lookup would break ordinary title search.
        "companies law 1929",
        "5",
        # Two year-shaped numbers is a range, not a citation.
        "2010-2015",
        # No digits at all.
        "commercial companies law",
        "",
        # Three numbers: whatever this is, it is not this parser's to guess.
        "Law 5 of 2010 art 12",
    ],
)
def test_queries_that_stay_with_title_search(query: str) -> None:
    assert parse_citation(query) is None
