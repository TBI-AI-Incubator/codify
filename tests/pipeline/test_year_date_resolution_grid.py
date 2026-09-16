"""Every cell of the year/date resolution grid, asserted through the caller.

The axes: the model's calendar label, whether the calendar declares its month
grid is the Gregorian one, whether the source states a date, whether the model's
date echoes the title's year, either side of a mid-year reform, and whether a
title identity is declared. Titles are fabricated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codify.jurisdictions import try_load_config
from codify.pipeline import stages

MONTHS = [
    "มกราคม",
    "กุมภาพันธ์",
    "มีนาคม",
    "เมษายน",
    "พฤษภาคม",
    "มิถุนายน",
    "กรกฎาคม",
    "สิงหาคม",
    "กันยายน",
    "ตุลาคม",
    "พฤศจิกายน",
    "ธันวาคม",
]
IDENTITY = {
    "strip_prefixes": ["พระราชบัญญัติ"],
    "year_particles": ["พ.ศ."],
    "edition_markers": ["ฉบับที่"],
    "consolidation_markers": ["Update"],
}
#: Pre-reform, so a first-quarter month moves the year; and post-reform, so it
#: does not. Both titles are invented.
PRE = "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2478"
POST = "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511"
#: A first-quarter date, so the reform shift is visible where it applies.
SOURCE_PRE = "ให้ไว้ ณ วันที่ ๓๑ มกราคม พ.ศ. ๒๔๗๘\n"
SOURCE_POST = "ให้ไว้ ณ วันที่ ๓๑ มกราคม พ.ศ. ๒๕๑๑\n"


def _conversion(
    *, gregorian_grid: bool = True, reform: bool = True, epoch_year: int = -543
) -> dict[str, object]:
    rule: dict[str, object] = {
        "kind": "buddhist",
        "epoch_year": epoch_year,
        "month_names": MONTHS,
        "month_day_is_gregorian": gregorian_grid,
        "date_cues": ["ให้ไว้ ณ วันที่"],
        "year_particles": ["พ.ศ."],
    }
    if reform:
        rule |= {"new_year_month": 4, "new_year_reform_year": 2484}
    return rule


#: code -> (title identity declared, Gregorian month grid, reform declared)
JURISDICTIONS = {
    "xg": (True, True, True),
    "xn": (True, True, False),
    "xo": (True, False, True),
    "xs": (False, True, True),
    # A declared epoch the calendar's name does not imply, and no title grammar.
    "xe": (False, True, True),
    # The same three, with no title grammar declared.
    "xg2": (False, True, True),
    "xn2": (False, True, False),
    "xo2": (False, False, True),
}


@pytest.fixture(autouse=True)
def configs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tests.config_fixtures import isolated_configs

    built = {}
    for code, (identity, grid, reform) in JURISDICTIONS.items():
        frbr: dict[str, object] = {
            "country_code": code,
            "uri_patterns": {"act": f"/akn/{code}/act/{{year}}/{{number}}"},
            "calendar_conversion": _conversion(
                gregorian_grid=grid, reform=reform, epoch_year=-200 if code == "xe" else -543
            ),
        }
        if identity:
            frbr["title_identity"] = IDENTITY
        built[code] = {"calendar": "buddhist_era", "languages": ["tha"], "frbr": frbr}
    with isolated_configs(monkeypatch, tmp_path / "jurisdictions", built):
        yield


def _resolve(code: str, metadata: dict[str, object], source: str) -> stages.Descriptors:
    try_load_config.cache_clear()
    return stages.resolve_descriptors(
        {"number": "", **metadata},
        jurisdiction_code=code,
        source_bytes=source.encode(),
        fallback_stem="source",
        classification_text=source,
    )


# (case, code, metadata, source text, expected year, expected date)
GRID: list[tuple[str, str, dict[str, object], str, str, str]] = [
    # --- no date anywhere: the title's year converts, month-blind ------------
    ("title only, pre-reform", "xg", {"title": PRE}, "", "1935", ""),
    ("title only, post-reform", "xg", {"title": POST}, "", "1968", ""),
    ("title only, no reform declared", "xn", {"title": PRE}, "", "1935", ""),
    # No grammar declares the title, so the year converts month-blind.
    ("no identity, no date", "xs", {"title": PRE}, "", "1935", ""),
    # --- source states a date ------------------------------------------------
    ("source date, pre-reform", "xg", {"title": PRE}, SOURCE_PRE, "1936", "1936-01-31"),
    ("source date, post-reform", "xg", {"title": POST}, SOURCE_POST, "1968", "1968-01-31"),
    ("source date, no reform declared", "xn", {"title": PRE}, SOURCE_PRE, "1935", "1935-01-31"),
    ("source date, no identity", "xs", {"title": PRE}, SOURCE_PRE, "1936", "1936-01-31"),
    (
        "source date, model year echoes the title",
        "xg",
        {"title": PRE, "year": "2478", "calendar": "buddhist"},
        SOURCE_PRE,
        "1936",
        "1936-01-31",
    ),
    (
        "source date wins over a metadata date",
        "xg",
        {"title": PRE, "date": "2478-05-20", "calendar": "buddhist"},
        SOURCE_PRE,
        "1936",
        "1936-01-31",
    ),
    # --- the model states a local date, labelled -----------------------------
    (
        "labelled local date, Gregorian grid",
        "xg",
        {"title": PRE, "date": "2478-02-29", "calendar": "buddhist"},
        "",
        "1936",
        "1936-02-29",
    ),
    (
        "labelled local date, post-reform",
        "xg",
        {"title": POST, "date": "2511-09-09", "calendar": "buddhist"},
        "",
        "1968",
        "1968-09-09",
    ),
    (
        "labelled local date, month grid not Gregorian",
        "xo",
        {"title": PRE, "date": "2478-02-20", "calendar": "buddhist"},
        "",
        "1935",
        "",
    ),
    # --- the model states a local date and calls it Gregorian ----------------
    (
        "unlabelled local date echoing the title",
        "xg",
        {"title": POST, "date": "2511-09-09", "calendar": ""},
        "",
        "1968",
        "1968-09-09",
    ),
    (
        "unlabelled local date, month grid not Gregorian",
        "xo",
        {"title": POST, "date": "2511-09-09", "calendar": ""},
        "",
        "1968",
        "",
    ),
    (
        "unlabelled date that does not echo the title",
        "xg",
        {"title": POST, "date": "1968-09-09", "calendar": ""},
        "",
        "1968",
        "1968-09-09",
    ),
    # --- the configured rule, not a generic offset --------------------------
    ("custom epoch, title only", "xe", {"title": PRE}, "", "2278", ""),
    (
        "no identity, labelled local date",
        "xs",
        {"title": PRE, "date": "2478-02-29", "calendar": "buddhist"},
        "",
        "1936",
        "1936-02-29",
    ),
    (
        "unlabelled local date whose day does not exist",
        "xg",
        {"title": POST, "date": "2511-04-31", "calendar": ""},
        "",
        "1968",
        "",
    ),
    (
        "a non-date field does not hide a source date",
        "xg",
        {"title": PRE, "date": "unknown"},
        SOURCE_PRE,
        "1936",
        "1936-01-31",
    ),
    # --- fields that state no year ------------------------------------------
    ("date field naming no year", "xg", {"title": POST, "date": "unknown"}, "", "1968", "unknown"),
    (
        "year the conversion cannot carry",
        "xg",
        {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 999", "year": "999"},
        "",
        "",
        "",
    ),
]


@pytest.mark.parametrize(
    ("case", "code", "metadata", "source", "expected_year", "expected_date"),
    GRID,
    ids=[row[0] for row in GRID],
)
def test_year_and_date_resolution(
    case: str,
    code: str,
    metadata: dict[str, object],
    source: str,
    expected_year: str,
    expected_date: str,
) -> None:
    desc = _resolve(code, metadata, source)
    assert (desc.year, desc.raw_date) == (expected_year, expected_date), case


#: Each jurisdiction of the grid paired with its twin declaring no title grammar,
#: so every cell runs on both sides of that axis.
WITHOUT_IDENTITY = {"xg": "xg2", "xn": "xn2", "xo": "xo2", "xs": "xs", "xe": "xe"}

#: Cells whose local-ness is visible only by echoing the title. With no title
#: grammar there is nothing to echo, the model called the date Gregorian, and
#: taking it at its word is the only reading left.
UNECHOED = {
    "unlabelled local date echoing the title": ("2511", "2511-09-09"),
    "unlabelled local date, month grid not Gregorian": ("2511", "2511-09-09"),
    # Same: nothing marks it local, so it stays as the model wrote it. Whether a
    # model date should be validated at all is a question for every calendar.
    "unlabelled local date whose day does not exist": ("2511", "2511-04-31"),
}


@pytest.mark.parametrize(
    ("case", "code", "metadata", "source", "expected_year", "expected_date"),
    GRID,
    ids=[row[0] for row in GRID],
)
def test_the_same_grid_without_a_title_grammar(
    case: str,
    code: str,
    metadata: dict[str, object],
    source: str,
    expected_year: str,
    expected_date: str,
) -> None:
    """The date resolution is the title grammar's business only where the title
    states the year. Everything a date settles must settle the same either way.
    """
    twin = _resolve(WITHOUT_IDENTITY[code], metadata, source)
    stated = str(metadata.get("date") or "") or source
    if not stated:
        # Nothing but the title carries a year, so the two sides may differ only
        # in that the twin reads it through the generic fallback.
        assert twin.raw_date == ""
        return
    assert (twin.year, twin.raw_date) == UNECHOED.get(case, (expected_year, expected_date)), case
