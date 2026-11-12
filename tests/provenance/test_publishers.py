"""Publisher registry: builders, parsers, verification flags and adapter coverage."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import codify.acquisition.adapters as adapters
from codify.acquisition.base import AcquiredDocument, BaseAcquirer, Body, DocumentRef
from codify.provenance import (
    ADAPTER_PUBLISHERS,
    PATTERN_VERIFIED,
    PUBLISHERS,
    OfficialRef,
    celex_ref,
    official_ref_from_url,
    parse_official_url,
)


def _shipped_acquirers() -> set[str]:
    """Every class in an adapter module that can fetch, whether or not it subclasses the base."""
    names: set[str] = set()
    for mod in pkgutil.walk_packages(adapters.__path__, f"{adapters.__name__}."):
        module = importlib.import_module(mod.name)
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and name.endswith("Acquirer"):
                names.add(name)
            elif cls.__module__ == module.__name__ and issubclass(cls, BaseAcquirer):
                names.add(name)
    return names


def test_every_shipped_adapter_maps_to_a_publisher_or_is_listed_without_one() -> None:
    assert _shipped_acquirers() == set(ADAPTER_PUBLISHERS)
    assert {p for p in ADAPTER_PUBLISHERS.values() if p} <= set(PUBLISHERS)


def test_no_pattern_is_verified_yet_and_every_builder_has_a_flag() -> None:
    assert not any(PATTERN_VERIFIED.values())
    for key, p in PUBLISHERS.items():
        for kind, builder in (
            ("act", p.act),
            ("edition", p.edition),
            ("current", p.current),
            ("fragment", p.fragment),
        ):
            if builder is not None and not (key == "legislation-gov-uk" and kind == "fragment"):
                assert f"{key}.{kind}" in PATTERN_VERIFIED, (key, kind)


@pytest.mark.parametrize(
    ("url", "publisher", "work_id", "edition_id", "fragment"),
    [
        (
            "https://www.riigiteataja.ee/akt/130062026129",
            "riigi-teataja",
            None,
            "130062026129",
            None,
        ),
        (
            "https://www.riigiteataja.ee/akt/130062026129.xml",
            "riigi-teataja",
            None,
            "130062026129",
            None,
        ),
        (
            "https://www.riigiteataja.ee/akt/130062026129#pr10lg2",
            "riigi-teataja",
            None,
            "130062026129",
            "pr10lg2",
        ),
        (
            "https://www.legislation.gov.uk/ukpga/1998/42",
            "legislation-gov-uk",
            "ukpga/1998/42",
            None,
            None,
        ),
        (
            "https://www.legislation.gov.uk/id/ukpga/1998/42/section/13/2",
            "legislation-gov-uk",
            "ukpga/1998/42",
            None,
            "section/13/2",
        ),
        (
            "https://www.legislation.gov.uk/ukpga/1998/42/enacted/data.akn",
            "legislation-gov-uk",
            "ukpga/1998/42",
            "enacted",
            None,
        ),
        (
            "https://www.legislation.gov.uk/uksi/2003/1887/2026-04-06",
            "legislation-gov-uk",
            "uksi/2003/1887",
            "2026-04-06",
            None,
        ),
        (
            "https://www.legislation.gov.uk/aep/Cha2/14/15/data.akn",
            "legislation-gov-uk",
            "aep/Cha2/14/15",
            None,
            None,
        ),
        (
            "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679",
            "eur-lex",
            "32016R0679",
            None,
            None,
        ),
        (
            "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A02014R0833-20250101",
            "eur-lex",
            "32014R0833",
            "02014R0833-20250101",
            None,
        ),
        (
            "https://publications.europa.eu/resource/celex/32004L0042",
            "eur-lex",
            "32004L0042",
            None,
            None,
        ),
        ("http://data.europa.eu/eli/reg/2016/679/oj", "eur-lex", "32016R0679", None, None),
        (
            "https://legalinstruments.oecd.org/en/instruments/OECD-LEGAL-0368",
            "oecd-compendium",
            "OECD/LEGAL/0368",
            "en",
            None,
        ),
        ("https://zakon.rada.gov.ua/laws/show/435-15", "zakon-rada", "435-15", None, None),
        (
            "https://zakon.rada.gov.ua/laws/show/435-15/ed20260525#n14",
            "zakon-rada",
            "435-15",
            "ed20260525",
            "n14",
        ),
        (
            "https://zakon.rada.gov.ua/laws/show/254%D0%BA/96-%D0%B2%D1%80",
            "zakon-rada",
            "254к/96-вр",
            None,
            None,
        ),
        ("https://zakon.rada.gov.ua/go/435-15", "zakon-rada", "435-15", None, None),
        ("https://zakon.rada.gov.ua/laws/show/en/435-15", "zakon-rada", "435-15", "en", None),
        (
            "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32012D0026(01)",
            "eur-lex",
            "32012D0026(01)",
            None,
            None,
        ),
        (
            "https://data.dre.pt/eli/dec-lei/7/2009/02/12/p/dre/pt/html",
            "dre",
            "dec-lei/7/2009",
            None,
            None,
        ),
        ("https://qbz.gov.al/eli/fz/2020/226/162-2020.pdf", "qbz", "fz/2020/226", None, None),
        (
            "https://searchlaw.ocs.go.th/council-of-state/#/public/doc/abc123",
            "ocs",
            None,
            "abc123",
            None,
        ),
        ("https://data.dre.pt/eli/lei/12/2020/p/dre/pt/html", "dre", "lei/12/2020", None, None),
        (
            "https://data.dre.pt/eli/lei/12/2020/p/cons/20240101",
            "dre",
            "lei/12/2020",
            "20240101",
            None,
        ),
        (
            "https://www.boe.es/eli/es/l/2015/10/01/39/dof/spa/xml",
            "boe",
            "es/l/2015/10/01/39",
            None,
            None,
        ),
        (
            "https://www.consultoria.gov.do/uploads/gaceta-1.pdf",
            "gaceta-oficial-do",
            "uploads/gaceta-1.pdf",
            None,
            None,
        ),
    ],
)
def test_parser_reads_ids_out_of_official_urls(
    url: str, publisher: str, work_id: str | None, edition_id: str | None, fragment: str | None
) -> None:
    parsed = parse_official_url(url)
    assert parsed is not None
    assert (parsed.publisher, parsed.work_id, parsed.edition_id, parsed.fragment) == (
        publisher,
        work_id,
        edition_id,
        fragment,
    )
    assert parsed.lookup


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/akt/1",
        "https://www.riigiteataja.ee/vaheleht.html",
        "https://www.legislation.gov.uk/notatoken/2020/1",
        "https://eur-lex.europa.eu/homepage.html",
        "ftp://www.riigiteataja.ee/akt/1",
        "./130062026129",
    ],
)
def test_parser_ignores_other_urls(url: str) -> None:
    assert parse_official_url(url) is None


@pytest.fixture
def all_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in PATTERN_VERIFIED:
        monkeypatch.setitem(PATTERN_VERIFIED, key, True)


def test_builders_round_trip_through_the_parser(all_verified: None) -> None:
    cases = [
        ("riigi-teataja", "5555", "130062026129"),
        ("legislation-gov-uk", "ukpga/1998/42", "2026-04-06"),
        ("zakon-rada", "254к/96-вр", "ed20260525"),
        ("ocs", "ก0003-1B-0001", "abc123"),
        ("dre", "lei/12/2020", "20240101"),
        ("dre", "dec-lei/7/2009", "20240101"),
    ]
    for key, work, edition in cases:
        publisher = PUBLISHERS[key]
        for url in (publisher.act_url(work, edition), publisher.edition_url(work, edition)):
            assert url is not None
            parsed = parse_official_url(url)
            assert parsed is not None and parsed.publisher == key
            assert work in parsed.lookup or edition in parsed.lookup


def test_a_publisher_with_no_such_page_returns_none(all_verified: None) -> None:
    assert PUBLISHERS["riigi-teataja"].act_url("5555") is None
    assert PUBLISHERS["qbz"].edition_url("fz/2020/226", "x") is None
    assert PUBLISHERS["eur-lex"].current_url("32016R0679") is None


def test_a_fragment_is_withheld_until_its_pattern_is_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = PUBLISHERS["riigi-teataja"]
    assert rt.provision_url("5555", "130062026129", "para10") is None
    monkeypatch.setitem(PATTERN_VERIFIED, "riigi-teataja.fragment", True)
    assert (
        rt.provision_url("5555", "130062026129", "para10")
        == "https://www.riigiteataja.ee/akt/130062026129#para10"
    )


def test_a_gb_fragment_is_verified_per_provision_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    gb = PUBLISHERS["legislation-gov-uk"]
    monkeypatch.setitem(PATTERN_VERIFIED, "legislation-gov-uk.fragment.section.2", True)
    assert (
        gb.provision_url("ukpga/1998/42", None, "section/13/2")
        == "https://www.legislation.gov.uk/ukpga/1998/42/section/13/2"
    )
    assert gb.provision_url("ukpga/1998/42", None, "schedule/1/part/3") is None
    assert gb.provision_url("ukpga/1998/42", None, "section/13") is None
    assert gb.provision_url("ukpga/1998/42", None, "section/13/2/a") is None


def test_celex_ref_keeps_a_consolidated_text_as_the_edition() -> None:
    assert celex_ref("32016R0679") == OfficialRef(publisher="eur-lex", work_id="32016R0679")
    ref = celex_ref("02023R0956-20251020", edition_date="2025-10-20")
    assert (ref.work_id, ref.edition_id, ref.edition_date) == (
        "32023R0956",
        "02023R0956-20251020",
        "2025-10-20",
    )


def test_official_ref_from_url_needs_a_work_id() -> None:
    assert official_ref_from_url("https://www.riigiteataja.ee/akt/1") is None
    ref = official_ref_from_url(
        "https://zakon.rada.gov.ua/laws/show/435-15", edition_date="2026-05-25"
    )
    assert ref == OfficialRef(publisher="zakon-rada", work_id="435-15", edition_date="2026-05-25")


def test_an_acquired_document_carries_its_ids_as_json() -> None:
    doc = AcquiredDocument(
        ref=DocumentRef(jurisdiction_code="ee", doctype="act", year=2022, number="1"),
        frbr_work_uri="/akn/ee/act/2022/x",
        bodies=[Body(media_type="application/xml", content=b"<a/>")],
        source_url="https://www.riigiteataja.ee/akt/1.xml",
        official=OfficialRef(
            publisher="riigi-teataja", work_id="5", edition_id="1", edition_date="2023-01-01"
        ),
    )
    dumped = doc.model_dump(mode="json")["official"]
    assert dumped == {
        "publisher": "riigi-teataja",
        "work_id": "5",
        "edition_id": "1",
        "edition_date": "2023-01-01",
    }


def test_no_page_link_is_given_until_its_pattern_is_verified() -> None:
    gb = PUBLISHERS["legislation-gov-uk"]
    assert gb.act_url("ukpga/1998/42") is None
    assert gb.edition_url("ukpga/1998/42", "enacted") is None
    assert gb.current_url("ukpga/1998/42") is None
    assert gb.page_url("ukpga/1998/42", "enacted") is None


def test_the_page_falls_back_from_edition_to_act(monkeypatch: pytest.MonkeyPatch) -> None:
    gb = PUBLISHERS["legislation-gov-uk"]
    monkeypatch.setitem(PATTERN_VERIFIED, "legislation-gov-uk.act", True)
    assert gb.page_url("ukpga/1998/42", "enacted") == (
        "https://www.legislation.gov.uk/ukpga/1998/42",
        "act",
    )
    monkeypatch.setitem(PATTERN_VERIFIED, "legislation-gov-uk.edition", True)
    assert gb.page_url("ukpga/1998/42", "enacted") == (
        "https://www.legislation.gov.uk/ukpga/1998/42/enacted",
        "edition",
    )
    assert gb.page_url("ukpga/1998/42") == (
        "https://www.legislation.gov.uk/ukpga/1998/42",
        "act",
    )


def test_a_suffixed_celex_is_not_cut_to_another_act() -> None:
    assert celex_ref("32012D0026(01)").work_id == "32012D0026(01)"
    assert celex_ref("32016R0679R(01)").work_id == "32016R0679"


def test_a_dated_dre_work_is_stored_undated_and_consolidated_links_drop_the_date(
    all_verified: None,
) -> None:
    dre = PUBLISHERS["dre"]
    assert dre.edition_url("dec-lei/7/2009/02/12", "20240101") == (
        "https://data.dre.pt/eli/dec-lei/7/2009/p/cons/20240101"
    )
    assert (
        dre.current_url("dec-lei/7/2009/02/12") == "https://data.dre.pt/eli/dec-lei/7/2009/p/cons"
    )
    cons = parse_official_url("https://data.dre.pt/eli/dec-lei/7/2009/p/cons/20240101")
    printed = parse_official_url("https://data.dre.pt/eli/dec-lei/7/2009/02/12/p/dre")
    assert cons is not None and printed is not None
    assert cons.work_id == printed.work_id == "dec-lei/7/2009"


@pytest.mark.parametrize(
    ("key", "eid", "fragment"),
    [
        ("legislation-gov-uk", "section-13-2", "section/13/2"),
        ("legislation-gov-uk", "section-9A-1_dup2", "section/9A/1"),
        ("legislation-gov-uk", "article-2", "article/2"),
        ("legislation-gov-uk", "crossheading-powers", None),
        ("legislation-gov-uk", "date-2020", None),
        ("legislation-gov-uk", "period-from-2020", None),
        ("eur-lex", "chp_1__sec_2__art_5__para_1", "art_5"),
        ("eur-lex", "art_1__para_1", "art_1"),
        ("eur-lex", "att_1__art_2", None),
        ("eur-lex", "art_1__para_2__mod_1__qstr_1__art_5", None),
        ("eur-lex", "art_3_dup1", None),
        ("eur-lex", "recitals__rec_4", None),
        ("riigi-teataja", "art_10", None),
    ],
)
def test_an_eid_gives_a_fragment_only_where_a_rule_is_sure(
    key: str, eid: str, fragment: str | None
) -> None:
    assert PUBLISHERS[key].fragment_for_eid(eid) == fragment


def test_the_rada_english_edition_has_its_own_page(all_verified: None) -> None:
    rada = PUBLISHERS["zakon-rada"]
    assert rada.edition_url("435-15", "en") == "https://zakon.rada.gov.ua/laws/show/en/435-15"
    assert rada.provision_url("435-15", "en/ed20260525", "n12") == (
        "https://zakon.rada.gov.ua/laws/show/en/435-15/ed20260525#n12"
    )
    assert (
        rada.edition_url("435-15", "ed20260525")
        == "https://zakon.rada.gov.ua/laws/show/435-15/ed20260525"
    )


def test_a_rada_url_keeps_both_language_and_date() -> None:
    parsed = parse_official_url("https://zakon.rada.gov.ua/laws/show/en/435-15/ed20260525#n12")
    assert parsed is not None
    assert (parsed.work_id, parsed.edition_id, parsed.fragment) == (
        "435-15",
        "en/ed20260525",
        "n12",
    )


def test_an_oecd_french_edition_never_falls_back_to_the_english_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(PATTERN_VERIFIED, "oecd-compendium.act", True)
    oecd = PUBLISHERS["oecd-compendium"]
    assert oecd.page_url("OECD/LEGAL/0368", "fr") == (
        "https://legalinstruments.oecd.org/fr/instruments/OECD-LEGAL-0368",
        "act",
    )


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("https://www.legislation.gov.uk/ukpga/1998/42#section-13", "section-13"),
        ("https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679#art_5", "art_5"),
    ],
)
def test_an_explicit_url_fragment_is_kept(url: str, fragment: str) -> None:
    parsed = parse_official_url(url)
    assert parsed is not None and parsed.fragment == fragment


def test_an_ocs_timeline_id_may_hold_slashes_plus_and_equals(all_verified: None) -> None:
    for timeline in ("+/8=", "AAA/"):
        url = PUBLISHERS["ocs"].edition_url("x", timeline)
        assert url is not None
        parsed = parse_official_url(url)
        assert parsed is not None and parsed.edition_id == timeline


def test_a_malformed_url_is_not_a_publisher_url() -> None:
    assert parse_official_url("https://[broken/") is None


def test_an_ocs_page_can_be_built_from_the_work_level_timeline_alone(
    all_verified: None,
) -> None:
    ocs = PUBLISHERS["ocs"]
    expected = "https://searchlaw.ocs.go.th/council-of-state/#/public/doc/abc"
    assert ocs.act_url("abc") == expected
    assert ocs.current_url("abc") == expected
    assert ocs.page_url("abc") == (expected, "act")
