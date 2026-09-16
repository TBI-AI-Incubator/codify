"""Reading the date a source states its document was made on."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from codify.calendar import (
    _DATE_WINDOW_CHARS,
    compile_local_date_patterns,
    declares_local_date_grammar,
    local_date_from_text,
)
from codify.jurisdictions import CalendarConversion

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


@pytest.fixture(autouse=True)
def date_grammar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One calendar that names its months, one that does not."""
    from tests.config_fixtures import isolated_configs

    named = {
        "kind": "buddhist",
        "epoch_year": -543,
        "month_names": MONTHS,
        "month_day_is_gregorian": True,
        "date_cues": ["ให้ไว้ ณ วันที่", "ตราไว้ ณ วันที่"],
        "year_particles": ["พระพุทธศักราช", "พุทธศักราช", "พ.ศ."],
        "new_year_month": 4,
        "new_year_reform_year": 2484,
    }
    configs = {
        "xn": {
            "calendar": "buddhist_era",
            "frbr": {"country_code": "xn", "calendar_conversion": named},
        },
        # An era-table rule, whose `eras` hold dicts no cache key may carry.
        "xj": {
            "frbr": {
                "country_code": "xj",
                "calendar_conversion": {
                    "kind": "era_table",
                    "eras": [{"name": "Reiwa", "abbrev": "令和", "start": "2019-05-01"}],
                },
            }
        },
        "xm": {
            "calendar": "buddhist_era",
            "frbr": {
                "country_code": "xm",
                "calendar_conversion": {"kind": "buddhist", "epoch_year": -543},
            },
        },
    }
    with isolated_configs(monkeypatch, tmp_path / "jurisdictions", configs):
        yield


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ให้ไว้ ณ วันที่ ๒๖ เมษายน พ.ศ. ๒๕๕๙", date(2016, 4, 26)),
        ("ให้ไว้ ณ วันที่ 26 เมษายน พ.ศ. 2559", date(2016, 4, 26)),
        # A signature block wraps wherever the column ran out.
        ("ให้ไว้ ณ\r\nวันที่ ๒๐\r\nสิงหาคม\r\nพุทธศักราช ๒๔๗๘", date(1935, 8, 20)),
        # A second declared cue, and no year particle before the year.
        ("ตราไว้ ณ วันที่ ๓ ธันวาคม ๒๕๐๐", date(1957, 12, 3)),
    ],
)
def test_reads_a_dated_line_a_cue_introduces(text: str, expected: date) -> None:
    assert local_date_from_text(text, "xn") == expected


def test_a_year_that_began_mid_year_shifts_its_first_months() -> None:
    """Before the reform the local year ran from month 4, so month 1 of local
    year N falls in the Gregorian year after N + epoch."""
    assert local_date_from_text("ให้ไว้ ณ วันที่ ๓๑ มกราคม พุทธศักราช ๒๔๗๘", "xn") == date(1936, 1, 31)
    # Control: the same month after the reform takes the plain offset.
    assert local_date_from_text("ให้ไว้ ณ วันที่ ๓๑ มกราคม พุทธศักราช ๒๕๐๐", "xn") == date(1957, 1, 31)
    # Control: a month after the local new year is unshifted either side.
    assert local_date_from_text("ให้ไว้ ณ วันที่ ๒๐ สิงหาคม พุทธศักราช ๒๔๗๘", "xn") == date(1935, 8, 20)


def test_a_dated_line_no_cue_introduces_is_not_this_documents_date() -> None:
    """The first dated line of an amending document is the one it amends."""
    assert local_date_from_text("แก้ไขเพิ่มเติม ๔ สิงหาคม พ.ศ. ๒๔๘๐ ต่อไปนี้", "xn") is None


def test_a_date_too_far_after_its_cue_belongs_to_the_next_paragraph() -> None:
    assert local_date_from_text("ให้ไว้ ณ วันที่" + "ก" * 300 + " ๒๖ เมษายน พ.ศ. ๒๕๕๙", "xn") is None


def test_an_impossible_day_is_refused_rather_than_clamped() -> None:
    assert local_date_from_text("ให้ไว้ ณ วันที่ ๓๑ เมษายน พ.ศ. ๒๕๕๙", "xn") is None


def test_a_calendar_that_names_no_months_reads_nothing() -> None:
    assert local_date_from_text("ให้ไว้ ณ วันที่ ๒๖ เมษายน พ.ศ. ๒๕๕๙", "xm") is None


def test_an_undeclared_country_reads_nothing() -> None:
    assert local_date_from_text("ให้ไว้ ณ วันที่ ๒๖ เมษายน พ.ศ. ๒๕๕๙", "") is None


_ADVERSARIAL = """
import sys
sys.path.insert(0, {root!r})
from codify.calendar import compile_local_date_patterns
from codify.jurisdictions import CalendarConversion
from tests.test_local_date_from_text import MONTHS
rule = CalendarConversion(
    kind="buddhist",
    month_names=MONTHS,
    month_day_is_gregorian=True,
    date_cues=["A B C"],
    year_particles=["\u0e1e\u0e23\u0e30\u0e1e\u0e38\u0e17\u0e18\u0e28\u0e31\u0e01\u0e23\u0e32\u0e0a",
                    "\u0e1e\u0e38\u0e17\u0e18\u0e28\u0e31\u0e01\u0e23\u0e32\u0e0a"],
)
patterns = compile_local_date_patterns(rule)
patterns.date.search("1 \u0e21\u0e01\u0e23\u0e32\u0e04\u0e21" + " " * 40000 + "x")
patterns.cue.search("A " * 40000)
"""


def test_the_shipped_patterns_are_bounded_on_a_long_non_matching_repeat() -> None:
    """`re` holds the GIL and takes no timeout, so the bound is a child process
    and a catastrophic pattern fails by name rather than hanging the run."""
    script = _ADVERSARIAL.format(root=str(Path(__file__).resolve().parents[1]))
    try:
        subprocess.run([sys.executable, "-c", script], timeout=10, check=True)
    except subprocess.TimeoutExpired:
        pytest.fail("a date pattern did not return within 10s on 40k repeats")


def test_a_calendar_with_its_own_month_grid_reads_nothing() -> None:
    """The year converts; the month and day do not. Composing a Gregorian date
    from a local month would be wrong by months, so the rule refuses."""
    rule = CalendarConversion(kind="hijri_lunar", month_names=MONTHS, date_cues=["ให้ไว้ ณ วันที่"])
    assert compile_local_date_patterns(rule) is None


def test_a_year_straddling_the_window_edge_is_read_whole() -> None:
    """Bounding the match's start, not the string: an `endpos` cutting through
    the year would match its first three digits and date the document to 2016
    minus a millennium."""
    cue = "ให้ไว้ ณ วันที่"
    prefix = f"{cue} ๒๖ เมษายน พ.ศ. "
    filler = "ก" * (_DATE_WINDOW_CHARS - len(prefix) - len(cue) + 12)
    text = f"{cue}{filler} ๒๖ เมษายน พ.ศ. ๒๕๕๙"
    found = local_date_from_text(text, "xn")
    assert found == date(2016, 4, 26)


@pytest.mark.parametrize(
    "broken",
    [
        {"month_names": ["", *MONTHS], "date_cues": ["c"]},
        {"month_names": [MONTHS[0], *MONTHS], "date_cues": ["c"]},
        {"month_names": MONTHS, "date_cues": ["", " "]},
    ],
)
def test_a_grammar_that_would_read_a_wrong_date_is_refused_at_load(
    broken: dict[str, list[str]],
) -> None:
    """A blank month renumbers the calendar and a blank cue matches every
    document at offset zero; both produce a valid, wrong date."""
    with pytest.raises(ValidationError):
        CalendarConversion(kind="buddhist", month_day_is_gregorian=True, **broken)


def test_a_longer_digit_run_is_not_a_year() -> None:
    """`[0-9]{3,4}` would take the first four digits of a longer run."""
    assert local_date_from_text("ให้ไว้ ณ วันที่ ๒๖ เมษายน พ.ศ. ๒๕๕๙๐", "xn") is None


def test_a_cue_introducing_no_date_does_not_end_the_search() -> None:
    """A document may name the cue before the signature block that carries the
    date; stopping at the first occurrence reads no date at all."""
    barren = "ให้ไว้ ณ วันที่" + "ก" * 400
    assert local_date_from_text(f"{barren}\nให้ไว้ ณ วันที่ ๒๖ เมษายน พ.ศ. ๒๕๕๙", "xn") == date(
        2016, 4, 26
    )


@pytest.mark.parametrize(
    "broken",
    [
        {"month_names": MONTHS[:11], "month_day_is_gregorian": True},
        {"month_names": MONTHS, "month_day_is_gregorian": True, "new_year_reform_year": 2484},
    ],
)
def test_a_grammar_whose_declaration_cannot_hold_is_refused(broken: dict[str, object]) -> None:
    """Eleven months renumber the calendar; a reform year with no new-year month
    can never fire, so the field reads as set and does nothing."""
    with pytest.raises(ValidationError):
        CalendarConversion(kind="buddhist", date_cues=["c"], **broken)  # type: ignore[arg-type]


def test_a_calendar_whose_rule_holds_dicts_is_asked_without_crashing() -> None:
    """`eras` hold dicts, which no cache key may carry. Every ingest asks this of
    every jurisdiction, so an era-table config must answer, not raise."""
    assert declares_local_date_grammar("xj") is False
    assert local_date_from_text("令和6年 ให้ไว้ ณ วันที่", "xj") is None


def test_an_impossible_date_does_not_hide_a_valid_later_one() -> None:
    """A syntactic match that is not a date must not end the cue walk."""
    text = "ให้ไว้ ณ วันที่ ๓๑ เมษายน พ.ศ. ๒๕๕๙\nให้ไว้ ณ วันที่ ๒๖ เมษายน พ.ศ. ๒๕๕๙"
    assert local_date_from_text(text, "xn") == date(2016, 4, 26)


def test_a_longer_digit_run_is_not_a_day() -> None:
    """Without a leading boundary "126 เมษายน" reads as the 26th."""
    assert local_date_from_text("ให้ไว้ ณ วันที่ ๑๒๖ เมษายน พ.ศ. ๒๕๕๙", "xn") is None


@pytest.mark.parametrize(
    "broken",
    [
        {"new_year_month": 0, "new_year_reform_year": 2484},
        {"new_year_month": 13, "new_year_reform_year": 2484},
        {"new_year_month": 4, "new_year_reform_year": 0},
        {"new_year_month": 4, "new_year_reform_year": -1},
    ],
)
def test_a_reform_declared_out_of_range_is_refused(broken: dict[str, int]) -> None:
    """A month outside 1-12 and a year below one cannot describe a new year."""
    with pytest.raises(ValidationError):
        CalendarConversion(
            kind="buddhist",
            month_names=MONTHS,
            month_day_is_gregorian=True,
            date_cues=["c"],
            **broken,
        )


_MANY_CUES = """
import sys
sys.path.insert(0, {root!r})
from codify.calendar import _first_stated_date, compile_local_date_patterns
from codify.jurisdictions import CalendarConversion
from tests.test_local_date_from_text import MONTHS
rule = CalendarConversion(
    kind="buddhist", month_names=MONTHS, month_day_is_gregorian=True,
    date_cues=["A B C"], year_particles=["P"],
)
patterns = compile_local_date_patterns(rule)
assert _first_stated_date("A B C " * 20000, patterns, rule, "xn") is None
"""


def test_a_document_full_of_cues_and_no_date_is_read_once() -> None:
    """A fresh search per cue re-reads the rest of the document, so 20k cue
    phrases and no date took time in the square of the document length."""
    script = _MANY_CUES.format(root=str(Path(__file__).resolve().parents[1]))
    try:
        subprocess.run([sys.executable, "-c", script], timeout=15, check=True)
    except subprocess.TimeoutExpired:
        pytest.fail("the cue walk did not finish within 15s on 20k date-free cues")
