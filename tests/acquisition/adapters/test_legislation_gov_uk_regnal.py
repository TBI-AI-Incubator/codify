"""A calendar-year citation finds its regnal index entry."""

from __future__ import annotations

import pytest

from codify.acquisition.adapters.legislation_gov_uk import (
    entry_from_path,
    paths_for_work,
    regnal_calendar_years,
)

# Index entries as the 2026-09-06 build carries them around the cited items the
# join could not place: the same token and number in neighbouring sessions.
_INDEX = [
    entry_from_path("ukla", 1898, "ukla/Vict/61-62/36"),
    entry_from_path("ukla", 1899, "ukla/Vict/62-63/36"),
    entry_from_path("ukla", 1901, "ukla/Edw7/1/36"),
    entry_from_path("ukla", 1905, "ukla/Edw7/5/15"),
    entry_from_path("ukla", 1906, "ukla/Edw7/6/15"),
    entry_from_path("ukla", 1908, "ukla/Edw7/8/15"),
    entry_from_path("ukla", 1905, "ukla/Edw7/5/30"),
    entry_from_path("ukla", 1906, "ukla/Edw7/6/30"),
    entry_from_path("ukla", 1908, "ukla/Edw7/8/30"),
    entry_from_path("ukla", 1908, "ukla/Edw7/8/37"),
    entry_from_path("ukla", 1910, "ukla/Edw7and1Geo5/10/37"),
    entry_from_path("ukla", 1911, "ukla/Geo5/1-2/37"),
    entry_from_path("ukla", 1918, "ukla/Geo5/8-9/31"),
    entry_from_path("ukla", 1919, "ukla/Geo5/9-10/31"),
    entry_from_path("ukla", 1922, "ukla/Geo5/12-13/31"),
    entry_from_path("ukpga", 1891, "ukpga/Vict/54-55/71"),
    entry_from_path("ukpga", 1894, "ukpga/Vict/56-57/71"),
    entry_from_path("ukpga", 1922, "ukpga/Geo5/12-13/58"),
    entry_from_path("ukpga", 1925, "ukpga/Geo5/15-16/58"),
    entry_from_path("ukpga", 1926, "ukpga/Geo5/16-17/58"),
    entry_from_path("uksi", 2003, "uksi/2003/1"),
    entry_from_path("uksi", 1967, "uksi/1967/25"),
    entry_from_path("uksi", 1970, "uksi/1970/25"),
]


@pytest.mark.parametrize(
    ("regnal", "years"),
    [
        ("Vict/62-63", {1898, 1899, 1900}),
        ("Edw7/6", {1906, 1907}),
        ("Geo5/9-10", {1918, 1919, 1920}),
        ("Edw7and1Geo5/10", {1910, 1911}),
        ("Geo3Sess2/47", {1806, 1807}),
        ("Cha2/12", {1660, 1661}),
        ("Hen3c23/52", set()),
        ("WillandMar/2", {1690, 1691}),
        ("WillandMarSess2/1", {1689, 1690}),
        ("Geo3St2/47", {1806, 1807}),
        ("Will4and1Vict/7", {1836, 1837, 1838}),
    ],
)
def test_a_regnal_session_covers_the_calendar_years_of_its_regnal_years(
    regnal: str, years: set[int]
) -> None:
    assert regnal_calendar_years(regnal) == years


@pytest.mark.parametrize(
    ("token", "year", "number", "expected"),
    [
        ("ukla", 1900, "36", ["ukla/Vict/62-63/36"]),
        ("ukla", 1907, "15", ["ukla/Edw7/6/15"]),
        ("ukla", 1907, "30", ["ukla/Edw7/6/30"]),
        ("ukla", 1909, "37", ["ukla/Edw7/8/37"]),
        ("ukla", 1920, "31", ["ukla/Geo5/9-10/31"]),
        ("ukpga", 1893, "71", ["ukpga/Vict/56-57/71"]),
        ("ukpga", 1924, "58", ["ukpga/Geo5/15-16/58"]),
    ],
)
def test_the_cited_calendar_year_finds_its_regnal_entry(
    token: str, year: int, number: str, expected: list[str]
) -> None:
    assert paths_for_work(_INDEX, token, year, number) == expected


def test_a_year_two_sessions_cover_names_both_for_the_caller_to_refuse() -> None:
    """Neither entry is listed under the cited year, and both sessions span it."""
    index = [
        *_INDEX,
        entry_from_path("ukla", 1918, "ukla/Geo5/8-9/99"),
        entry_from_path("ukla", 1920, "ukla/Geo5/10-11/99"),
    ]
    assert paths_for_work(index, "ukla", 1919, "99") == [
        "ukla/Geo5/10-11/99",
        "ukla/Geo5/8-9/99",
    ]


def test_the_publisher_facet_year_wins_over_a_neighbouring_session() -> None:
    index = [*_INDEX, entry_from_path("ukla", 1900, "ukla/Vict/63-64/36")]
    assert paths_for_work(index, "ukla", 1900, "36") == ["ukla/Vict/63-64/36"]


def test_a_modern_path_is_matched_exactly_and_never_by_regnal_span() -> None:
    assert paths_for_work(_INDEX, "uksi", 2003, "1") == ["uksi/2003/1"]
    # The neighbouring years' numbers are not this item.
    assert paths_for_work(_INDEX, "uksi", 1969, "25") == []


def test_an_item_the_index_lacks_is_empty_not_guessed() -> None:
    assert paths_for_work(_INDEX, "apgb", 1783, "28") == []
    assert paths_for_work(_INDEX, "ukpga", 1840, "897") == []
