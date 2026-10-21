"""TH, OcsAcquirer unit tests via httpx.MockTransport, on invented laws."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from lxml import etree

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DocumentRef, registered_kinds
from codify.acquisition.adapters.th.ocs import (
    LAW_CODE_IDENTITY,
    IndexEmpty,
    IndexMoved,
    OcsAcquirer,
    OcsItemMissing,
    SourceDown,
    build_index,
    crawl,
    dump_name,
    failures_path,
    index_entry,
    law_code_number,
    section_text,
)
from codify.acquisition.adapters.th.ocs_akn import UnmappedUnit, to_akn, to_bluebell
from codify.acquisition.base import DiscoveryQuery
from codify.acquisition.politeness import PoliteTransport
from codify.frbr import TITLE_DIGEST_PREFIX, identity_from_code
from codify.jurisdictions import SourceAdapter, TitleIdentity


@pytest.fixture(autouse=True)
def _th_config(tmp_path_factory: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An invented th config, from the fictional xa one, for the AKN parse."""
    import codify.jurisdictions as jurisdictions

    root = tmp_path_factory.mktemp("jurisdictions")
    config = json.loads((jurisdictions.JURISDICTIONS_DIR / "xa" / "config.json").read_text())
    config |= {"code": "th", "languages": ["tha"], "authoritative_language": "tha"}
    config["frbr"] = {
        **config.get("frbr", {}),
        "country_code": "th",
        "uri_patterns": {"act": "/akn/th/act/{year}/{number}"},
        "calendar_conversion": {
            "kind": "buddhist",
            "epoch_year": -543,
            "month_names": [
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
            ],
            "month_day_is_gregorian": True,
            "date_cues": ["ประกาศ ณ วันที่"],
            "new_year_month": 4,
            "new_year_reform_year": 2484,
        },
    }
    config["calendar"] = "buddhist_era"
    (root / "th").mkdir()
    (root / "th" / "config.json").write_text(json.dumps(config))
    clear = getattr(jurisdictions.try_load_config, "cache_clear", lambda: None)
    monkeypatch.setattr(jurisdictions, "JURISDICTIONS_DIR", root)
    clear()
    yield
    clear()


RULE = TitleIdentity(
    strip_prefixes=["พระราชบัญญัติ"],
    year_particles=["พ.ศ."],
    edition_markers=["ฉบับที่"],
    consolidation_markers=["Update"],
)
BASE = "tl-base"
AMENDING = "tl-amend"
TRANSLATED = "tl-en"
TWIN = "tl-twin"
TWIN_OTHER = "tl-twin-2"
TWIN_TITLE = "พระราชบัญญัติแฝด พ.ศ. 2568"


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


_MONTHS = [
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


def _gazette_note(published: str) -> str:
    """Each invented law's own Gazette note, dated as it was published."""
    y, m, d = (int(x) for x in published.split("-"))
    return f"ราชกิจจานุเบกษา เล่ม ๑๑๘/ตอนที่ ๑๒ ก/หน้า ๓/{d} {_MONTHS[m - 1]} {y + 543}"


def _doc(
    title: str,
    type_id: str,
    published: str,
    timelines: list[dict[str, Any]],
    *,
    law: str = "ก0001-1B-0001",
) -> dict[str, Any]:
    return {
        "respHeader": {"errorCode": "SUCCESS"},
        "respBody": {
            "lawInfo": {
                "timelineTypeId": type_id,
                "timelineLawCode": f"{law}-xx",
                "lawCode": law,
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
            "footnoteList": [{"footnoteContent": _gazette_note(published)}],
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
    # Two laws one title names: the source tells them apart by law code alone.
    (TWIN, False): _doc(
        TWIN_TITLE,
        "4",
        "2025-05-15",
        [_timeline(TWIN, "4", "ร0006-2A-0036-00", "2025-05-15")],
        law="ร0006-2A-0036",
    ),
    (TWIN_OTHER, False): _doc(
        TWIN_TITLE,
        "4",
        "2025-11-28",
        [_timeline(TWIN_OTHER, "4", "ร0006-2A-0064-00", "2025-11-28")],
        law="ร0006-2A-0064",
    ),
}


def _adapter() -> SourceAdapter:
    return SourceAdapter(kind="json_portal", name="ocs", rate_limit_per_minute=12)


def _ref(timeline_id: str, **extra: str) -> DocumentRef:
    return DocumentRef(
        jurisdiction_code="th",
        doctype="act",
        year=2001,
        number="ก0001-1B-0001",
        extra={"timeline_id": timeline_id, **extra},
    )


def _handler(seen: list[dict[str, Any]]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        key = (body["reqBody"]["timelineId"], body["reqBody"]["isTransEng"])
        doc = _DOCS.get(key, {"respHeader": {"errorCode": "NOT_FOUND"}, "respBody": None})
        return httpx.Response(200, json=doc)

    return handle


async def _fetch(timeline_id: str, **extra: str) -> tuple[Any, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(seen)))
    try:
        acquirer = OcsAcquirer(_adapter(), client=client, title_rule=RULE)
        return await acquirer.fetch(_ref(timeline_id, **extra)), seen
    finally:
        await client.aclose()


def test_registers_json_portal() -> None:
    assert "json_portal" in registered_kinds()


def test_default_client_is_polite() -> None:
    acquirer = OcsAcquirer(_adapter(), title_rule=RULE)
    assert isinstance(acquirer._client._transport, PoliteTransport)
    assert acquirer.politeness.rate_limit_per_minute == 12
    sent = acquirer._client._transport.profile.user_agent
    assert sent.startswith("Codify/") and sent.endswith(
        "(+https://github.com/TBI-AI-Incubator/codify)"
    )


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
    # AKN built from the units in their stated order, not the order the API lists them.
    assert doc.primary().media_type == "application/xml"
    root = etree.fromstring(doc.primary().content)
    ns = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
    assert root.xpath("string(//a:preface/a:p[1])", namespaces=ns) == "พระราชบัญญัติทดสอบ พ.ศ. 2544"
    assert [
        (s.xpath("string(a:num)", namespaces=ns), s.xpath("string(.//a:p)", namespaces=ns))
        for s in root.xpath("//a:body/a:section", namespaces=ns)
    ] == [("๑", "ทดสอบ")]
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


def _hit(code: str, children: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
    return {
        "lawCode": code,
        "encTimelineID": f"enc-{code}",
        "lawNameTh": f"<mark>พระราชบัญญัติ</mark>{code}",
        "year": 2001,
        "state": "01",
        "lawEn": code == "a",
        "childrens": [
            {"name": kind, "items": [{"title": f"<mark>x</mark>{tid}", "encTimelineID": tid}]}
            for kind, tid in children
        ],
    }


def _checkpoint(total: int, code: str) -> str:
    return json.dumps(
        {"total": total, "per_page": 2, "pages_done": 1, "entries": [index_entry(_hit(code))]}
    )


def _catalogue(pages: dict[int, list[dict[str, Any]]], totals: dict[int, int]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        form = request.content.decode()
        assert "query%5BstateName%5D=&" in form  # every state, not only in force
        assert "query%5BonlyMatchChild%5D=0" in form  # every child, not only matches
        page = int(form.split("pagination%5Bpage%5D=")[1].split("&")[0])
        meta = {"total": totals.get(page, 3), "pages": len(pages)}
        return httpx.Response(200, json={"meta": meta, "data": pages[page]})

    return handle


_PAGES = {
    1: [
        _hit("a", (("พระราชกฤษฎีกา", "d1"), ("กฎกระทรวง", "r1"))),
        _hit("b", (("กฎกระทรวง", "r1"),)),
    ],
    2: [_hit("b"), _hit("c")],
}


async def _build(handler: Any, **kw: Any) -> dict[str, Any]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        acquirer = OcsAcquirer(_adapter(), client=client, title_rule=RULE)
        return await build_index(acquirer, per_page=2, **kw)
    finally:
        await client.aclose()


async def test_index_walks_every_page_and_counts_shared_children_once(tmp_path: Any) -> None:
    out = tmp_path / "index.json"
    payload = await _build(_catalogue(_PAGES, {}), checkpoint=out)
    assert [e["law_code"] for e in payload["entries"]] == ["a", "b", "c"]
    assert payload["entries"][0]["children"][0] == {
        "type": "พระราชกฤษฎีกา",
        "title": "xd1",
        "timeline_id": "d1",
        "date": "",
        "state": "",
        "english": False,
    }
    assert payload["stats"] == {
        "laws": 3,
        "missing": 0,
        "dropped_hits": 0,
        "children": 2,
        "child_types": {"พระราชกฤษฎีกา": 1, "กฎกระทรวง": 1},
        "states": {"01": 3},
        "english": 1,
    }
    assert payload["complete"] is True
    # The last checkpoint is the walk so far, never marked complete.
    assert json.loads(out.read_text())["complete"] is False


async def test_index_resumes_after_the_last_page_written(tmp_path: Any) -> None:
    out = tmp_path / "index.json"
    seen: list[int] = []
    inner = _catalogue(_PAGES, {})

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(int(request.content.decode().split("pagination%5Bpage%5D=")[1].split("&")[0]))
        return inner(request)

    out.write_text(_checkpoint(3, "z"))
    payload = await _build(handle, checkpoint=out, resume=True)
    # Page 1 is asked for the total, before and after; its laws come from the checkpoint.
    assert seen == [1, 2, 1]
    assert [e["law_code"] for e in payload["entries"]] == ["z", "b", "c"]


async def test_index_restarts_when_the_total_has_moved(tmp_path: Any) -> None:
    out = tmp_path / "index.json"
    out.write_text(_checkpoint(9, "z"))
    payload = await _build(_catalogue(_PAGES, {}), checkpoint=out, resume=True)
    assert [e["law_code"] for e in payload["entries"]] == ["a", "b", "c"]


async def test_index_refuses_a_catalogue_that_moves_mid_walk() -> None:
    with pytest.raises(IndexMoved):
        await _build(_catalogue(_PAGES, {2: 4}))


_INDEX = {
    "entries": [
        {"timeline_id": BASE, "children": [{"timeline_id": TRANSLATED}]},
        {"timeline_id": TRANSLATED, "children": []},
    ]
}


async def _crawl(out: Any, seen: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(seen)))
    try:
        acquirer = OcsAcquirer(_adapter(), client=client, title_rule=RULE)
        return await crawl(acquirer, _INDEX, out / "raw", **kw)
    finally:
        await client.aclose()


def _asked(seen: list[dict[str, Any]]) -> list[tuple[str, bool]]:
    return [(s["reqBody"]["timelineId"], s["reqBody"]["isTransEng"]) for s in seen]


async def test_crawl_saves_each_listed_timeline_once_and_follows_only_asked_kinds(
    tmp_path: Any,
) -> None:
    seen: list[dict[str, Any]] = []
    result = await _crawl(tmp_path, seen, kinds=frozenset({"amending"}))
    # The original (BASE-0) is a kind not asked for, so it is never fetched.
    assert _asked(seen) == [
        (BASE, False),
        (TRANSLATED, False),
        (TRANSLATED, True),
        (AMENDING, False),
    ]
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == sorted(
        [
            dump_name(BASE),
            dump_name(TRANSLATED),
            dump_name(TRANSLATED, english=True),
            dump_name(AMENDING),
        ]
    )
    assert result == {"timelines": 3, "fetched": 4, "textless": 0, "failures": {}}


async def test_crawl_resumes_without_fetching_what_is_saved(tmp_path: Any) -> None:
    await _crawl(tmp_path, [], kinds=frozenset({"amending"}))
    seen: list[dict[str, Any]] = []
    result = await _crawl(tmp_path, seen, kinds=frozenset({"amending"}))
    assert seen == []
    assert result["fetched"] == 0


async def test_crawl_records_a_missing_timeline_and_stops_on_a_failing_streak(
    tmp_path: Any,
) -> None:
    index = {"entries": [{"timeline_id": "gone-1"}, {"timeline_id": BASE}]}
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler([])))
    try:
        acquirer = OcsAcquirer(_adapter(), client=client, title_rule=RULE)
        result = await crawl(acquirer, index, tmp_path / "raw")
        assert list(result["failures"]) == [dump_name("gone-1")]
        assert (tmp_path / "raw" / dump_name(BASE)).exists()
        with pytest.raises(SourceDown):
            await crawl(
                acquirer,
                {"entries": [{"timeline_id": "gone-2"}, {"timeline_id": "gone-3"}]},
                tmp_path / "raw",
                stop_after_failures=2,
            )
    finally:
        await client.aclose()


async def test_crawl_counts_only_failures_in_a_row(tmp_path: Any) -> None:
    index = {"entries": [{"timeline_id": t} for t in ("gone-1", BASE, "gone-2", AMENDING)]}
    result = await _crawl_index(tmp_path / "raw", index, stop_after_failures=2)
    assert sorted(result["failures"]) == [dump_name("gone-1"), dump_name("gone-2")]


async def test_crawl_fetches_a_timeline_two_documents_list_once(tmp_path: Any) -> None:
    seen: list[dict[str, Any]] = []
    # BASE and AMENDING list each other; each is fetched once.
    index = {"entries": [{"timeline_id": BASE}, {"timeline_id": AMENDING}]}
    await _crawl_index(tmp_path / "raw", index, seen=seen, kinds=frozenset({"amending", "current"}))
    assert _asked(seen) == [(BASE, False), (AMENDING, False)]


async def _crawl_index(
    out: Any, index: dict[str, Any], *, seen: list[dict[str, Any]] | None = None, **kw: Any
) -> dict[str, Any]:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler([] if seen is None else seen))
    )
    try:
        acquirer = OcsAcquirer(_adapter(), client=client, title_rule=RULE)
        return await crawl(acquirer, index, out, **kw)
    finally:
        await client.aclose()


def test_dump_names_are_safe_filenames() -> None:
    assert dump_name("a/b+c==") == "a_b-c==.json"
    assert dump_name("a/b", english=True) == "a_b.en.json"


def _unit(seq: int, kind: str, html: str, label: str = "") -> dict[str, Any]:
    return {"sectionSeq": seq, "sectionTypeId": kind, "sectionContent": html, "sectionLabel": label}


def test_units_nest_under_their_groups_and_a_trailing_law_is_annexed() -> None:
    units = [
        _unit(1, "1", "<p>พระราชบัญญัติทดสอบ</p>"),
        _unit(2, "3", "<p>โดยที่สมควร</p>"),
        _unit(3, "8", "<p>หมวด ๑</p><p>บททั่วไป</p>"),
        _unit(4, "4", "<p>มาตรา</p><p>๑ ข้อความ **ตัวหนา**</p>"),
        _unit(5, "9", "<p>ส่วนที่ ๑ การยื่น</p>"),
        _unit(6, "4", "<p>มาตรา ๒/๑ ข้อความ</p><p>(๑) รายการ</p>"),
        _unit(7, "8", "<p>หมวด ๒ บทสุดท้าย</p>"),
        _unit(8, "13", "<p>บทเฉพาะกาล</p>"),
        _unit(9, "4", "<p>มาตรา ๓ ทวิ ข้อความ</p>", "มาตรา 3 ทวิ"),
        _unit(10, "14", "<p>ผู้รับสนองพระบรมราชโองการ</p>"),
        _unit(11, "1", "<p>พระราชบัญญัติทดสอบ (ฉบับที่ ๒)</p>"),
        _unit(12, "16", "<p>บัญชีอัตรา</p><p>ร้อยละ ๕</p>"),
    ]
    assert to_bluebell(units).splitlines() == [
        "PREFACE",
        "  พระราชบัญญัติทดสอบ",
        "PREAMBLE",
        "  โดยที่สมควร",
        "BODY",
        "  CHAPTER ๑ - บททั่วไป",
        "    SEC ๑",
        "      ข้อความ \\*\\*ตัวหนา\\*\\*",
        "    DIVISION ๑ - การยื่น",
        "      SEC ๒/๑",
        "        ข้อความ",
        "        (๑) รายการ",
        "  CHAPTER ๒ - บทสุดท้าย",
        "    CROSSHEADING บทเฉพาะกาล",
        "    SEC ๓ ทวิ",
        "      ข้อความ",
        "CONCLUSIONS",
        "  ผู้รับสนองพระบรมราชโองการ",
        "",
        "SCHEDULE พระราชบัญญัติทดสอบ (ฉบับที่ ๒)",
        "  บัญชีอัตรา",
        "  ร้อยละ ๕",
    ]
    root = _akn(units)
    ns = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
    assert root.xpath("//a:hcontainer", namespaces=ns) == []
    assert [
        s.xpath("string(a:num)", namespaces=ns) for s in root.xpath("//a:section", namespaces=ns)
    ] == [
        "๑",
        "๒/๑",
        "๓ ทวิ",
    ]
    assert "ตัวหนา" in root.xpath("string(//a:section[1])", namespaces=ns)


def test_an_unknown_unit_kind_is_refused_not_dropped() -> None:
    with pytest.raises(UnmappedUnit, match="unit type 99"):
        to_bluebell([_unit(1, "99", "<p>x</p>"), _unit(2, "4", "<p>มาตรา ๑ x</p>")])


async def test_configured_acquirer_reads_a_saved_crawl_before_the_network(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACQUISITION_INDEX_DIR", str(tmp_path))
    (tmp_path / "th" / "raw").mkdir(parents=True)
    (tmp_path / "th" / "raw" / dump_name(BASE)).write_text(
        json.dumps(_DOCS[(BASE, False)]["respBody"])
    )
    acquirer = OcsAcquirer.from_config("th", _adapter())
    seen: list[dict[str, Any]] = []
    acquirer._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(seen)))
    acquirer._title_rule = RULE
    try:
        doc = await acquirer.fetch(_ref(BASE))
        assert doc.expression_uri.endswith("/tha@2010-05-02")
        assert seen == []
        # A timeline the crawl did not save is still fetched.
        await acquirer.fetch(_ref(AMENDING))
        assert _asked(seen) == [(AMENDING, False)]
    finally:
        await acquirer._client.aclose()


_AKN = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}


def _akn(units: list[dict[str, Any]], **kw: Any) -> Any:
    xml = to_akn(
        units,
        doctype="act",
        number="t-1",
        work="/akn/th/act/2001/t-1",
        published="2001-01-02",
        expression="/akn/th/act/2001/t-1/tha@2010-05-02",
        as_of="2010-05-02",
        **kw,
    )
    return etree.fromstring(xml.encode())


def _x(root: Any, path: str) -> list[str]:
    return [" ".join(str(v).split()) for v in root.xpath(path, namespaces=_AKN)]


def test_an_appended_amending_law_is_an_attachment_not_body_sections() -> None:
    units = [
        _unit(1, "1", "<p>พระราชบัญญัติหลัก</p>"),
        _unit(2, "4", "<p>มาตรา ๑ ข้อความหลัก</p>"),
        _unit(3, "1", "<p>พระราชบัญญัติหลัก (ฉบับที่ ๒)</p>"),
        _unit(4, "4", "<p>มาตรา ๑ ให้ใช้บังคับ</p>"),
        _unit(5, "15", "<p>หมายเหตุ เหตุผล</p>"),
    ]
    root = _akn(units)
    assert _x(root, "//a:body//a:section/a:num/text()") == ["๑"]
    assert _x(root, "//a:attachments//a:heading//text()") == ["พระราชบัญญัติหลัก (ฉบับที่ ๒)"]
    assert _x(root, "//a:attachments//a:p//text()") == ["มาตรา ๑ ให้ใช้บังคับ", "หมายเหตุ เหตุผล"]


def test_the_segment_titled_as_the_law_is_its_body() -> None:
    # A code follows the Act that enacts it; the code is the law.
    units = [
        _unit(1, "1", "<p>พระราชบัญญัติให้ใช้ประมวล</p>"),
        _unit(2, "4", "<p>มาตรา ๑ ให้ใช้ประมวลท้ายนี้</p>"),
        _unit(3, "1", "<p>ประมวล</p><p>ทดสอบ</p>"),
        _unit(4, "4", "<p>มาตรา ๑ บทนิยาม</p>"),
        _unit(5, "4", "<p>มาตรา ๒ บทลงโทษ</p>"),
    ]
    root = _akn(units, title="ประมวลทดสอบ")
    assert _x(root, "//a:preface//a:p/text()") == ["ประมวล", "ทดสอบ"]
    assert _x(root, "//a:body//a:section/a:num/text()") == ["๑", "๒"]
    assert _x(root, "//a:attachments//a:heading//text()") == ["พระราชบัญญัติให้ใช้ประมวล"]


def test_numbers_take_an_ordinal_only_where_the_label_has_it() -> None:
    units = [
        _unit(1, "4", "<p>มาตรา ๓ เอกาทศ ความ</p>", "มาตรา 3 เอกาทศ"),
        _unit(2, "4", "<p>มาตรา ๓ สัตตรส ความ</p>", "มาตรา 3 สัตตรส"),
        # ฉลาก begins with the ordinal ฉ; the label says this clause has none.
        _unit(3, "10", "<p>ข้อ ๔ ฉลากต้องแสดง</p>", "ข้อ 4"),
    ]
    root = _akn(units)
    assert _x(root, "//a:section/a:num/text()") == ["๓ เอกาทศ", "๓ สัตตรส", "๔"]
    assert _x(root, "//a:section/a:content/a:p/text()") == ["ความ", "ความ", "ฉลากต้องแสดง"]


def test_parts_prose_images_and_inner_schedules_are_placed() -> None:
    units = [
        _unit(1, "6", "<p>ภาค ๑</p><p>ทั่วไป</p>"),
        _unit(2, "18", "<p>ให้กำหนดชนิดเหรียญ</p>"),
        _unit(3, "16", "<p>การอุทธรณ์</p>"),
        _unit(4, "4", '<p>มาตรา ๑ ตามแบบ</p><p><img src="data:image/png;base64,AAAA"></p>'),
        _unit(5, "20", "<p>แบบท้าย</p>"),
    ]
    root = _akn(units)
    assert _x(root, "//a:part/a:heading/text()") == ["ทั่วไป"]
    assert "ให้กำหนดชนิดเหรียญ" in _x(root, "//a:part//a:p/text()")
    # A schedule-typed unit followed by provisions heads them; a trailing one is an annex.
    assert _x(root, "//a:part//a:crossHeading//text()") == ["การอุทธรณ์"]
    assert root.xpath("//a:section//a:img/@src", namespaces=_AKN) == ["data:image/png;base64,AAAA"]
    assert _x(root, "//a:attachments//a:heading//text()") == ["แบบท้าย"]
    assert "BODY" not in _x(root, "//a:attachments//text()")


def test_runs_of_marker_characters_stay_text() -> None:
    root = _akn([_unit(1, "4", "<p>มาตรา ๑ ชื่อ _____ ก *** ข /// ค {{x}} [[y]]</p>")])
    assert root.xpath("//a:b|//a:i|//a:u|//a:img|//a:ref", namespaces=_AKN) == []
    assert _x(root, "//a:section//a:p/text()") == ["ชื่อ _____ ก *** ข /// ค {{x}} [[y]]"]


def test_the_akn_is_dated_as_the_work_and_expression_it_names() -> None:
    root = _akn([_unit(1, "4", "<p>มาตรา ๑ ก</p>")])
    ident = "//a:meta/a:identification"
    assert _x(root, f"{ident}/a:FRBRWork/a:FRBRdate/@date") == ["2001-01-02"]
    assert _x(root, f"{ident}/a:FRBRExpression/a:FRBRdate/@date") == ["2010-05-02"]
    assert _x(root, f"{ident}/a:FRBRExpression/a:FRBRthis/@value") == [
        "/akn/th/act/2001/t-1/tha@2010-05-02"
    ]


def test_enabling_words_are_the_body_of_a_decree_with_nothing_else() -> None:
    units = [_unit(1, "1", "<p>พระราชกฤษฎีกาเรียกประชุม</p>"), _unit(2, "17", "<p>ให้เรียกประชุม</p>")]
    assert _x(_akn(units), "//a:body//a:p/text()") == ["ให้เรียกประชุม"]
    announced = [
        _unit(1, "1", "<p>ประกาศคณะ</p>"),
        _unit(2, "3", "<p>ให้ยกเลิกการห้าม</p>"),
        _unit(3, "3", "<p>ตั้งแต่บัดนี้</p>"),
    ]
    assert _x(_akn(announced), "//a:body//a:p/text()") == ["ให้ยกเลิกการห้าม", "ตั้งแต่บัดนี้"]
    # With enabling words, those are the body and the preamble stays a preamble.
    decree = [
        _unit(1, "1", "<p>พระราชกฤษฎีกา</p>"),
        _unit(2, "3", "<p>มีพระบรมราชโองการ</p>"),
        _unit(3, "17", "<p>อาศัยอำนาจ</p>"),
        _unit(4, "17", "<p>ให้เรียกประชุม</p>"),
    ]
    root = _akn(decree)
    assert _x(root, "//a:body//a:p/text()") == ["อาศัยอำนาจ", "ให้เรียกประชุม"]
    assert _x(root, "//a:preamble//a:p/text()") == ["มีพระบรมราชโองการ"]


def test_an_unnumbered_provision_is_prose_and_a_labelled_one_takes_its_label() -> None:
    units = [
        _unit(1, "4", "<p>ให้กำหนดชนิด</p>", "มาตรา"),
        _unit(2, "4", "<p>ความที่สอง</p>", "มาตรา 2"),
    ]
    root = _akn(units)
    assert _x(root, "//a:body/a:hcontainer//a:p/text()") == ["ให้กำหนดชนิด"]
    assert _x(root, "//a:section/a:num/text()") == ["2"]


def test_a_body_with_no_operative_text_is_refused() -> None:
    with pytest.raises(UnmappedUnit, match="no operative text"):
        to_bluebell([_unit(1, "1", "<p>ชื่อ</p>"), _unit(2, "14", "<p>ผู้รับสนอง</p>")])


def test_only_the_laws_own_gazette_note_is_read() -> None:
    from codify.acquisition.adapters.th.ocs import gazette_ref

    notes = [
        {"footnoteContent": "ราชกิจจานุเบกษา เล่ม ๙๒/ตอนที่ ๒๒/ฉบับพิเศษ หน้า ๑/๑๓ กุมภาพันธ์ ๒๕๑๘"},
        {"footnoteContent": "ราชกิจจานุเบกษา เล่ม ๑๐๙/ตอนที่ ๑๐/หน้า ๑/๑ มกราคม ๒๕๓๕"},
    ]
    ref = gazette_ref(notes, "1975-02-13")
    assert ref is not None and (ref.issue, ref.page) == ("92/22", "1")
    # A note in a form not read gives no reference, never a later citation's.
    unread = [{"footnoteContent": "ราชกิจจานุเบกษา ฉบับที่ไม่รู้จัก"}, notes[1]]
    assert gazette_ref(unread, "1975-02-13") is None


async def test_index_refuses_a_search_that_answers_no_catalogue() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with pytest.raises(IndexEmpty):
        await _build(handle)


async def test_index_refuses_a_total_that_moved_by_the_end_and_flags_a_short_walk() -> None:
    calls: list[int] = []
    inner = _catalogue(_PAGES, {})

    def moved_at_end(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        resp = inner(request)
        if len(calls) == 3:
            return httpx.Response(200, json={"meta": {"total": 4, "pages": 2}, "data": []})
        return resp

    with pytest.raises(IndexMoved):
        await _build(moved_at_end)
    short = await _build(_catalogue({1: [_hit("a")], 2: [_hit("b")]}, {}))
    assert (short["complete"], short["stats"]["missing"]) == (False, 1)


async def test_index_counts_dropped_hits_and_merges_a_law_listed_twice() -> None:
    nameless = _hit("x")
    nameless["lawCode"] = ""
    pages = {
        1: [_hit("a", (("กฎกระทรวง", "r1"),)), nameless],
        2: [_hit("a", (("กฎกระทรวง", "r2"),)), _hit("c")],
    }
    payload = await _build(_catalogue(pages, {}))
    assert payload["stats"]["dropped_hits"] == 1
    assert [c["timeline_id"] for c in payload["entries"][0]["children"]] == ["r1", "r2"]


async def test_crawl_writes_its_failures_even_when_it_stops(tmp_path: Any) -> None:
    out = tmp_path / "raw"
    index = {"entries": [{"timeline_id": "gone-1"}, {"timeline_id": "gone-2"}]}
    with pytest.raises(SourceDown):
        await _crawl_index(out, index, stop_after_failures=2)
    assert sorted(json.loads(failures_path(out).read_text())) == [
        dump_name("gone-1"),
        dump_name("gone-2"),
    ]
    assert not (out / "failures.json").exists()


async def test_a_resumed_crawl_counts_saved_files_as_success(tmp_path: Any) -> None:
    out = tmp_path / "raw"
    index = {
        "entries": [{"timeline_id": t} for t in ("gone-1", BASE, "gone-2", AMENDING, "gone-3")]
    }
    await _crawl_index(out, index, stop_after_failures=2)
    # On resume the saved files sit between the same failures; none is a streak.
    result = await _crawl_index(out, index, stop_after_failures=2)
    assert len(result["failures"]) == 3


async def test_a_crawl_refetches_a_saved_file_it_cannot_read(tmp_path: Any) -> None:
    out = tmp_path / "raw"
    out.mkdir()
    (out / dump_name(BASE)).write_text('{"lawInfo": ')
    seen: list[dict[str, Any]] = []
    await _crawl_index(out, {"entries": [{"timeline_id": BASE}]}, seen=seen)
    assert _asked(seen) == [(BASE, False)]
    assert json.loads((out / dump_name(BASE)).read_text())["lawInfo"]


async def test_a_failed_translation_keeps_the_thai_text(tmp_path: Any) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["reqBody"]["isTransEng"]:
            return httpx.Response(503)
        return httpx.Response(200, json=_DOCS[(TRANSLATED, False)])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        doc = await OcsAcquirer(_adapter(), client=client, title_rule=RULE).fetch(_ref(TRANSLATED))
    finally:
        await client.aclose()
    assert [b.role for b in doc.bodies if b.role == "translation"] == []
    assert doc.primary().language == "tha"
    assert "503" in doc.upstream_metadata["translation_error"]


async def test_a_timeline_of_an_unread_kind_is_refused() -> None:
    doc = json.loads(json.dumps(_DOCS[(BASE, False)]))
    doc["respBody"]["lawInfo"]["timelineTypeId"] = "5"

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=doc)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(OcsItemMissing, match="unread kind"):
            await OcsAcquirer(_adapter(), client=client, title_rule=RULE).fetch(_ref(BASE))
    finally:
        await client.aclose()


async def test_a_fetched_expression_is_dated_in_its_own_akn() -> None:
    doc, _ = await _fetch(AMENDING)
    root = etree.fromstring(doc.primary().content)
    ident = "//a:meta/a:identification"
    assert _x(root, f"{ident}/a:FRBRExpression/a:FRBRdate/@date") == ["2010-05-01"]
    assert _x(root, f"{ident}/a:FRBRExpression/a:FRBRthis/@value") == [doc.expression_uri]


def test_a_configured_client_keeps_the_acquirers_pace_and_one_connection() -> None:
    bare = SourceAdapter(kind="json_portal", name="ocs")
    profile = OcsAcquirer(bare, title_rule=RULE)._client._transport.profile
    assert (profile.rate_limit_per_minute, profile.concurrency) == (12, 1)


def test_a_record_dated_after_its_own_gazette_note_takes_the_notes_date() -> None:
    from codify.acquisition.adapters.th.ocs import publication_date

    notes = [{"footnoteContent": "ราชกิจจานุเบกษา เล่ม ๙๖/ตอนที่ ๘/ฉบับพิเศษ หน้า ๑/๒๙ มกราคม ๒๕๒๒"}]
    assert publication_date({"publishDateAd": "1991-02-28"}, notes) == "1979-01-29"
    # A note dated after the record, or one not read, leaves the record's date.
    assert publication_date({"publishDateAd": "1978-12-01"}, notes) == "1978-12-01"
    assert publication_date({"publishDateAd": "1991-02-28"}, [{"footnoteContent": "อื่น"}]) == (
        "1991-02-28"
    )


def test_the_law_is_found_by_its_title_in_either_digit_script() -> None:
    units = [
        _unit(1, "1", "<p>พระราชบัญญัติให้ใช้ประมวล</p>"),
        _unit(2, "4", "<p>มาตรา ๑ ให้ใช้</p>"),
        _unit(3, "1", "<p>ประมวลทดสอบ พ.ศ. ๒๕๔๔</p>"),
        _unit(4, "4", "<p>มาตรา ๑ บทนิยาม</p>"),
    ]
    root = _akn(units, title="ประมวลทดสอบ พ.ศ. 2544")
    assert _x(root, "//a:body//a:section//a:p/text()") == ["บทนิยาม"]


def test_a_provision_keeps_its_images_where_they_stand() -> None:
    html = '<p>มาตรา</p><p>๑ ก่อนภาพ</p><p><img src="data:image/png;base64,AA"></p><p>หลังภาพ</p>'
    root = _akn([_unit(1, "4", html, "มาตรา 1")])
    content = root.xpath("//a:section/a:content/*", namespaces=_AKN)
    assert [
        ("img" if c.xpath(".//a:img", namespaces=_AKN) else c.xpath("string(.)", namespaces=_AKN))
        for c in content
    ] == [
        "ก่อนภาพ",
        "img",
        "หลังภาพ",
    ]
    assert _x(root, "//a:section/a:num/text()") == ["๑"]


async def test_fetch_keeps_the_source_document_as_provenance() -> None:
    doc, _ = await _fetch(BASE)
    cards = [b for b in doc.bodies if b.role == "card"]
    assert [b.media_type for b in cards] == ["application/json"]
    assert json.loads(cards[0].content) == _DOCS[(BASE, False)]["respBody"]


def test_a_pointer_to_the_gazette_site_is_no_text() -> None:
    from codify.acquisition.adapters.th.ocs import holds_text

    stub = {
        "lawSections": [
            _unit(1, "1", "<p>พระราชบัญญัติเก่า (ยกเลิก)</p>"),
            _unit(2, "16", "<p>โปรดดูรายละเอียดที่ https://ratchakitcha.soc.go.th/</p>"),
        ]
    }
    assert not holds_text(stub)
    assert not holds_text({"lawSections": []})
    empty = {"lawSections": [_unit(1, "1", "<p>ชื่อ</p>"), _unit(2, "4", "<p><span> </span></p>")]}
    assert not holds_text(empty)
    assert holds_text(
        {"lawSections": [_unit(1, "20", '<p><img src="data:image/png;base64,AA"></p>')]}
    )
    assert holds_text({"lawSections": [_unit(1, "4", "<p>มาตรา ๑ ก</p>")]})


async def test_fetch_dates_the_work_by_the_laws_own_gazette_note() -> None:
    doc = json.loads(json.dumps(_DOCS[(BASE, False)]))
    # The record carries a later act's date; the law's own note does not.
    doc["respBody"]["lawInfo"]["publishDateAd"] = "2015-06-01"

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=doc)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        got = await OcsAcquirer(_adapter(), client=client, title_rule=RULE).fetch(_ref(BASE))
    finally:
        await client.aclose()
    assert got.frbr_work_uri.startswith("/akn/th/act/2001/t-")


async def test_index_walks_again_from_a_checkpoint_that_ended_short(tmp_path: Any) -> None:
    out = tmp_path / "index.json"
    short = {"total": 3, "per_page": 2, "pages_done": 2, "entries": [index_entry(_hit("a"))]}
    out.write_text(json.dumps(short))
    payload = await _build(_catalogue(_PAGES, {}), checkpoint=out, resume=True)
    assert (payload["complete"], [e["law_code"] for e in payload["entries"]]) == (
        True,
        ["a", "b", "c"],
    )


async def test_a_resumed_index_keeps_the_hits_it_dropped(tmp_path: Any) -> None:
    out = tmp_path / "index.json"
    prior = {
        "total": 3,
        "per_page": 2,
        "pages_done": 1,
        "entries": [index_entry(_hit("a"))],
        "stats": {"dropped_hits": 2},
    }
    out.write_text(json.dumps(prior))
    payload = await _build(_catalogue(_PAGES, {}), checkpoint=out, resume=True)
    assert payload["stats"]["dropped_hits"] == 2


def test_an_image_is_kept_however_its_source_is_quoted() -> None:
    html = "<p>มาตรา ๑ ตามแบบ</p><p><img alt='x' src = 'data:image/png;base64,BB'></p>"
    root = _akn([_unit(1, "4", html, "มาตรา 1")])
    assert root.xpath("//a:section//a:img/@src", namespaces=_AKN) == ["data:image/png;base64,BB"]


def test_a_law_without_sections_is_still_split_from_one_appended_after_it() -> None:
    for kind in ("17", "3"):
        units = [
            _unit(1, "1", "<p>ประกาศหลัก</p>"),
            _unit(2, kind, "<p>ให้ใช้ความนี้</p>"),
            _unit(3, "19", "<p>ประกาศ ณ วันที่</p>"),
            _unit(4, "1", "<p>ประกาศแก้ไข</p>"),
            _unit(5, "4", "<p>มาตรา ๑ แก้ไข</p>"),
        ]
        root = _akn(units)
        assert _x(root, "//a:body//a:p/text()") == ["ให้ใช้ความนี้"], kind
        assert _x(root, "//a:attachments//a:heading//text()") == ["ประกาศแก้ไข"], kind
        assert root.xpath("//a:body//a:section", namespaces=_AKN) == [], kind


def test_an_empty_provision_does_not_hide_a_preamble_only_body() -> None:
    units = [
        _unit(1, "1", "<p>ประกาศคณะ</p>"),
        _unit(2, "3", "<p>ให้ยกเลิก</p>"),
        _unit(3, "4", "<p><span> </span></p>", "มาตรา 1"),
    ]
    assert _x(_akn(units), "//a:body//a:p/text()") == ["ให้ยกเลิก"]


def test_the_failures_report_sits_beside_a_relative_crawl_dir(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert failures_path(Path(".")) == tmp_path.resolve().parent / f"{tmp_path.name}-failures.json"


def test_a_range_repealed_together_stays_one_line_not_one_section() -> None:
    units = [
        _unit(1, "4", "<p>มาตรา ๓ ก</p>", "มาตรา 3"),
        _unit(2, "4", "<p>(ยกเลิก)</p>", "มาตรา 4 ถึง 8"),
        _unit(3, "4", "<p>มาตรา ๙ ถึง มาตรา ๑๒ (ยกเลิก)</p>", "มาตรา 9 ถึง มาตรา 12"),
    ]
    root = _akn(units)
    assert _x(root, "//a:section/a:num/text()") == ["๓"]
    assert _x(root, "//a:body/a:hcontainer//a:p/text()") == [
        "มาตรา 4 ถึง 8",
        "(ยกเลิก)",
        "มาตรา ๙ ถึง มาตรา ๑๒ (ยกเลิก)",
    ]


def test_every_component_names_the_work_and_expression() -> None:
    units = [
        _unit(1, "4", "<p>มาตรา ๑ ก</p>"),
        _unit(2, "1", "<p>พระราชบัญญัติแก้ไข</p>"),
        _unit(3, "4", "<p>มาตรา ๑ ข</p>"),
    ]
    root = _akn(units)
    works = _x(root, "//a:FRBRWork/a:FRBRthis/@value")
    expressions = _x(root, "//a:FRBRExpression/a:FRBRthis/@value")
    manifestations = _x(root, "//a:FRBRManifestation/a:FRBRthis/@value")
    assert len(works) > 1, "the attachment carries its own identification"
    assert all(w.startswith("/akn/th/act/2001/t-1") for w in works)
    assert all(e.startswith("/akn/th/act/2001/t-1/tha@2010-05-02") for e in expressions)
    assert all(m.startswith("/akn/th/act/2001/t-1/tha@2010-05-02") for m in manifestations)
    assert set(_x(root, "//a:FRBRExpression/a:FRBRdate/@date")) == {"2010-05-02"}
    assert set(_x(root, "/a:akomaNtoso/*/a:meta/a:identification/a:FRBRWork/a:FRBRdate/@date")) == {
        "2001-01-02"
    }


async def test_laws_one_title_names_are_two_works_by_their_law_codes() -> None:
    by_title = {(await _fetch(t))[0].frbr_work_uri for t in (TWIN, TWIN_OTHER)}
    assert len(by_title) == 1
    first, _ = await _fetch(TWIN, identity=LAW_CODE_IDENTITY)
    second, _ = await _fetch(TWIN_OTHER, identity=LAW_CODE_IDENTITY)
    assert first.frbr_work_uri == f"/akn/th/act/2025/{identity_from_code('ร0006-2A-0036')}"
    assert second.frbr_work_uri == f"/akn/th/act/2025/{identity_from_code('ร0006-2A-0064')}"
    assert first.frbr_work_uri != second.frbr_work_uri
    assert first.expression_uri == f"{first.frbr_work_uri}/tha@2025-05-15"
    root = etree.fromstring(first.primary().content)
    assert root.xpath("string(//a:FRBRWork/a:FRBRuri/@value)", namespaces=_AKN) == (
        first.frbr_work_uri
    )


async def test_a_law_not_asked_for_by_code_keeps_its_title_uri() -> None:
    """The URI this law took before law-code identities existed."""
    doc, _ = await _fetch(BASE)
    assert doc.frbr_work_uri == "/akn/th/act/2001/t-907299549cfc"


async def test_an_amending_text_asked_for_by_code_takes_its_own_timelines_code() -> None:
    """Its text is another act's, so the law's own code would file it as the law's text."""
    base, _ = await _fetch(BASE, identity=LAW_CODE_IDENTITY)
    amending, _ = await _fetch(AMENDING, identity=LAW_CODE_IDENTITY)
    assert base.frbr_work_uri == f"/akn/th/act/2001/{identity_from_code('ก0001-1B-0001')}"
    assert amending.frbr_work_uri == (f"/akn/th/act/2010/{identity_from_code('ก0001-1B-0001-xx')}")


async def test_an_identity_the_acquirer_does_not_know_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown identity 'lawcode'"):
        await _fetch(TWIN, identity="lawcode")


def test_a_law_code_number_is_a_digest_of_the_code_however_its_digits_are_written() -> None:
    number = law_code_number({"timelineTypeId": "4", "lawCode": "ร0006-2A-0036"})
    assert re.fullmatch(r"l-[0-9a-f]{12}", number)
    assert number == law_code_number({"timelineTypeId": "4", "lawCode": " ร๐๐๐๖-2A-๐๐๓๖ "})
    assert number != law_code_number({"timelineTypeId": "4", "lawCode": "ร0006-2A-0064"})
    assert not number.startswith(TITLE_DIGEST_PREFIX)


def test_a_document_naming_no_law_code_has_no_law_code_number() -> None:
    with pytest.raises(OcsItemMissing, match="names no law code"):
        law_code_number({"timelineId": "x", "timelineTypeId": "4", "lawCode": " "})
    with pytest.raises(OcsItemMissing, match="names no law code"):
        law_code_number({"timelineId": "x", "timelineTypeId": "2", "lawCode": "ก0001-1B-0001"})
