"""AL, QbzAcquirer unit tests via httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DocumentRef, get_acquirer, load_manifest
from codify.acquisition.adapters.al.qbz import QbzAcquirer, frbr_for
from codify.jurisdictions import SourceAdapter


def _adapter() -> SourceAdapter:
    return SourceAdapter(
        kind="pdf_gazette",
        name="qbz",
        base_url="https://qbz.gov.al",
        rate_limit_per_minute=6,
    )


def test_frbr_for_simple() -> None:
    assert frbr_for(2020, "162") == "/akn/al/act/ligj/2020/162"


def test_al_corpus_manifest_loads() -> None:
    m = load_manifest("al")
    assert m is not None
    assert m.jurisdiction_code == "al"
    assert {(r.year, r.number) for r in m.refs} == {(2042, str(i)) for i in range(1, 6)}


def test_al_acquirer_resolves_via_registry() -> None:
    a = get_acquirer("al")
    assert a.jurisdiction_code == "al"
    assert a.__class__.__name__ == "QbzAcquirer"


# Alfresco search response shapes, these mirror what qbz.gov.al's
# CMS returns, trimmed to the fields _resolve_node + fetch actually read.
def _alfresco_hits(*entries: dict) -> dict:
    return {"list": {"entries": [{"entry": e} for e in entries]}}


def _qbz_mock(
    *,
    name_hits: list[dict],
    rendition_hits: list[dict],
    content_bytes: bytes = b"%PDF-1.4\n%qbz\n",
    content_headers: dict[str, str] | None = None,
) -> httpx.MockTransport:
    """Build a MockTransport for QBZ's two-step search → content flow.

    First POST to /search returns name_hits (used to discover the dated
    filename prefix); second POST returns rendition_hits (PDF + others);
    GET /nodes/{id}/content returns the body bytes.
    """
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/search/versions/1/search" in str(request.url):
            payload = posts.pop(0) if posts else {"list": {"entries": []}}
            return httpx.Response(200, json=payload)
        if request.method == "GET" and "/nodes/" in str(request.url):
            return httpx.Response(200, content=content_bytes, headers=content_headers or {})
        return httpx.Response(404)

    posts.append(_alfresco_hits(*name_hits))
    posts.append(_alfresco_hits(*rendition_hits))
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_returns_pdf_body_with_etag() -> None:
    pdf_bytes = b"%PDF-1.4\n%test pdf\n"
    name_hits = [{"id": "n-162", "name": "ligj-2020-12-22-162"}]
    rendition_hits = [
        {
            "id": "r-162-pdf",
            "name": "ligj-2020-12-22-162.pdf",
            "modifiedAt": "2020-12-22T09:00:00Z",
            "content": {"mimeType": "application/pdf"},
        },
    ]
    client = httpx.AsyncClient(
        transport=_qbz_mock(
            name_hits=name_hits,
            rendition_hits=rendition_hits,
            content_bytes=pdf_bytes,
            content_headers={
                "etag": 'W/"qbz-162"',
                "last-modified": "Tue, 22 Dec 2020 09:00:00 GMT",
            },
        )
    )
    acquirer = QbzAcquirer(_adapter(), client=client)
    ref = DocumentRef(
        jurisdiction_code="al",
        doctype="ligj",
        year=2020,
        number="162",
        languages=["sqi"],
    )
    acquired = await acquirer.fetch(ref)
    await client.aclose()

    assert acquired.frbr_work_uri == "/akn/al/act/ligj/2020/162"
    assert acquired.bodies[0].media_type == "application/pdf"
    assert acquired.bodies[0].content == pdf_bytes
    assert acquired.bodies[0].language == "sqi"
    assert acquired.etag == 'W/"qbz-162"'
    assert acquired.ocr_required is False  # 2020 ≥ 2010
    assert acquired.upstream_metadata == {
        "qbz_node_id": "r-162-pdf",
        "consolidated": False,
    }


@pytest.mark.asyncio
async def test_fetch_marks_pre_2010_as_ocr_required() -> None:
    name_hits = [{"id": "n-7895", "name": "ligj-1995-07-13-7895"}]
    rendition_hits = [
        {
            "id": "r-7895-pdf",
            "name": "ligj-1995-07-13-7895.pdf",
            "modifiedAt": "1995-07-13T00:00:00Z",
            "content": {"mimeType": "application/pdf"},
        },
    ]
    client = httpx.AsyncClient(
        transport=_qbz_mock(name_hits=name_hits, rendition_hits=rendition_hits)
    )
    acquirer = QbzAcquirer(_adapter(), client=client)
    ref = DocumentRef(jurisdiction_code="al", doctype="ligj", year=1995, number="7895")
    acquired = await acquirer.fetch(ref)
    await client.aclose()
    assert acquired.ocr_required is True


@pytest.mark.asyncio
async def test_fetch_prefers_consolidated_rendition() -> None:
    """When QBZ returns both the original and a consolidated ('përditësuar')
    PDF for the same act, the consolidated one wins, and ocr_required
    flips to False even for pre-2010 acts (consolidated text is born-digital)."""
    name_hits = [{"id": "n-7850", "name": "ligj-1994-07-27-7850"}]
    rendition_hits = [
        {
            "id": "r-7850-orig",
            "name": "ligj-1994-07-27-7850.pdf",
            "modifiedAt": "1994-07-27T00:00:00Z",
            "content": {"mimeType": "application/pdf"},
        },
        {
            "id": "r-7850-consol",
            "name": "ligj-1994-07-27-7850 i përditësuar.pdf",
            "modifiedAt": "2018-03-01T00:00:00Z",
            "content": {"mimeType": "application/pdf"},
        },
    ]
    client = httpx.AsyncClient(
        transport=_qbz_mock(name_hits=name_hits, rendition_hits=rendition_hits)
    )
    acquirer = QbzAcquirer(_adapter(), client=client)
    ref = DocumentRef(jurisdiction_code="al", doctype="ligj", year=1994, number="7850")
    acquired = await acquirer.fetch(ref)
    await client.aclose()
    assert acquired.upstream_metadata["qbz_node_id"] == "r-7850-consol"
    assert acquired.upstream_metadata["consolidated"] is True
    assert acquired.ocr_required is False


@pytest.mark.asyncio
async def test_discover_reads_config_query_and_yields_refs() -> None:
    from codify.acquisition.base import DiscoveryQuery
    from codify.jurisdictions import DiscoveryConfig

    adapter = SourceAdapter(
        kind="pdf_gazette",
        name="qbz",
        base_url="https://qbz.gov.al",
        rate_limit_per_minute=6,
        discovery=DiscoveryConfig(queries={"vendim": "cm:title:prokurim*"}, max_total=100),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "list": {
                    "entries": [
                        {
                            "entry": {
                                "name": "vendim-2021-05-19-285",
                                "id": "n1",
                                "properties": {"qbz:actTitle": "Rregullat e prokurimit"},
                            }
                        }
                    ],
                    "pagination": {"hasMoreItems": False},
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acq = QbzAcquirer(adapter, client=client)
    refs = [r async for r in acq.discover(DiscoveryQuery(doctype="vendim"))]
    assert len(refs) == 1
    assert (refs[0].doctype, refs[0].year, refs[0].number) == ("vendim", 2021, "285")
    assert refs[0].extra["title"] == "Rregullat e prokurimit"


@pytest.mark.asyncio
async def test_discover_rejects_unknown_doctype() -> None:
    from codify.acquisition.base import DiscoveryQuery
    from codify.jurisdictions import DiscoveryConfig

    adapter = SourceAdapter(
        kind="pdf_gazette",
        name="qbz",
        base_url="https://qbz.gov.al",
        discovery=DiscoveryConfig(queries={"vendim": "q"}),
    )
    acq = QbzAcquirer(
        adapter,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        ),
    )
    with pytest.raises(ValueError, match="no discovery query for doctype"):
        [r async for r in acq.discover(DiscoveryQuery(doctype="ligj"))]
