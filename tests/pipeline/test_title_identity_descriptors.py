"""A jurisdiction that numbers nothing gets its identity and date off the source."""

from __future__ import annotations

from pathlib import Path

import pytest

from codify.frbr import build_frbr_work_uri, is_citable_work_uri
from codify.jurisdictions import try_load_config
from codify.pipeline import stages
from codify.pipeline.stages import _year_int

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
# Fabricated: a title of this shape exists in no statute book. It carries the
# grammar under test — kind prefix, combining marks, edition parenthetical,
# year particle, native digits — and nothing else.
TITLE = "พระราชบัญญัติเครื่องร่อนสุริยะ (ฉบับที่ ๓) พ.ศ. ๒๕๑๑"
SOURCE_TEXT = f"{TITLE}\nให้ไว้ ณ วันที่ ๙ กันยายน พ.ศ. ๒๕๑๑\n"
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

    def build(
        *, with_identity: bool, epoch_year: int = -543, reform: bool = False
    ) -> dict[str, object]:
        frbr: dict[str, object] = {
            "country_code": "xn",
            "uri_patterns": {"act": "/akn/xn/act/{year}/{number}"},
            "calendar_conversion": {
                **conversion,
                "epoch_year": epoch_year,
                **({"new_year_month": 4, "new_year_reform_year": 2484} if reform else {}),
            },
        }
        if with_identity:
            frbr["title_identity"] = identity
        return {"calendar": "buddhist_era", "languages": ["tha"], "frbr": frbr}

    with isolated_configs(
        monkeypatch,
        tmp_path / "jurisdictions",
        {
            "xn": build(with_identity=True),
            "xm": build(with_identity=False),
            # A declared epoch the calendar's name does not imply.
            "xk": build(with_identity=True, epoch_year=-200),
            # A year that began mid-year before a reform.
            "xr": build(with_identity=True, reform=True),
        },
    ):
        yield


def _descriptors(code: str) -> stages.Descriptors:
    try_load_config.cache_clear()
    return stages.resolve_descriptors(
        {
            "title": TITLE,
            "number": "",
            "year": "๒๕๑๑",
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
    assert uri == "/akn/xn/act/1968/เครื่องร่อนสุริยะ-ฉบับที่-3"
    assert is_citable_work_uri(uri)


def test_without_one_the_identity_is_the_content_address(configs: None) -> None:
    """Control: the config declaration is what replaces the hash, not the title."""
    desc = _descriptors("xm")
    assert desc.number == stages.draft_number(SOURCE)
    assert not is_citable_work_uri(build_frbr_work_uri("xm", desc.doctype, desc.year, desc.number))


def test_the_source_supplies_the_day_the_model_did_not(configs: None) -> None:
    assert _descriptors("xn").raw_date == "1968-09-09"


def test_a_route_that_extracted_no_text_reads_no_date(configs: None) -> None:
    """Control: on the scanned route `source_bytes` is the file itself, whose
    decoded bytes carry markup rather than the document's own dated line."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": TITLE, "number": "", "year": "๒๕๑๑", "date": "", "calendar": "buddhist"},
        jurisdiction_code="xn",
        source_bytes=SOURCE,
        fallback_stem="source",
    )
    assert desc.raw_date == ""


def test_a_numbered_instrument_keeps_its_number(configs: None) -> None:
    """The title grammar is a fallback, not a replacement for a stated number."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": TITLE, "number": "17", "year": "๒๕๑๑", "calendar": "buddhist"},
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
        {"title": TITLE, "number": "", "year": "๒๕๑๑", "date": ""},
        jurisdiction_code="xn",
        source_bytes=SOURCE,
        fallback_stem="source",
        classification_text=SOURCE_TEXT,
    )
    assert desc.year == "1968"


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
    assert desc.raw_date == "1968-09-09"
    assert desc.year == "1968"


def test_a_title_only_year_is_local_whatever_its_digits(configs: None) -> None:
    """A year the model never stated came from the title, so it is in the local
    calendar even when it happens to read as four ASCII digits."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511", "number": ""},
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    assert desc.year == "1968"


def test_a_five_digit_year_does_not_pass_as_a_resolved_one(configs: None) -> None:
    """It converts to another five-digit value, which is not a year."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": TITLE, "number": "", "year": "25110", "date": "", "calendar": "buddhist"},
        jurisdiction_code="xn",
        source_bytes=SOURCE,
        fallback_stem="source",
        classification_text=SOURCE_TEXT,
    )
    assert desc.year == "1968"


def test_a_model_year_echoing_the_title_is_the_same_local_year(configs: None) -> None:
    """The model repeating what the title says is one reading, not independent
    Gregorian evidence, however it labelled the calendar."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511", "number": "", "year": "2511"},
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    assert desc.year == "1968"


def test_the_configured_conversion_rule_wins_over_the_generic_one(configs: None) -> None:
    """A config may declare an epoch the calendar's name does not imply; the
    source-date path already honours it, so this one must agree."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511", "number": ""},
        jurisdiction_code="xk",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    assert desc.year == "2311"


def test_the_unknown_year_sentinel_gives_way_to_a_recovered_date(configs: None) -> None:
    """The placeholder stands in for a year nobody read; a source that states
    one must replace it, not be refused by it."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "Untitled", "number": "", "year": "0001", "date": ""},
        jurisdiction_code="xn",
        source_bytes=SOURCE,
        fallback_stem="source",
        classification_text=SOURCE_TEXT,
    )
    assert desc.year == "1968"


def test_a_conversion_that_cannot_form_a_year_leaves_it_unresolved(configs: None) -> None:
    """A three-digit local year converts to a number no URI segment can carry."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 999", "number": ""},
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    # Not merely unusable: unresolved, so the URI takes the placeholder rather
    # than the three-digit number the offset produced.
    assert desc.year == ""
    assert _year_int(desc.year) is None


def test_a_decorated_model_year_still_echoes_the_title(configs: None) -> None:
    """A model answers with the era it just read; the field is decorated, the
    year run inside it is the same local year the title states."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511", "number": "", "year": "B.E. 2511"},
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    assert desc.year == "1968"


def _escape_rule_descriptors(metadata: dict[str, object]) -> stages.Descriptors:
    try_load_config.cache_clear()
    return stages.resolve_descriptors(
        metadata,
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )


def test_a_declared_identity_supersedes_the_generic_title_number(configs: None) -> None:
    """The generic inference reads only the number inside a title, so every
    second edition of a numberless series would resolve to the same segment."""
    two = _escape_rule_descriptors({"title": "Act Alpha (No. 2) of 1991", "number": ""})
    three = _escape_rule_descriptors({"title": "Act Alpha (No. 3) of 1991", "number": ""})
    assert two.number not in ("2", "3")
    assert two.number != three.number


def test_a_stated_number_still_wins_over_the_title(configs: None) -> None:
    """Control: the skip is of the inference, not of evidence about the document."""
    desc = _escape_rule_descriptors({"title": "Act Alpha (No. 2) of 1991", "number": "17"})
    assert desc.number == "17"


def test_a_year_straddling_two_gregorian_ones_follows_the_stated_date(configs: None) -> None:
    """Before the reform the local year began mid-year, so converting it with a
    month and without gives different years; the work date would then disagree
    with the URI year and be dropped."""
    try_load_config.cache_clear()
    title = "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. ๒๔๗๘"
    text = f"{title}\nให้ไว้ ณ วันที่ ๓๑ มกราคม พ.ศ. ๒๔๗๘\n"
    desc = stages.resolve_descriptors(
        {"title": title, "number": ""},
        jurisdiction_code="xr",
        source_bytes=text.encode(),
        fallback_stem="source",
        classification_text=text,
    )
    assert desc.raw_date == "1936-01-31"
    assert desc.year == "1936"


def test_an_echoed_year_that_cannot_convert_is_cleared_not_kept(configs: None) -> None:
    """The local value is not a Gregorian year; leaving it in place would put it
    in the URI instead of the unknown-year placeholder."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 999", "number": "", "year": "999"},
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    assert desc.year == ""


def test_a_date_field_echoing_the_title_is_local_too(configs: None) -> None:
    """A model dating a document in the local calendar while calling it
    Gregorian states the same local year in another field; the whole date is
    local, not only its year."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {
            "title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511",
            "number": "",
            "date": "2511-09-09",
            "calendar": "",
        },
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    assert desc.year == "1968"
    assert desc.raw_date == "1968-09-09"


def test_a_day_valid_only_in_the_converted_year_survives(configs: None) -> None:
    """29 February falls in a leap year of one calendar and not the other, so
    validating the day against the local year loses the date."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {
            "title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2503",
            "number": "",
            "date": "2503-02-29",
            "calendar": "",
        },
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    assert desc.year == "1960"
    assert desc.raw_date == "1960-02-29"


def test_a_date_field_naming_no_year_is_not_a_stated_year(configs: None) -> None:
    """Presence is not a statement: a field holding no year run leaves the
    title's local year unconverted in the URI."""
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511", "number": "", "date": "unknown"},
        jurisdiction_code="xn",
        source_bytes=b"",
        fallback_stem="source",
        classification_text="",
    )
    assert desc.year == "1968"
