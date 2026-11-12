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
    for mod in pkgutil.walk_packages(adapters.__path__, f"{adapters.__name__}."):
        importlib.import_module(mod.name)
    names: set[str] = set()
    stack = list(BaseAcquirer.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if inspect.getmodule(cls).__name__.startswith(adapters.__name__):  # type: ignore[union-attr]
            names.add(cls.__name__)
    return names | {"AknNativeAcquirer"}


def test_every_shipped_adapter_maps_to_a_publisher_or_is_listed_without_one() -> None:
    assert _shipped_acquirers() == set(ADAPTER_PUBLISHERS)
    assert {p for p in ADAPTER_PUBLISHERS.values() if p} <= set(PUBLISHERS)


def test_no_pattern_is_verified_yet() -> None:
    assert PATTERN_VERIFIED and not any(PATTERN_VERIFIED.values())
    assert all(PATTERN_VERIFIED.get(f"{k}.act") is not None for k in PUBLISHERS)


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
            None,
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


def test_builders_round_trip_through_the_parser() -> None:
    cases = [
        ("riigi-teataja", "5555", "130062026129"),
        ("legislation-gov-uk", "ukpga/1998/42", "2026-04-06"),
        ("zakon-rada", "254к/96-вр", "ed20260525"),
        ("ocs", "ก0003-1B-0001", "abc123"),
        ("dre", "lei/12/2020", "20240101"),
    ]
    for key, work, edition in cases:
        publisher = PUBLISHERS[key]
        for url in (publisher.act_url(work, edition), publisher.edition_url(work, edition)):
            assert url is not None
            parsed = parse_official_url(url)
            assert parsed is not None and parsed.publisher == key
            assert work in parsed.lookup or edition in parsed.lookup


def test_a_publisher_with_no_such_page_returns_none() -> None:
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
    monkeypatch.setitem(PATTERN_VERIFIED, "legislation-gov-uk.fragment.section", True)
    assert (
        gb.provision_url("ukpga/1998/42", None, "section/13/2")
        == "https://www.legislation.gov.uk/ukpga/1998/42/section/13/2"
    )
    assert gb.provision_url("ukpga/1998/42", None, "schedule/1/part/3") is None


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
