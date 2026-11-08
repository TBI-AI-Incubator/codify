from __future__ import annotations

import importlib.util
import json
import unicodedata
from pathlib import Path
from types import ModuleType

import pytest

from codify.countries import _DATA, countries_named, country_names, languages

_ROOT = Path(__file__).resolve().parents[1]


def _build_script() -> ModuleType:
    path = _ROOT / "scripts" / "build_country_names.py"
    spec = importlib.util.spec_from_file_location("codify_build_country_names", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_names_match_only_in_the_texts_language() -> None:
    assert countries_named("Einfuhren aus Island", "deu") == [("IS", "Island")]
    assert countries_named("Island waters and the island shore", "eng") == []
    assert countries_named("Imports from Iceland", "deu") == []


def test_alternative_forms_match() -> None:
    text = "Goods from Turkey, the Czech Republic and Hong Kong."
    assert countries_named(text, "eng") == [
        ("CZ", "Czech Republic"),
        ("HK", "Hong Kong"),
        ("TR", "Turkey"),
    ]


def test_a_name_inside_a_longer_name_is_not_counted_again() -> None:
    assert countries_named("Hong Kong SAR China", "eng") == [("HK", "Hong Kong SAR China")]


def test_a_name_matches_as_written_or_in_capitals() -> None:
    assert countries_named("porcelain or china", "eng") == []
    assert countries_named("Made in China", "eng") == [("CN", "China")]
    assert countries_named("MADE IN CHINA", "eng") == [("CN", "CHINA")]
    assert countries_named("Made in CHina or cHINA", "eng") == []


def test_a_name_matches_only_as_a_whole_word() -> None:
    assert countries_named("Chinatown, Indo-China, China-based, Chinas", "eng") == []
    assert countries_named("China's ports (China).", "eng") == [("CN", "China")]


def test_apostrophes_line_breaks_and_invisible_characters_do_not_hide_a_name() -> None:
    assert countries_named("Côte d'Ivoire and Hong\nKong", "eng") == [
        ("CI", "Côte d’Ivoire"),
        ("HK", "Hong Kong"),
    ]
    decomposed = unicodedata.normalize("NFD", "Côte d‘Ivoire")
    assert countries_named(f"{decomposed}, Ger\u00admany", "eng") == [
        ("CI", "Côte d’Ivoire"),
        ("DE", "Germany"),
    ]


def test_a_non_breaking_hyphen_is_a_hyphen() -> None:
    assert countries_named("Guinée\u2011Bissau", "fra") == [("GW", "Guinée-Bissau")]
    assert countries_named("Saint-Kitts-et-Nevis", "fra") == [("KN", "Saint-Kitts-et-Nevis")]


def test_most_named_first_with_the_commonest_written_form() -> None:
    text = "China, Turkey, TURKEY, Türkiye, Turkey"
    assert countries_named(text, "eng") == [("TR", "Turkey"), ("CN", "China")]


def test_language_codes_normalise_and_unknown_languages_raise() -> None:
    assert countries_named("Iceland", "en") == countries_named("Iceland", "eng")
    with pytest.raises(ValueError, match="no country names"):
        countries_named("Islandia", "pol")


def test_the_table_carries_countries_only() -> None:
    not_countries = {"EU", "EZ", "UN", "QO", "XA", "XB", "XK", "ZZ", "AC", "CP", "DG", "EA", "IC"}
    for language in languages():
        codes = set(country_names(language))
        assert all(len(c) == 2 and c.isalpha() and c.isupper() for c in codes)
        assert not codes & not_countries, language


def test_the_data_was_built_from_the_scripts_pins() -> None:
    script = _build_script()
    sources = json.loads(_DATA.read_text(encoding="utf-8"))["sources"]
    assert sources["cldr"]["version"] == script.CLDR_VERSION
    assert sources["cldr"]["packages"] == script.CLDR_PACKAGES
    assert languages() == script.LANGUAGES


def test_the_eu_table_reads_short_and_full_names_by_iso_code() -> None:
    script = _build_script()
    fixture = _ROOT / "tests" / "fixtures" / "eu_country_table.xml"
    version, names = script.eu_names(fixture, frozenset({"CZ", "TR"}))
    assert version == "20990101-0"
    assert names == {
        "deu": {"CZ": ["Tschechien", "Tschechische Republik"]},
        "eng": {
            "CZ": ["Czechia", "Czech Republic"],
            "TR": ["Turkey", "Republic of Turkey", "Türkiye", "Republic of Türkiye"],
        },
    }


@pytest.mark.parametrize(
    ("xml", "refusal"),
    [
        ("<countries/>", "not a country authority table"),
        ('<languages table.id="language" version="1"><record/></languages>', "not a country"),
        (
            '<countries table.id="country" version="1"><record>'
            "<code-3166-1-alpha-2>CZ</code-3166-1-alpha-2>"
            '<label><lg.version lg="en">Czechia</lg.version></label></record></countries>',
            "names no country",
        ),
    ],
)
def test_a_file_that_is_not_the_eu_table_is_refused(tmp_path: Path, xml: str, refusal: str) -> None:
    path = tmp_path / "other.xml"
    path.write_text(xml, encoding="utf-8")
    with pytest.raises(SystemExit, match=refusal):
        _build_script().eu_names(path, frozenset({"CZ"}))


def test_sources_merge_and_a_name_two_countries_share_names_neither() -> None:
    merged = _build_script().merge(
        {"eng": {"CZ": ["Czechia"], "TR": ["Türkiye"]}},
        {"eng": {"CZ": ["Czech Republic", "Shared", "Caps"], "TR": ["Turkey", "Shared", "CAPS"]}},
    )
    assert merged == {"eng": {"CZ": ["Czechia", "Czech Republic"], "TR": ["Türkiye", "Turkey"]}}


def test_a_merge_that_would_leave_a_country_nameless_fails() -> None:
    with pytest.raises(SystemExit, match="no name"):
        _build_script().merge({"eng": {"CZ": ["Czechia"]}}, {"eng": {"TR": ["Czechia"]}})
