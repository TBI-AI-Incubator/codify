"""OECD, OecdCompendiumAcquirer unit tests via httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DiscoveryQuery, DocumentRef, registered_kinds
from codify.acquisition.adapters.oecd.compendium import (
    OecdCompendiumAcquirer,
    OecdInstrumentMissing,
    instrument_key,
    wrap_body,
)
from codify.jurisdictions import SourceAdapter

BASE = "https://legalinstruments.oecd.org"

# The list endpoint's rows, in the API's own shape; the instruments are invented.
_LIST = [
    {
        "id": "901",
        "key": "OECD/LEGAL/9002",
        "adoptionDate": "2020-05-05",
        "lastPublishDate": "2025-02-20T11:44:25+01:00",
        "title": "Recommendation of the Council on Lantern Registers",
        "status": {"id": "1", "name": "In force"},
        "type": {"id": "2", "name": "Recommendation"},
    },
    {
        "id": "902",
        "key": "OECD/LEGAL/9003",
        "adoptionDate": "1961-12-12",
        "lastPublishDate": "2024-01-01T00:00:00+01:00",
        "title": "Decision of the Council on Harbour Lights",
        "status": {"id": "1", "name": "In force"},
        "type": {"id": "1", "name": "Decision"},
    },
]

_RECORD = {
    "id": 901,
    "key": "OECD/LEGAL/9002",
    "transmittedAt": "2026-09-18T16:59:47+02:00",
    "title": {
        "name": [
            {"lang": "en", "value": "Recommendation of the Council on Lantern Registers"},
            {"lang": "fr", "value": "Recommandation du Conseil sur les registres de lanternes"},
        ]
    },
    "status": {"id": 1},
    "type": {"id": 2},
    "statusSummary": {
        "adoptionDate": "2020-05-05",
        "inForceDate": "2020-05-05",
        "adherents": {
            "countryRef": [
                {"id": 11, "adherenceTypeId": 1},
                {"id": 104, "adherenceTypeId": 2, "date": "2021-02-16"},
            ]
        },
    },
    "themes": {"theme": [{"id": 9}]},
    "committees": {"parentCommittee": [{"id": 863}]},
    "relations": {"relatedTo": [{"id": 668, "key": "OECD/LEGAL/9001"}]},
    "changeHistory": {
        "update": [{"date": "2020-05-05", "type": {"id": 1}, "OLISRef": [{"uri": "C(2020)1"}]}]
    },
    "bodyText": {
        "ref": [
            {"lang": "en", "format": "html", "uri": "/public/doc/901/body-text.en.html"},
            {"lang": "fr", "format": "html", "uri": "/public/doc/901/body-text.fr.html"},
        ],
        "orig": [{"lang": "en", "format": "docx", "uri": "/public/doc/901/body-text.en.docx"}],
    },
    "monitoring": {
        "entry": [{"clause": {"id": 3}, "year": "2025", "OLISRef": [{"uri": "C(2025)9"}]}]
    },
}

_BODY_EN = (
    "<p>THE COUNCIL,</p><p>On the proposal of the Committee:</p>"
    "<p>I. RECOMMENDS that Adherents light lanterns.</p>"
)
_BODY_FR = (
    "<p>LE CONSEIL,</p><p>Sur proposition du Comité :</p>"
    "<p>I. RECOMMANDE que les Adhérents allument les lanternes.</p>"
)


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/instruments":
        assert request.url.params["lang"] == "en"
        assert request.url.params["statusIds"] == "1"
        rows = _LIST
        if "typeIds" in request.url.params:
            rows = [r for r in rows if r["type"]["id"] == request.url.params["typeIds"]]
        return httpx.Response(200, json=rows)
    if path == "/api/instruments/OECD-LEGAL-9002":
        return httpx.Response(200, json=_RECORD)
    if path.startswith("/api/instruments/"):
        return httpx.Response(404, json={"message": "not found"})
    if path == "/public/doc/901/body-text.en.html":
        return httpx.Response(200, text=_BODY_EN)
    if path == "/public/doc/901/body-text.fr.html":
        return httpx.Response(200, text=_BODY_FR)
    return httpx.Response(500)


def _acquirer() -> OecdCompendiumAcquirer:
    adapter = SourceAdapter(
        kind="oecd_compendium", name="oecd-compendium", base_url=BASE, rate_limit_per_minute=60
    )
    return OecdCompendiumAcquirer(
        adapter, client=httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    )


def test_kind_is_registered() -> None:
    assert "oecd_compendium" in registered_kinds()


def test_instrument_key_pads_the_serial() -> None:
    assert instrument_key("406") == "OECD/LEGAL/0406"
    assert instrument_key("0406") == "OECD/LEGAL/0406"


@pytest.mark.asyncio
async def test_discover_lists_instruments_in_force_with_their_publish_date() -> None:
    refs = [r async for r in _acquirer().discover(DiscoveryQuery())]
    assert [(r.doctype, r.year, r.number) for r in refs] == [
        ("recommendation", 2020, "9002"),
        ("decision", 1961, "9003"),
    ]
    assert refs[0].languages == ["eng", "fra"]
    assert refs[0].extra["last_published"] == "2025-02-20T11:44:25+01:00"
    assert refs[0].extra["key"] == "OECD/LEGAL/9002"


@pytest.mark.asyncio
async def test_discover_filters_by_class_and_limit() -> None:
    refs = [r async for r in _acquirer().discover(DiscoveryQuery(doctype="decision"))]
    assert [r.number for r in refs] == ["9003"]
    refs = [r async for r in _acquirer().discover(DiscoveryQuery(limit=1))]
    assert [r.number for r in refs] == ["9002"]
    assert [r async for r in _acquirer().discover(DiscoveryQuery(doctype="statute"))] == []


@pytest.mark.asyncio
async def test_fetch_wraps_both_language_bodies_and_carries_the_record() -> None:
    ref = DocumentRef(jurisdiction_code="oecd", doctype="recommendation", year=2020, number="9002")
    doc = await _acquirer().fetch(ref)
    assert doc.frbr_work_uri == "/akn/oecd/act/recommendation/2020/9002"
    assert doc.source_url == f"{BASE}/en/instruments/OECD-LEGAL-9002"
    assert [(b.role, b.language, b.media_type) for b in doc.bodies] == [
        ("primary", "eng", "text/html"),
        ("translation", "fra", "text/html"),
    ]
    primary = doc.primary().content.decode()
    assert '<meta name="oecd.key" content="OECD/LEGAL/9002">' in primary
    assert '<meta name="oecd.adopted" content="2020-05-05">' in primary
    assert "<title>Recommendation of the Council on Lantern Registers</title>" in primary
    assert _BODY_EN in primary
    meta = doc.upstream_metadata
    assert meta["key"] == "OECD/LEGAL/9002"
    assert meta["committees"] == [863]
    assert meta["related"] == ["OECD/LEGAL/9001"]
    assert meta["adherence"] == [
        {"country_id": 11, "adherence_type_id": 1, "date": None},
        {"country_id": 104, "adherence_type_id": 2, "date": "2021-02-16"},
    ]
    assert meta["monitoring"] == [{"year": "2025", "olis": ["C(2025)9"]}]
    assert meta["change_history"][0]["olis"] == ["C(2020)1"]
    assert meta["title_fr"].startswith("Recommandation")
    # The metadata round-trips as JSON, which is how the ingest run stores it.
    json.dumps(meta)


@pytest.mark.asyncio
async def test_fetch_of_an_unknown_key_raises() -> None:
    ref = DocumentRef(jurisdiction_code="oecd", doctype="recommendation", year=2020, number="9999")
    with pytest.raises(OecdInstrumentMissing):
        await _acquirer().fetch(ref)


def test_wrap_body_escapes_the_meta() -> None:
    page = wrap_body(
        "<p>x</p>", meta={"key": "K", "title": 'A "quoted" <title>', "lang": "en"}
    ).decode()
    assert 'content="A &quot;quoted&quot; &lt;title&gt;"' in page
    assert "<title>A &quot;quoted&quot; &lt;title&gt;</title>" in page
