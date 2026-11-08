from __future__ import annotations

import importlib.util
import json
import unicodedata
from pathlib import Path
from types import ModuleType

import pytest

from codify.countries import _DATA, countries_named, country_names, languages, name_forms

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


def test_a_name_that_is_a_common_word_is_dropped() -> None:
    assert countries_named("Man skal betale avgift. MAN SKAL", "nor") == []
    assert countries_named("Мали држави", "mkd") == []
    assert "IM" not in country_names("nor")
    table = {"nor": {"IM": ["Man"], "NO": ["Norge"]}}
    _build_script().drop_names(table, frozenset({"IM", "NO"}))
    assert table == {"nor": {"NO": ["Norge"]}}
    with pytest.raises(SystemExit, match="names no country"):
        _build_script().drop_names({"nor": {"NO": ["Norge"]}}, frozenset({"NO"}))


def test_a_name_that_is_another_countrys_code_is_dropped() -> None:
    assert countries_named("RU", "spa") == []
    assert countries_named("AS", "ind") == []
    table = {"spa": {"GB": ["Reino Unido", "RU"], "RU": ["Rusia"]}, "eng": {"US": ["US"]}}
    _build_script().drop_names(table, frozenset({"GB", "RU", "US"}))
    assert table == {"spa": {"GB": ["Reino Unido"], "RU": ["Rusia"]}, "eng": {"US": ["US"]}}


def test_a_place_holding_a_country_name_names_no_country() -> None:
    assert countries_named("England and Wales, Scotland and Northern Ireland", "eng") == []
    assert countries_named("NORTHERN IRELAND and Ireland", "eng") == [("IE", "Ireland")]
    assert countries_named("New Mexico, Papua New Guinea", "eng") == [("PG", "Papua New Guinea")]
    assert countries_named("l’Irlande du Nord et l’Irlande", "fra") == [("IE", "Irlande")]
    spanish = "Reino Unido de Gran Bretaña e Irlanda del Norte; Nuevo México y Nueva Guinea"
    assert countries_named(spanish, "spa") == [("GB", "Reino Unido")]
    assert countries_named("Nuovo Messico", "ita") == []


def test_places_come_from_cldr_where_it_has_them_and_the_list_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _build_script()
    monkeypatch.setattr(script, "LANGUAGES", frozenset({"eng"}))
    monkeypatch.setattr(script, "NOT_COUNTRIES", {"eng": ["New Guinea"]})
    names = {"eng": {"GN": ["Guinea"], "IE": ["Ireland"]}}
    subdivisions = {"eng": {"gbnir": "Northern Ireland", "gbeng": "England"}}
    assert script.not_countries(subdivisions, names) == {"eng": ["New Guinea", "Northern Ireland"]}
    monkeypatch.setattr(script, "NOT_COUNTRIES", {"eng": ["New Atlantis"]})
    with pytest.raises(SystemExit, match="holds no country's name"):
        script.not_countries(subdivisions, names)


def test_a_name_matches_only_as_a_whole_word() -> None:
    assert countries_named("Chinatown, Indo-China, China-based, Chinas", "eng") == []
    assert countries_named("China's ports (China).", "eng") == [("CN", "China")]


def test_a_hyphen_joins_a_name_only_to_a_capital() -> None:
    assert countries_named("EU-Japan and EU-UK talks", "eng") == [("GB", "UK"), ("JP", "Japan")]
    assert countries_named("China-Importe", "deu") == [("CN", "China")]
    assert countries_named("Guinea-Bissau, Timor-Leste, Papua New Guinea", "eng") == [
        ("GW", "Guinea-Bissau"),
        ("PG", "Papua New Guinea"),
        ("TL", "Timor-Leste"),
    ]
    assert countries_named("Guinée-Bissau, Papouasie-Nouvelle-Guinée, Nouvelle-Guinée", "fra") == [
        ("GW", "Guinée-Bissau"),
        ("PG", "Papouasie-Nouvelle-Guinée"),
    ]


def test_a_capitals_form_never_joins_a_hyphen() -> None:
    dutch = "VERENIGD KONINKRIJK VAN GROOT-BRITTANNIË EN NOORD-IERLAND"
    assert countries_named(dutch, "nld") == [("GB", "VERENIGD KONINKRIJK")]
    norwegian = "DET FORENTE KONGERIKE STORBRITANNIA OG NORD-IRLAND"
    assert countries_named(norwegian, "nor") == [("GB", "STORBRITANNIA")]
    assert countries_named("NOUVELLE-GUINÉE, NOUVEAU-MEXIQUE", "fra") == []
    assert countries_named("NIEUW-MEXICO", "nld") == []
    assert countries_named("NY-GUINEA", "nor") == []
    assert countries_named("INDO-CHINA, CHINA-BASED, EU-JAPAN", "eng") == []
    assert countries_named("EU-UK", "eng") == [("GB", "UK")]
    assert countries_named("GUINEA-BISSAU, TIMOR-LESTE", "eng") == [
        ("GW", "GUINEA-BISSAU"),
        ("TL", "TIMOR-LESTE"),
    ]


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
        table = country_names(language)
        codes = set(table)
        assert all(len(c) == 2 and c.isalpha() and c.isupper() for c in codes)
        assert not codes & not_countries, language
        for code, names in table.items():
            forms = {form for name in names for form in name_forms(name)}
            assert not (codes - {code}) & forms, (language, code)


def test_the_data_was_built_from_the_scripts_pins() -> None:
    script = _build_script()
    data = json.loads(_DATA.read_text(encoding="utf-8"))
    sources = data["sources"]
    assert sources["cldr"]["version"] == script.CLDR_VERSION
    assert sources["cldr"]["packages"] == script.CLDR_PACKAGES
    assert languages() == script.LANGUAGES
    for language, places in script.NOT_COUNTRIES.items():
        assert set(places) <= set(data["not_countries"][language])
    assert {"Northern Ireland", "New Jersey", "New Mexico"} <= set(data["not_countries"]["eng"])


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
