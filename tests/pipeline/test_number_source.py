"""A class declaring `number_source: title_identity` is numbered by its title alone."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from codify.frbr import TITLE_DIGEST_PREFIX, build_frbr_work_uri
from codify.jurisdictions import JurisdictionConfig, try_load_config
from codify.pipeline import stages

CODE = "xs"
# Fabricated: two issuers, each numbering its own requirements from 1.
NORTH = "Requirement of the Northern Harbour Board on Mooring Fees No. 3 of 2018"
SOUTH = "Requirement of the Southern Harbour Board on Mooring Fees No. 3 of 2018"
IDENTITY = {"strip_prefixes": ["Requirement"], "year_particles": ["of"]}


def _classes(*, switch: bool) -> dict[str, object]:
    requirement: dict[str, object] = {"label": "Requirement", "frbr_subtype": "requirement"}
    if switch:
        requirement["number_source"] = "title_identity"
    return {"act": {"label": "Act"}, "requirement": requirement}


@pytest.fixture
def configs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tests.config_fixtures import isolated_configs

    def build(code: str, *, switch: bool) -> dict[str, object]:
        return {
            "frbr": {"country_code": code, "title_identity": IDENTITY},
            "document_classes": _classes(switch=switch),
        }

    # `xt` is the control: the same jurisdiction without the switch.
    with isolated_configs(
        monkeypatch,
        tmp_path / "jurisdictions",
        {CODE: build(CODE, switch=True), "xt": build("xt", switch=False)},
    ):
        yield


def _uri(title: str, number: str, *, code: str = CODE, doctype: str = "requirement") -> str:
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"title": title, "number": number, "year": "2018", "date": ""},
        jurisdiction_code=code,
        source_bytes=title.encode(),
        fallback_stem="source",
        requested_doctype=doctype,
    )
    return build_frbr_work_uri(code, desc.doctype, desc.year, desc.number)


def test_the_switch_forces_the_title_digest_over_a_stated_number(configs: None) -> None:
    uri = _uri(NORTH, "3")
    digest = rf"{TITLE_DIGEST_PREFIX}[0-9a-f]{{12}}"
    assert re.fullmatch(rf"/akn/{CODE}/act/requirement/2018/{digest}", uri), uri


def test_two_issuers_with_one_serial_get_two_work_uris(configs: None) -> None:
    assert _uri(NORTH, "3") != _uri(SOUTH, "3")


@pytest.mark.parametrize("stated", ["", "3", "2", "2018-3"])
def test_the_uri_is_the_same_whatever_number_the_model_stated(configs: None, stated: str) -> None:
    assert _uri(NORTH, stated) == _uri(NORTH, "")


def test_without_the_switch_the_stated_number_still_wins(configs: None) -> None:
    """Control: the declaration, not the grammar, is what discards the number."""
    assert _uri(NORTH, "3", code="xt") == "/akn/xt/act/requirement/2018/3"
    assert _uri(SOUTH, "3", code="xt") == _uri(NORTH, "3", code="xt")


def test_other_classes_keep_their_stated_number(configs: None) -> None:
    assert _uri("Harbour Act 2018", "7", doctype="act") == f"/akn/{CODE}/act/2018/7"


def test_the_switch_without_a_title_grammar_is_refused() -> None:
    with pytest.raises(ValueError, match="frbr.title_identity"):
        JurisdictionConfig.model_validate(
            {
                "code": CODE,
                "name": "Example",
                "tradition": ["civil_law"],
                "languages": ["eng"],
                "document_classes": _classes(switch=True),
            }
        )


def _bundled_codes() -> list[str]:
    from codify.jurisdictions import JURISDICTIONS_DIR

    return sorted(p.parent.name for p in JURISDICTIONS_DIR.glob("*/config.json"))


@pytest.mark.parametrize("code", _bundled_codes())
def test_no_bundled_class_opts_in(code: str) -> None:
    """The default is the stated number, so every bundled URI mints as before."""
    cfg = try_load_config(code)
    assert cfg is not None
    opted = [n for n, dc in cfg.document_classes.items() if dc.number_source != "stated"]
    assert opted == []
