"""EU Cellar adapter, unit tests via httpx.MockTransport."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401  registers eurlex_cellar
from codify.acquisition import DocumentRef, get_acquirer, load_manifest
from codify.acquisition.adapters.eu.cellar import EuCellarAcquirer, celex_to_frbr
from codify.jurisdictions import SourceAdapter

# Synthetic FORMEX body, the round-trip only asserts the `<ACT` marker passes
# through the mock transport, so no real directive text is needed.
_SYNTHETIC_FORMEX = "<ACT><TITLE>synthetic</TITLE></ACT>"

_BRANCH_NOTICE = (
    '<?xml version="1.0"?>'
    "<NOTICE><WORK><URI><VALUE>https://example.test/cellar/work-1234"
    "</VALUE></URI></WORK></NOTICE>"
)


def _adapter() -> SourceAdapter:
    return SourceAdapter(
        kind="eurlex_cellar",
        name="eu-sparql",
        base_url="http://publications.europa.eu/webapi/rdf/sparql",
    )


def test_parse_300_choices_real_cellar_fixture() -> None:
    """Real Cellar 300 response (captured 2026-05-06 from CELEX 32016L0943)."""
    from codify.acquisition.adapters.eu.cellar import _parse_300_choices

    fixture = Path(__file__).parent / "fixtures" / "cellar_300_32016L0943.html"
    body = fixture.read_text(encoding="utf-8")
    pairs = _parse_300_choices(body)
    # Cellar lists DOC_1 (FORMEX) and DOC_2 (XHTML) under the manifestation;
    # both must appear, regex fallback or XML walk.
    hrefs = {h for h, _ in pairs}
    assert any("DOC_1" in h for h in hrefs)
    assert any("DOC_2" in h for h in hrefs)


def test_parse_300_choices_xml() -> None:
    """REV-2 item 6: real RDF/XML 300-Multiple-Choice listing parses cleanly."""
    from codify.acquisition.adapters.eu.cellar import _parse_300_choices

    body = (
        '<?xml version="1.0"?>\n'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<entry href="https://cellar/work/DOC_1">DOC_1.fmx4</entry>'
        '<entry href="https://cellar/work/DOC_2">DOC_2.doc.xml</entry>'
        "</rdf:RDF>"
    )
    pairs = _parse_300_choices(body)
    assert ("https://cellar/work/DOC_1", "DOC_1.fmx4") in pairs
    assert ("https://cellar/work/DOC_2", "DOC_2.doc.xml") in pairs


def test_parse_300_choices_falls_back_to_regex_on_malformed_xml() -> None:
    from codify.acquisition.adapters.eu.cellar import _parse_300_choices

    body = (
        '<choice href="https://cellar/work/DOC_1" '
        '<value rdf:resource="stream_name">DOC_1.fmx<endvalue>'
    )
    pairs = _parse_300_choices(body)
    assert pairs == [("https://cellar/work/DOC_1", "DOC_1.fmx")]


def test_celex_to_frbr_directive() -> None:
    uri, doctype = celex_to_frbr("32014L0024")
    assert doctype == "directive"
    assert uri == "/akn/eu/act/dir/2014/24"


def test_celex_to_frbr_regulation() -> None:
    uri, doctype = celex_to_frbr("32016R0679")
    assert doctype == "regulation"
    assert uri == "/akn/eu/act/reg/2016/679"


def test_celex_to_frbr_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        celex_to_frbr("not-a-celex")


def test_eu_corpus_manifest_loads() -> None:
    m = load_manifest("eu")
    assert m is not None
    assert m.jurisdiction_code == "eu"
    assert {r.extra["celex"] for r in m.refs} == {f"32042L{i:04d}" for i in range(1, 6)}


def test_eu_acquirer_resolves_via_registry() -> None:
    a = get_acquirer("eu")
    assert a.jurisdiction_code == "eu"
    assert a.__class__.__name__ == "EuCellarAcquirer"


@pytest.mark.asyncio
async def test_fetch_round_trip_via_mock() -> None:
    body_xml = _SYNTHETIC_FORMEX

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.endswith("/cellar/celex/32016L0943"):
            return httpx.Response(
                200,
                text=_BRANCH_NOTICE,
                headers={
                    "etag": 'W/"abc"',
                    "last-modified": "Wed, 01 Jan 2026 00:00:00 GMT",
                },
            )
        if url == "https://example.test/cellar/work-1234":
            return httpx.Response(
                300,
                text=(
                    '<choice href="https://example.test/cellar/work-1234/DOC_1" '
                    '<value rdf:resource="stream_name">DOC_1.fmx<endvalue>'
                ),
            )
        if url == "https://example.test/cellar/work-1234/DOC_1":
            return httpx.Response(200, text=body_xml)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SourceAdapter(
        kind="eurlex_cellar", name="eu", base_url="https://example.test/cellar/celex"
    )
    acquirer = EuCellarAcquirer(adapter, client=client)

    import codify.acquisition.adapters.eu.cellar as cellar_mod

    cellar_mod.CELLAR_BASE = "https://example.test/cellar/celex"

    ref = DocumentRef(
        jurisdiction_code="eu",
        doctype="directive",
        year=2016,
        number="943",
        languages=["eng"],
        extra={"celex": "32016L0943"},
    )
    acquired = await acquirer.fetch(ref)
    await client.aclose()

    assert acquired.frbr_work_uri == "/akn/eu/act/dir/2016/943"
    assert acquired.licence == "EU-RU/2011/833"
    assert acquired.etag == 'W/"abc"'
    assert len(acquired.bodies) == 1
    assert acquired.bodies[0].language == "eng"
    assert acquired.bodies[0].media_type == "application/xml"
    assert b"<ACT" in acquired.bodies[0].content  # FORMEX fixture


@pytest.mark.asyncio
async def test_fetch_falls_back_to_xhtml_when_formex_is_missing() -> None:
    """An older act Cellar never published as FORMEX answers 404 to fmx4; the
    XHTML manifestation is taken instead and the body says so."""
    accepts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.endswith("/cellar/celex/31978D0774"):
            return httpx.Response(200, text=_BRANCH_NOTICE)
        if url == "https://example.test/cellar/work-1234":
            accept = request.headers.get("accept", "")
            accepts.append(accept)
            if accept.startswith("application/xhtml+xml"):
                return httpx.Response(200, text="<html><body><p>Article 1</p></body></html>")
            return httpx.Response(404)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SourceAdapter(
        kind="eurlex_cellar", name="eu", base_url="https://example.test/cellar/celex"
    )
    acquirer = EuCellarAcquirer(adapter, client=client)

    import codify.acquisition.adapters.eu.cellar as cellar_mod

    cellar_mod.CELLAR_BASE = "https://example.test/cellar/celex"

    ref = DocumentRef(
        jurisdiction_code="eu",
        doctype="decision",
        year=1978,
        number="774",
        languages=["eng"],
        extra={"celex": "31978D0774"},
    )
    acquired = await acquirer.fetch(ref)
    await client.aclose()

    assert [a.split(";")[0] for a in accepts] == [
        "application/xml",
        "application/xml",
        "application/xhtml+xml, text/html",
    ]
    assert acquired.primary().media_type == "text/html"
    assert acquired.upstream_metadata["formats"] == {"eng": "html"}
    assert acquired.frbr_work_uri == "/akn/eu/act/dec/1978/774"


@pytest.mark.asyncio
async def test_an_act_with_only_an_html_manifestation_is_fetched() -> None:
    """Old acts carry an html manifestation and no xhtml: a request naming
    xhtml alone is refused, one naming text/html as well is answered."""
    accepts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.endswith("/cellar/celex/31978D0774"):
            return httpx.Response(200, text=_BRANCH_NOTICE)
        if url == "https://example.test/cellar/work-1234":
            accept = request.headers.get("accept", "")
            accepts.append(accept)
            if "text/html" in accept:
                return httpx.Response(
                    200,
                    text="<!DOCTYPE HTML PUBLIC><html><body><p>Article 1</p></body></html>",
                    headers={"content-type": "text/html;charset=UTF-8"},
                )
            return httpx.Response(404)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SourceAdapter(
        kind="eurlex_cellar", name="eu", base_url="https://example.test/cellar/celex"
    )
    acquirer = EuCellarAcquirer(adapter, client=client)

    import codify.acquisition.adapters.eu.cellar as cellar_mod

    cellar_mod.CELLAR_BASE = "https://example.test/cellar/celex"
    ref = DocumentRef(
        jurisdiction_code="eu",
        doctype="decision",
        year=1978,
        number="774",
        languages=["eng"],
        extra={"celex": "31978D0774"},
    )
    acquired = await acquirer.fetch(ref)
    await client.aclose()
    assert accepts[-1] == "application/xhtml+xml, text/html"
    assert acquired.primary().media_type == "text/html"
    assert acquired.primary().content.startswith(b"<!DOCTYPE HTML")


@pytest.mark.asyncio
async def test_xhtml_fallback_follows_a_300_in_the_same_format() -> None:
    """A 300 on the XHTML request is resolved by probing the streams as XHTML
    and taking the one that is an HTML document, not the largest FORMEX-less blob."""
    probes: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        accept = request.headers.get("accept", "")
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.endswith("/cellar/celex/31978D0774"):
            return httpx.Response(200, text=_BRANCH_NOTICE)
        if url == "https://example.test/cellar/work-1234":
            if accept.startswith("application/xhtml+xml"):
                return httpx.Response(
                    300,
                    text=(
                        '<choice href="https://example.test/cellar/work-1234/DOC_1" '
                        '<value rdf:resource="stream_name">DOC_1.xhtml<endvalue>'
                        '<choice href="https://example.test/cellar/work-1234/DOC_2" '
                        '<value rdf:resource="stream_name">DOC_2.xhtml<endvalue>'
                    ),
                )
            return httpx.Response(404)
        if url.startswith("https://example.test/cellar/work-1234/DOC_"):
            probes.append((url[-5:], accept))
            if url.endswith("DOC_1"):
                return httpx.Response(
                    200, text="<?xml version='1.0'?><DOC>" + "x" * 5000 + "</DOC>"
                )
            return httpx.Response(
                200, text="<!DOCTYPE html><html><body><p>Article 1</p></body></html>"
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SourceAdapter(
        kind="eurlex_cellar", name="eu", base_url="https://example.test/cellar/celex"
    )
    acquirer = EuCellarAcquirer(adapter, client=client)

    import codify.acquisition.adapters.eu.cellar as cellar_mod

    cellar_mod.CELLAR_BASE = "https://example.test/cellar/celex"

    ref = DocumentRef(
        jurisdiction_code="eu",
        doctype="decision",
        year=1978,
        number="774",
        languages=["eng"],
        extra={"celex": "31978D0774"},
    )
    acquired = await acquirer.fetch(ref)
    await client.aclose()

    assert all(a.startswith("application/xhtml+xml") for _, a in probes)
    assert acquired.primary().media_type == "text/html"
    assert b"<html>" in acquired.primary().content


def _cellar_client(handler):  # type: ignore[no-untyped-def]
    import codify.acquisition.adapters.eu.cellar as cellar_mod

    cellar_mod.CELLAR_BASE = "https://example.test/cellar/celex"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SourceAdapter(
        kind="eurlex_cellar", name="eu", base_url="https://example.test/cellar/celex"
    )
    return client, EuCellarAcquirer(adapter, client=client)


_OLD_REF = DocumentRef(
    jurisdiction_code="eu",
    doctype="decision",
    year=1978,
    number="774",
    languages=["eng"],
    extra={"celex": "31978D0774"},
)


@pytest.mark.asyncio
async def test_an_html_body_under_a_formex_request_is_typed_by_what_came_back() -> None:
    """Cellar answers the FORMEX request with 200 and an HTML body for an act it
    only holds as XHTML: the body is text/html and the format says so."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.endswith("/cellar/celex/31978D0774"):
            return httpx.Response(200, text=_BRANCH_NOTICE)
        if url == "https://example.test/cellar/work-1234":
            return httpx.Response(
                200,
                text="<!DOCTYPE html><html><body><p>Article 1</p></body></html>",
                headers={"content-type": "text/html; charset=utf-8"},
            )
        return httpx.Response(404)

    client, acquirer = _cellar_client(handler)
    acquired = await acquirer.fetch(_OLD_REF)
    await client.aclose()
    assert acquired.primary().media_type == "text/html"
    assert acquired.upstream_metadata["formats"] == {"eng": "html"}


@pytest.mark.asyncio
async def test_a_300_listing_a_formex_notice_and_an_xhtml_body_yields_html() -> None:
    """The captured shape for a FORMEX-less act: the FORMEX request answers 300
    with DOC_1 (the index notice) and DOC_2 (XHTML). No act root is found, the
    largest stream is the HTML one, and its content-type decides the type."""
    probes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.endswith("/cellar/celex/31978D0774"):
            return httpx.Response(200, text=_BRANCH_NOTICE)
        if url == "https://example.test/cellar/work-1234":
            return httpx.Response(
                300,
                text=(
                    '<choice href="https://example.test/cellar/work-1234/DOC_1" '
                    '<value rdf:resource="stream_name">DOC_1.fmx<endvalue>'
                    '<choice href="https://example.test/cellar/work-1234/DOC_2" '
                    '<value rdf:resource="stream_name">DOC_2.xhtml<endvalue>'
                ),
            )
        if url.endswith("/DOC_1"):
            probes.append(request.headers.get("accept", ""))
            return httpx.Response(
                200,
                text="<?xml version='1.0'?><DOC><TI>OJ notice</TI></DOC>",
                headers={"content-type": "application/xml"},
            )
        if url.endswith("/DOC_2"):
            probes.append(request.headers.get("accept", ""))
            return httpx.Response(
                200,
                text="<html><body>" + "<p>Article</p>" * 40 + "</body></html>",
                headers={"content-type": "application/xhtml+xml"},
            )
        return httpx.Response(404)

    client, acquirer = _cellar_client(handler)
    acquired = await acquirer.fetch(_OLD_REF)
    await client.aclose()
    assert probes and all(a.startswith("application/xml;type=fmx4") for a in probes)
    assert acquired.primary().media_type == "text/html"
    assert acquired.upstream_metadata["formats"] == {"eng": "html"}
