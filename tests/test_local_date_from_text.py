"""Reading the date a source states its document was made on."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import codify.calendar as calendar_module
from codify.calendar import local_date_from_text

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
        "xm": {
            "calendar": "buddhist_era",
            "frbr": {
                "country_code": "xm",
                "calendar_conversion": {"kind": "buddhist", "epoch_year": -543},
            },
        },
    }
    with isolated_configs(monkeypatch, tmp_path / "jurisdictions", configs):
        calendar_module._rule_and_patterns.cache_clear()
        yield
    calendar_module._rule_and_patterns.cache_clear()


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
    date_cues=["A B C"],
    year_particles=["\u0e1e\u0e23\u0e30\u0e1e\u0e38\u0e17\u0e18\u0e28\u0e31\u0e01\u0e23\u0e32\u0e0a",
                    "\u0e1e\u0e38\u0e17\u0e18\u0e28\u0e31\u0e01\u0e23\u0e32\u0e0a"],
)
patterns = compile_local_date_patterns(rule)
patterns.date.search("1 " * 40000)
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
