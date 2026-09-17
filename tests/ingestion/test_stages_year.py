"""resolve_year and gregorian_year extract year from metadata or fallback title."""

import pytest

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


def test_the_stored_year_matches_the_uri_year_on_a_title_only_year(tmp_path, monkeypatch) -> None:
    """The helper agrees with the URI path either way: a title-only year is read
    as written with no title grammar declared, and converted by one where it is."""
    from codify.pipeline.stages import resolve_year
    from tests.config_fixtures import isolated_configs

    conversion = {"kind": "buddhist", "epoch_year": -543}
    grammar = {"strip_prefixes": ["พระราชบัญญัติ"], "year_particles": ["พ.ศ."]}
    configs = {
        "xb": {
            "calendar": "buddhist_era",
            "frbr": {"country_code": "xb", "calendar_conversion": conversion},
        },
        "xg": {
            "calendar": "buddhist_era",
            "frbr": {
                "country_code": "xg",
                "calendar_conversion": conversion,
                "title_identity": grammar,
            },
        },
        "xc": {
            "calendar": "buddhist_era",
            "frbr": {
                "country_code": "xc",
                "calendar_conversion": {"kind": "buddhist", "epoch_year": -200},
            },
        },
    }
    metadata = {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511"}
    with isolated_configs(monkeypatch, tmp_path / "jurisdictions", configs):
        assert resolve_year(metadata, "xb", title=str(metadata["title"])) == "2511"
        assert gregorian_year(metadata, "xb") == 2511
        assert resolve_year(metadata, "xg", title=str(metadata["title"])) == "1968"
        assert gregorian_year(metadata, "xg") == 1968
        # A year the model states, on a declared epoch the calendar name does
        # not imply: both paths take the jurisdiction's rule, not the generic one.
        labelled = {"year": "2511", "calendar": "buddhist"}
        assert resolve_year(labelled, "xc") == "2311"
        assert gregorian_year(labelled, "xc") == 2311


@pytest.mark.parametrize("label", ["buddhist", " buddhist ", "BUDDHIST", " Buddhist_Era "])
def test_a_padded_or_cased_label_still_names_the_configured_calendar(
    label: str, tmp_path, monkeypatch
) -> None:
    """The label is a model answer, so its spacing and case vary; a comparison
    form built from the raw string misses the rule and takes the generic offset.
    """
    from tests.config_fixtures import isolated_configs

    configs = {
        "xc": {
            "calendar": "buddhist_era",
            "frbr": {
                "country_code": "xc",
                "calendar_conversion": {"kind": "buddhist", "epoch_year": -200},
            },
        }
    }
    from codify.calendar import declares_this_calendar, labelled_year_as_gregorian

    with isolated_configs(monkeypatch, tmp_path / "jurisdictions", configs):
        # Through the callers, which normalise, and at the helper, which is
        # where a caller passing a raw label would otherwise lose the rule.
        assert gregorian_year({"year": "2511", "calendar": label}, "xc") == 2311
        assert declares_this_calendar(label, "xc")
        assert labelled_year_as_gregorian("2511", label, "xc") == 2311
