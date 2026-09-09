"""UA, ZakonRadaAcquirer unit tests via httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DocumentRef, get_acquirer, load_manifest
from codify.acquisition.adapters.ua.parsers import extract_pdf_url, extract_title
from codify.acquisition.adapters.ua.zakon_rada import ZakonRadaAcquirer, frbr_for
from codify.jurisdictions import SourceAdapter

_CARD_HTML = """<!DOCTYPE html><html><head>
<title>Про запобігання корупції | Офіційний веб-портал</title></head><body>
<h1>Закон 1700-VII</h1>
<a href="/laws/file/text/97/f456789n10.pdf">Текст PDF</a>
</body></html>"""


def _adapter() -> SourceAdapter:
    return SourceAdapter(
        kind="html_portal",
        name="zakon-rada",
        base_url="https://zakon.rada.gov.ua",
        rate_limit_per_minute=6,
    )


def test_frbr_for() -> None:
    assert frbr_for("zakon", 2014, "1700-VII") == "/akn/ua/act/2014/1700-VII"


def test_extract_title_strips_pipe_suffix() -> None:
    assert extract_title(_CARD_HTML) == "Про запобігання корупції"


def test_extract_pdf_url_resolves_relative() -> None:
    pdf = extract_pdf_url(_CARD_HTML, base_url="https://zakon.rada.gov.ua/laws/show/1700-VII")
    assert pdf == "https://zakon.rada.gov.ua/laws/file/text/97/f456789n10.pdf"


def test_extract_pdf_url_prefers_body_over_chrome_footer() -> None:
    """REV-2 item 7: site-chrome PDF in footer must lose to law-text PDF in body."""
    html = """<html><head><title>Закон 1700-VII</title></head><body>
    <header><a href="/promo/download-app.pdf">App PDF</a></header>
    <div id="article">
      <a href="/laws/file/text/97/law-text.pdf">Текст PDF</a>
    </div>
    <footer><a href="/promo/privacy.pdf">Privacy</a></footer>
    </body></html>"""
    pdf = extract_pdf_url(html, base_url="https://zakon.rada.gov.ua/laws/show/1700-VII")
    assert pdf == "https://zakon.rada.gov.ua/laws/file/text/97/law-text.pdf"


def test_extract_pdf_url_returns_none_when_ambiguous() -> None:
    """No container + multiple PDFs on the page → can't pick safely."""
    html = """<html><body>
    <a href="/promo/download-app.pdf">App</a>
    <a href="/promo/privacy.pdf">Privacy</a>
    </body></html>"""
    assert extract_pdf_url(html, base_url="https://zakon.rada.gov.ua/x") is None


def test_ua_corpus_manifest_loads() -> None:
    m = load_manifest("ua")
    assert m is not None
    assert {(r.year, r.number) for r in m.refs} == {(2042, str(i)) for i in range(1, 6)}


def test_ua_acquirer_resolves_via_registry() -> None:
    a = get_acquirer("ua")
    assert a.jurisdiction_code == "ua"
    assert a.__class__.__name__ == "ZakonRadaAcquirer"


@pytest.mark.asyncio
async def test_fetch_card_then_pdf_two_bodies() -> None:
    pdf_bytes = b"%PDF-1.4\n%rada"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url == "https://zakon.rada.gov.ua/laws/show/1700-VII":
            return httpx.Response(
                200,
                text=_CARD_HTML,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if url.endswith(".pdf"):
            return httpx.Response(200, content=pdf_bytes)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acquirer = ZakonRadaAcquirer(_adapter(), client=client)
    ref = DocumentRef(
        jurisdiction_code="ua",
        doctype="zakon",
        year=2014,
        number="1700-VII",
        languages=["ukr"],
    )
    acquired = await acquirer.fetch(ref)
    await client.aclose()

    assert acquired.frbr_work_uri == "/akn/ua/act/2014/1700-VII"
    assert len(acquired.bodies) == 2
    # The anchored HTML page is primary (feeds the rada_html parser); the
    # gazette PDF rides along as provenance.
    assert acquired.bodies[0].media_type == "text/html"
    assert acquired.bodies[0].role == "primary"
    assert acquired.bodies[1].media_type == "application/pdf"
    assert acquired.bodies[1].role == "gazette"
    assert acquired.bodies[1].content == pdf_bytes
    assert acquired.citation_alt == "Про запобігання корупції"


@pytest.mark.asyncio
async def test_fetch_card_only_when_no_pdf_link() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("robots.txt"):
            return httpx.Response(200, text="")
        return httpx.Response(200, text="<html><title>X</title><body>no pdf here</body></html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acquirer = ZakonRadaAcquirer(_adapter(), client=client)
    ref = DocumentRef(jurisdiction_code="ua", doctype="zakon", year=2014, number="1700-VII")
    acquired = await acquirer.fetch(ref)
    await client.aclose()
    assert acquired.primary().media_type == "text/html"
    assert [b.role for b in acquired.bodies] == ["primary", "alternate"]
