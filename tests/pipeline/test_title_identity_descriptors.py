"""A jurisdiction that numbers nothing gets its identity and date off the source."""

from __future__ import annotations

from pathlib import Path

import pytest

from codify.frbr import build_frbr_work_uri, is_citable_work_uri
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
TITLE = "พระราชบัญญัติเครื่องหมายการค้า (ฉบับที่ ๓) พ.ศ. ๒๕๕๙"
SOURCE_TEXT = f"{TITLE}\nให้ไว้ ณ วันที่ ๒๖ เมษายน พ.ศ. ๒๕๕๙\n"
SOURCE = SOURCE_TEXT.encode()


@pytest.fixture
def configs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tests.config_fixtures import isolated_configs

    conversion = {
        "kind": "buddhist",
        "epoch_year": -543,
        "month_names": MONTHS,
        "month_day_is_gregorian": True,
        "date_cues": ["ให้ไว้ ณ วันที่"],
        "year_particles": ["พ.ศ."],
    }
    identity = {
        "strip_prefixes": ["พระราชบัญญัติ"],
        "year_particles": ["พ.ศ."],
        "edition_markers": ["ฉบับที่"],
        "consolidation_markers": ["Update"],
    }

    def build(*, with_identity: bool) -> dict[str, object]:
        frbr: dict[str, object] = {
            "country_code": "xn",
            "uri_patterns": {"act": "/akn/xn/act/{year}/{number}"},
            "calendar_conversion": conversion,
        }
        if with_identity:
            frbr["title_identity"] = identity
        return {"calendar": "buddhist_era", "languages": ["tha"], "frbr": frbr}

    with isolated_configs(
        monkeypatch,
        tmp_path / "jurisdictions",
        {"xn": build(with_identity=True), "xm": build(with_identity=False)},
    ):
        yield


def _descriptors(code: str) -> stages.Descriptors:
    try_load_config.cache_clear()
    return stages.resolve_descriptors(
        {
            "title": TITLE,
            "number": "",
            "year": "๒๕๕๙",
            "date": "",
            "calendar": "buddhist",
            "is_amendment": True,
        },
        jurisdiction_code=code,
        source_bytes=SOURCE,
        fallback_stem="source",
        classification_text=SOURCE_TEXT,
    )


def test_a_declared_title_grammar_mints_a_citable_work_uri(configs: None) -> None:
    desc = _descriptors("xn")
    uri = build_frbr_work_uri("xn", desc.doctype, desc.year, desc.number)
    assert uri == "/akn/xn/act/2016/เครื่องหมายการค้า-ฉบับที่-3"
    assert is_citable_work_uri(uri)


def test_without_one_the_identity_is_the_content_address(configs: None) -> None:
    """Control: the config declaration is what replaces the hash, not the title."""
    desc = _descriptors("xm")
    assert desc.number == stages.draft_number(SOURCE)
    assert not is_citable_work_uri(build_frbr_work_uri("xm", desc.doctype, desc.year, desc.number))


def test_the_source_supplies_the_day_the_model_did_not(configs: None) -> None:
    assert _descriptors("xn").raw_date == "2016-04-26"


def test_a_route_that_extracted_no_text_reads_no_date(configs: None) -> None:
    """Control: on the scanned route `source_bytes` is the file itself, whose
    decoded bytes carry markup rather than the document's own dated line."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": TITLE, "number": "", "year": "๒๕๕๙", "date": "", "calendar": "buddhist"},
        jurisdiction_code="xn",
        source_bytes=SOURCE,
        fallback_stem="source",
    )
    assert desc.raw_date == ""


def test_a_numbered_instrument_keeps_its_number(configs: None) -> None:
    """The title grammar is a fallback, not a replacement for a stated number."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": TITLE, "number": "17", "year": "๒๕๕๙", "calendar": "buddhist"},
        jurisdiction_code="xn",
        source_bytes=SOURCE,
        fallback_stem="source",
        classification_text=SOURCE_TEXT,
    )
    assert desc.number == "17"


def test_a_native_digit_year_is_not_a_resolved_year(configs: None) -> None:
    """A local year in its own script is truthy and would reach the URI
    unconverted, so the title's year must still be consulted."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": TITLE, "number": "", "year": "๒๕๕๙", "date": ""},
        jurisdiction_code="xn",
        source_bytes=SOURCE,
        fallback_stem="source",
        classification_text=SOURCE_TEXT,
    )
    assert desc.year == "2016"


def test_a_stated_date_supplies_a_year_the_metadata_omitted(configs: None) -> None:
    """Without this the URI takes the unknown-year placeholder while the
    document carries its own date."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "Untitled", "number": "", "year": "", "date": ""},
        jurisdiction_code="xn",
        source_bytes=SOURCE,
        fallback_stem="source",
        classification_text=SOURCE_TEXT,
    )
    assert desc.raw_date == "2016-04-26"
    assert desc.year == "2016"


def test_a_title_only_year_is_local_whatever_its_digits(configs: None) -> None:
    """A year the model never stated came from the title, so it is in the local
    calendar even when it happens to read as four ASCII digits."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "พระราชบัญญัติเครื่องหมายการค้า พ.ศ. 2559", "number": ""},
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    assert desc.year == "2016"


def test_a_five_digit_year_does_not_pass_as_a_resolved_one(configs: None) -> None:
    """It converts to another five-digit value, which is not a year."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": TITLE, "number": "", "year": "25590", "date": ""},
        jurisdiction_code="xn",
        source_bytes=SOURCE,
        fallback_stem="source",
        classification_text=SOURCE_TEXT,
    )
    assert desc.year == "2016"
