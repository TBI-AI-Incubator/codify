"""TH, OcsAcquirer unit tests via httpx.MockTransport, on invented laws."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DocumentRef, registered_kinds
from codify.acquisition.adapters.th.ocs import (
    OcsAcquirer,
    OcsItemMissing,
    section_text,
)
from codify.acquisition.base import DiscoveryQuery
from codify.acquisition.politeness import PoliteTransport
from codify.jurisdictions import SourceAdapter, TitleIdentity

RULE = TitleIdentity(
    strip_prefixes=["พระราชบัญญัติ"],
    year_particles=["พ.ศ."],
    edition_markers=["ฉบับที่"],
    consolidation_markers=["Update"],
)
BASE = "tl-base"
AMENDING = "tl-amend"
TRANSLATED = "tl-en"


def _section(seq: int, type_id: str, label: str, html: str) -> dict[str, Any]:
    return {
        "sectionId": seq,
        "sectionTypeId": type_id,
        "sectionSeq": seq,
        "sectionLabel": label,
        "sectionContent": html,
    }


def _timeline(tid: str, type_id: str, code: str, day: str, *, en: bool = False) -> dict[str, Any]:
    return {
        "timelineId": tid,
        "timelineTypeId": type_id,
        "timelineLawCode": code,
        "timelineDateAd": day,
        "updateDateAd": "2026-01-01",
        "hasSectionEn": en,
    }


_TIMELINES = [
    _timeline(BASE + "-0", "1", "ก0001-1B-0001-00", "2001-01-02"),
    _timeline(AMENDING, "2", "ก0001-1B-0001-01", "2010-05-02"),
    _timeline(BASE, "4", "ก0001-1B-0001-02", "2010-05-02"),
]


def _doc(
    title: str, type_id: str, published: str, timelines: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "respHeader": {"errorCode": "SUCCESS"},
        "respBody": {
            "lawInfo": {
                "timelineTypeId": type_id,
                "timelineLawCode": "ก0001-1B-0001-xx",
                "lawCode": "ก0001-1B-0001",
                "lawNameTh": title,
                "lawNameEn": "TEST ACT",
                "stateId": "01",
                "publishDateAd": published,
                "effectiveDateStartAd": published,
            },
            "lawSections": [
                _section(2, "4", "มาตรา 1", "<p><span>มาตรา ๑</span><span> ทด</span>สอบ</p>"),
                _section(1, "1", "ชื่อกฎหมาย", f"<p>{title}</p>"),
            ],
            "footnoteList": [
                {"footnoteContent": "ราชกิจจานุเบกษา เล่ม ๑๑๘/ตอนที่ ๑๒ ก/หน้า ๓/๒ มกราคม ๒๕๔๔"}
            ],
            "timelines": timelines,
        },
    }


_ENGLISH = {
    "respHeader": {"errorCode": "SUCCESS"},
    "respBody": {
        "lawInfo": {"lawNameTh": "x"},
        "lawTranslation": {
            "lawSections": [_section(1, "4", "Section 1", "<p>Section 1. A test.</p>")],
            "footnoteList": [{"footnoteContent": "This Translation is provided by an agency."}],
        },
    },
}

_DOCS = {
    (BASE, False): _doc("พระราชบัญญัติทดสอบ พ.ศ. 2544", "4", "2001-01-01", _TIMELINES),
    (AMENDING, False): _doc("พระราชบัญญัติทดสอบ (ฉบับที่ 2) พ.ศ. 2553", "2", "2010-05-01", _TIMELINES),
    (TRANSLATED, False): _doc(
        "พระราชบัญญัติแปล พ.ศ. 2562",
        "4",
        "2019-05-22",
        [
            # The source lists one id under both its original and current kinds.
            _timeline(TRANSLATED, "1", "ข0001-1B-0001-00", "2019-05-20"),
            _timeline(TRANSLATED, "4", "ข0001-1B-0001-00", "2019-05-23", en=True),
        ],
    ),
    (TRANSLATED, True): _ENGLISH,
}


def _adapter() -> SourceAdapter:
    return SourceAdapter(kind="json_portal", name="ocs", rate_limit_per_minute=12)


def _ref(timeline_id: str) -> DocumentRef:
    return DocumentRef(
        jurisdiction_code="th",
        doctype="act",
        year=2001,
        number="ก0001-1B-0001",
        extra={"timeline_id": timeline_id},
    )


def _handler(seen: list[dict[str, Any]]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        key = (body["reqBody"]["timelineId"], body["reqBody"]["isTransEng"])
        doc = _DOCS.get(key, {"respHeader": {"errorCode": "NOT_FOUND"}, "respBody": None})
        return httpx.Response(200, json=doc)

    return handle


async def _fetch(timeline_id: str) -> tuple[Any, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(seen)))
    try:
        acquirer = OcsAcquirer(_adapter(), client=client, title_rule=RULE)
        return await acquirer.fetch(_ref(timeline_id)), seen
    finally:
        await client.aclose()


def test_registers_json_portal() -> None:
    assert "json_portal" in registered_kinds()


def test_default_client_is_polite() -> None:
    acquirer = OcsAcquirer(_adapter(), title_rule=RULE)
    assert isinstance(acquirer._client._transport, PoliteTransport)
    assert acquirer.politeness.rate_limit_per_minute == 12


def test_section_text_joins_split_spans_and_keeps_paragraphs() -> None:
    assert section_text("<p><span>Digital</span><span>i</span>zation</p><p>b&amp;c</p>") == (
        "Digitalization\nb&c"
    )


async def test_current_text_lands_on_the_base_work_dated_by_its_timeline() -> None:
    doc, seen = await _fetch(BASE)
    work = doc.frbr_work_uri
    assert work.startswith("/akn/th/act/2001/t-")
    assert doc.expression_uri == f"{work}/tha@2010-05-02"
    assert doc.upstream_metadata["timeline_kind"] == "current"
    # Units in their stated order, not the order the API lists them.
    assert doc.primary().content.decode().splitlines()[0] == "พระราชบัญญัติทดสอบ พ.ศ. 2544"
    assert [t["kind"] for t in doc.upstream_metadata["timelines"]] == [
        "original",
        "amending",
        "current",
    ]
    assert doc.gazette_ref is not None
    assert (doc.gazette_ref.issue, doc.gazette_ref.page) == ("118/12 ก", "3")
    # No translation flagged: one request only.
    assert len(seen) == 1


async def test_amending_act_is_its_own_work_dated_by_publication() -> None:
    base, _ = await _fetch(BASE)
    amending, _ = await _fetch(AMENDING)
    assert amending.frbr_work_uri != base.frbr_work_uri
    assert amending.frbr_work_uri.startswith("/akn/th/act/2010/t-")
    assert amending.expression_uri == f"{amending.frbr_work_uri}/tha@2010-05-01"


async def test_translation_rides_with_its_stated_provenance() -> None:
    doc, seen = await _fetch(TRANSLATED)
    assert doc.expression_uri.endswith("/tha@2019-05-23")
    english = [b for b in doc.bodies if b.role == "translation"]
    assert [(b.language, b.content.decode()) for b in english] == [("eng", "Section 1. A test.")]
    assert doc.upstream_metadata["translation_note"] == "This Translation is provided by an agency."
    assert [s["reqBody"]["isTransEng"] for s in seen] == [False, True]


async def test_unknown_timeline_raises() -> None:
    with pytest.raises(OcsItemMissing):
        await _fetch("nope")


def _search_page(page: int, pages: int, hits: list[tuple[str, int]]) -> dict[str, Any]:
    return {
        "meta": {"page": str(page), "pages": pages, "total": len(hits) * pages},
        "data": [
            {
                "lawCode": code,
                "lawNameTh": f"<mark>พระราชบัญญัติ</mark>{code}",
                "encTimelineID": f"enc-{code}",
                "year": year,
            }
            for code, year in hits
        ],
    }


async def test_discover_pages_and_filters_by_year() -> None:
    pages = {
        "1": _search_page(1, 2, [("a", 2001), ("b", 1990)]),
        "2": _search_page(2, 2, [("c", 2005)]),
    }
    forms: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        form = request.content.decode()
        forms.append(form)
        page = form.split("pagination%5Bpage%5D=")[1].split("&")[0]
        return httpx.Response(200, json=pages[page])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        acquirer = OcsAcquirer(_adapter(), client=client, title_rule=RULE)
        refs = [
            r
            async for r in acquirer.discover(
                DiscoveryQuery(doctype="act", year_from=2000, q="พระราชบัญญัติ")
            )
        ]
    finally:
        await client.aclose()
    assert [(r.number, r.extra["timeline_id"], r.extra["title"]) for r in refs] == [
        ("a", "enc-a", "พระราชบัญญัติa"),
        ("c", "enc-c", "พระราชบัญญัติc"),
    ]
    assert len(forms) == 2
