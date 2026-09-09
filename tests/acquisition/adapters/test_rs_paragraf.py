"""RS, ParagrafAcquirer unit tests via httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DocumentRef, get_acquirer, load_manifest
from codify.acquisition.adapters.rs.paragraf import ParagrafAcquirer, frbr_for
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter

# Synthetic minimal PDF, the adapter only checks the %PDF magic and passes the
# body through, so no real trade-secrets text is needed.
_SYNTHETIC_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _adapter() -> SourceAdapter:
    return SourceAdapter(
        kind="paragraf_propisi",
        name="paragraf",
        base_url="https://www.paragraf.rs",
        rate_limit_per_minute=30,
    )


def test_frbr_for_zakon() -> None:
    # zakon is the default act type, so no subtype segment; the canonical /act/
    # segment is present (D3: routed through build_frbr_work_uri).
    assert frbr_for("zakon", 2011, "72") == "/akn/rs/act/2011/72"
    assert frbr_for("uredba", 2019, "5") == "/akn/rs/act/uredba/2019/5"
    assert frbr_for("uredba", 2019, "5") == build_frbr_work_uri("rs", "uredba", 2019, "5")


def test_rs_corpus_manifest_loads() -> None:
    m = load_manifest("rs")
    assert m is not None
    assert m.jurisdiction_code == "rs"
    assert all(r.extra.get("slug") for r in m.refs)
    slugs = {r.extra["slug"] for r in m.refs}
    assert slugs == {f"synthetic_observatory_{i}" for i in range(1, 6)}


def test_rs_acquirer_resolves_via_registry() -> None:
    a = get_acquirer("rs")
    assert a.jurisdiction_code == "rs"
    assert a.__class__.__name__ == "ParagrafAcquirer"


@pytest.mark.asyncio
async def test_fetch_real_trade_secrets_pdf() -> None:
    pdf_bytes = _SYNTHETIC_PDF

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.endswith("/propisi_download/zakon_o_zastiti_poslovne_tajne.pdf"):
            return httpx.Response(200, content=pdf_bytes, headers={"etag": 'W/"par-72"'})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acquirer = ParagrafAcquirer(_adapter(), client=client)
    ref = DocumentRef(
        jurisdiction_code="rs",
        doctype="zakon",
        year=2011,
        number="72",
        languages=["srp"],
        extra={
            "slug": "zakon_o_zastiti_poslovne_tajne",
            "title_short": "Zakon o zaštiti poslovne tajne",
            "transposes": "32016L0943",
        },
    )
    acquired = await acquirer.fetch(ref)
    await client.aclose()

    assert acquired.frbr_work_uri == "/akn/rs/act/2011/72"
    assert acquired.bodies[0].media_type == "application/pdf"
    assert acquired.bodies[0].content[:4] == b"%PDF"
    assert acquired.bodies[0].language == "srp"
    assert acquired.citation_alt == "Zakon o zaštiti poslovne tajne"
    assert acquired.upstream_metadata["transposes"] == "32016L0943"
    assert acquired.etag == 'W/"par-72"'


@pytest.mark.asyncio
async def test_fetch_rejects_non_pdf_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("robots.txt"):
            return httpx.Response(200, text="")
        return httpx.Response(200, content=b"<html>not a pdf</html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acquirer = ParagrafAcquirer(_adapter(), client=client)
    ref = DocumentRef(
        jurisdiction_code="rs",
        doctype="zakon",
        year=2011,
        number="72",
        extra={"slug": "zakon_o_nesto"},
    )
    with pytest.raises(RuntimeError, match="non-PDF"):
        await acquirer.fetch(ref)
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_requires_slug() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    acquirer = ParagrafAcquirer(_adapter(), client=client)
    ref = DocumentRef(jurisdiction_code="rs", doctype="zakon", year=2011, number="72")
    with pytest.raises(ValueError, match="slug"):
        await acquirer.fetch(ref)
    await client.aclose()
