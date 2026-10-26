"""DO, GacetaAcquirer unit tests via httpx.MockTransport. Records are invented."""

from __future__ import annotations

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DiscoveryQuery, DocumentRef, get_acquirer
from codify.acquisition.adapters.dom.gaceta import GacetaAcquirer
from codify.jurisdictions import SourceAdapter

BASE = "https://gazette.invalid"
PDF = b"%PDF-1.7\n%invented issue\n"


def _item(id_: str, title: str, year: int, **over: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": id_,
        "title": title,
        "fileUrl": f"/uploads/g-{title}.pdf|/uploads/g-{title}.jpg",
        "category": "gacetas",
        "year": year,
        "month": "marzo",
        "type": "pdf",
        "status": "published",
        "createdAt": "2042-03-04T10:00:00.000Z",
        "updatedAt": "2042-03-04T10:00:00.000Z",
        "uploaderId": "u-1",
        "uploader": {
            "id": "u-1",
            "name": "Invented Person",
            "email": "x@example.invalid",
        },
    }
    return item | over


LISTING = [
    _item("a", "9001", 2040),
    _item("b", "9002", 2041),
    _item("c", "9003", 2042, fileUrl="/uploads/g-9003.pdf"),
    _item("d", "9004", 2042, status="draft"),
    _item("e", "9005", 2042, category="leyes"),
    _item("f", "9006", 2042) | {"year": None},
    _item("g", " ", 2042),
    _item("h", "9008", 2042, fileUrl="/uploads/g-9008.jpg"),
    _item("i", "9009", 2039, fileUrl="/gone/g-9009.pdf"),
]


def _client(calls: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/documents":
            return httpx.Response(200, json=LISTING)
        if request.url.path.startswith("/uploads/") and request.url.path.endswith(
            ".pdf"
        ):
            return httpx.Response(200, content=PDF, headers={"etag": '"g1"'})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _acquirer(calls: list[str]) -> GacetaAcquirer:
    return GacetaAcquirer(
        SourceAdapter(kind="pdf_gazette", name="gaceta", base_url=BASE),
        client=_client(calls),
    )


async def _discover(query: DiscoveryQuery) -> list[DocumentRef]:
    return [r async for r in _acquirer([]).discover(query)]


@pytest.mark.asyncio
async def test_discover_keeps_published_gazettes_newest_first() -> None:
    refs = await _discover(DiscoveryQuery())
    assert [(r.year, r.number) for r in refs] == [
        (2042, "9003"),
        (2041, "9002"),
        (2040, "9001"),
        (2039, "9009"),
    ]
    assert {r.doctype for r in refs} == {"gazette"}
    assert refs[0].extra == {"source_id": "c"}


@pytest.mark.asyncio
async def test_discover_filters_year_range_and_limit() -> None:
    refs = await _discover(DiscoveryQuery(year_from=2041, year_to=2041))
    assert [r.number for r in refs] == ["9002"]
    refs = await _discover(DiscoveryQuery(year_from=2041))
    assert [r.number for r in refs] == ["9003", "9002"]
    refs = await _discover(DiscoveryQuery(year_to=2041))
    assert [r.number for r in refs] == ["9002", "9001", "9009"]
    refs = await _discover(DiscoveryQuery(limit=1))
    assert [r.number for r in refs] == ["9003"]


@pytest.mark.asyncio
async def test_listing_drops_uploader_fields() -> None:
    issues = await _acquirer([]).issues()
    leaked = [
        k
        for i in issues
        for k, v in i.items()
        if "uploader" in k or "Invented" in str(v)
    ]
    assert leaked == []


@pytest.mark.asyncio
async def test_fetch_resolves_issue_to_pdf() -> None:
    calls: list[str] = []
    acquirer = _acquirer(calls)
    ref = DocumentRef(
        jurisdiction_code="do",
        doctype="gazette",
        year=2041,
        number="9002",
        languages=["spa"],
    )
    acquired = await acquirer.fetch(ref)
    await acquirer.fetch(ref)

    assert acquired.source_url == f"{BASE}/uploads/g-9002.pdf"
    assert acquired.frbr_work_uri == "/akn/do/officialgazette/2041/9002"
    body = acquired.primary()
    assert (body.role, body.media_type, body.content, body.language) == (
        "primary",
        "application/pdf",
        PDF,
        "spa",
    )
    assert acquired.gazette_ref is not None
    assert acquired.gazette_ref.model_dump() == {
        "name": "Gaceta Oficial",
        "year": 2041,
        "issue": "9002",
        "page": None,
        "date": None,
    }
    assert "uploader" not in str(acquired.upstream_metadata)
    # The listing is read once per acquirer.
    assert calls.count("/api/documents") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("number", "year"),
    [
        ("9999", 2042),
        ("9004", 2042),
        ("9005", 2042),
        ("9006", 2042),
        ("9008", 2042),
        ("9001", 2042),
    ],
    ids=["absent", "draft", "not-gazette", "no-year", "no-pdf", "wrong-year"],
)
async def test_fetch_missing_issue_is_a_permanent_miss(number: str, year: int) -> None:
    ref = DocumentRef(
        jurisdiction_code="do", doctype="gazette", year=year, number=number
    )
    calls: list[str] = []
    with pytest.raises(FileNotFoundError):
        await _acquirer(calls).fetch(ref)
    # A listing miss never reaches a PDF.
    assert calls == ["/api/documents"]


@pytest.mark.asyncio
async def test_fetch_a_listed_issue_whose_pdf_is_gone_is_a_permanent_miss() -> None:
    ref = DocumentRef(
        jurisdiction_code="do", doctype="gazette", year=2039, number="9009"
    )
    calls: list[str] = []
    with pytest.raises(FileNotFoundError):
        await _acquirer(calls).fetch(ref)
    assert calls == ["/api/documents", "/gone/g-9009.pdf"]


@pytest.mark.asyncio
async def test_discover_returns_nothing_for_another_doctype() -> None:
    calls: list[str] = []
    refs = [r async for r in _acquirer(calls).discover(DiscoveryQuery(doctype="ley"))]
    assert refs == [] and calls == []
    assert len(await _discover(DiscoveryQuery(doctype="gazette"))) == 4


def test_do_resolves_via_registry() -> None:
    assert type(get_acquirer("do")) is GacetaAcquirer
    assert get_acquirer("do").politeness.rate_limit_per_minute == 20


def test_al_still_resolves_via_registry() -> None:
    assert get_acquirer("al").__class__.__name__ == "QbzAcquirer"
