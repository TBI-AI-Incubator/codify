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


async def test_annexes_published_beside_the_act_are_kept_in_its_body() -> None:
    """DOC_3 and DOC_4 are annexes: the body is the act with both, in stream order."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.endswith("/cellar/celex/31978D0774"):
            return httpx.Response(200, text=_BRANCH_NOTICE)
        if url == "https://example.test/cellar/work-1234":
            listing = "".join(
                f'<choice href="https://example.test/cellar/work-1234/DOC_{n}" '
                f'<value rdf:resource="stream_name">DOC_{n}.fmx<endvalue>'
                for n in (1, 2, 3, 4)
            )
            return httpx.Response(300, text=listing)
        bodies = {
            "/DOC_1": "<?xml version='1.0'?><DOC><TI>OJ notice</TI></DOC>",
            "/DOC_2": "<?xml version='1.0'?><ACT><TITLE>act</TITLE></ACT>",
            "/DOC_3": "<?xml version='1.0'?><ANNEX><TITLE>Annex I</TITLE></ANNEX>",
            # No declaration: the root is still read as an annex.
            "/DOC_4": "<ANNEX><TITLE>Annex II</TITLE></ANNEX>",
        }
        for suffix, text in bodies.items():
            if url.endswith(suffix):
                return httpx.Response(200, text=text, headers={"content-type": "application/xml"})
        return httpx.Response(404)

    client, acquirer = _cellar_client(handler)
    acquired = await acquirer.fetch(_OLD_REF)
    await client.aclose()
    body = acquired.primary().content
    assert acquired.primary().media_type == "application/xml"
    assert body.count(b"<ANNEX>") == 2
    assert body.index(b"Annex I<") < body.index(b"Annex II<")
    assert b"OJ notice" not in body


def _annex_handler(failures: dict[str, int], stream: str = "/DOC_2"):  # type: ignore[no-untyped-def]
    """The act plus one annex; `stream` fails the given number of times."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.endswith("/cellar/celex/31978D0774"):
            return httpx.Response(200, text=_BRANCH_NOTICE)
        if url == "https://example.test/cellar/work-1234":
            listing = "".join(
                f'<choice href="https://example.test/cellar/work-1234/DOC_{n}" '
                f'<value rdf:resource="stream_name">DOC_{n}.fmx<endvalue>'
                for n in (1, 2)
            )
            return httpx.Response(300, text=listing)
        if url.endswith(stream) and failures["left"] > 0:
            failures["left"] -= 1
            return httpx.Response(503)
        bodies = {
            "/DOC_1": "<?xml version='1.0'?><ACT><TITLE>act</TITLE></ACT>",
            "/DOC_2": "<?xml version='1.0'?><ANNEX><TITLE>Annex I</TITLE></ANNEX>",
        }
        for suffix, text in bodies.items():
            if url.endswith(suffix):
                return httpx.Response(200, text=text, headers={"content-type": "application/xml"})
        return httpx.Response(404)

    return handler


async def test_a_stream_after_the_act_that_fails_once_is_fetched_again() -> None:
    client, acquirer = _cellar_client(_annex_handler({"left": 1}))
    acquired = await acquirer.fetch(_OLD_REF)
    await client.aclose()
    assert b"Annex I<" in acquired.primary().content


@pytest.mark.parametrize("stream", ["/DOC_1", "/DOC_2"])
async def test_a_stream_that_cannot_be_fetched_fails_the_act_rather_than_dropping(
    stream: str,
) -> None:
    client, acquirer = _cellar_client(_annex_handler({"left": 2}, stream))
    with pytest.raises(httpx.HTTPStatusError):
        await acquirer.fetch(_OLD_REF)
    await client.aclose()


# --- Consolidated texts ------------------------------------------------------

_CONS_CELEX = "02042R0001-20450301"
_CONS_REF = DocumentRef(
    jurisdiction_code="eu",
    doctype="regulation",
    year=2042,
    number="1",
    languages=["eng"],
    extra={"celex": _CONS_CELEX},
)


def _formex_zip(*members: tuple[str, bytes]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


_NOTICE = ("CL2042R0001EN0010010.0001.doc.xml", b"<?xml version='1.0'?><DOC><FMX/></DOC>")
_CONS_ACT = (
    "CL2042R0001EN0010010.0001.xml",
    b"<?xml version='1.0' encoding='utf-8'?><CONS.ACT><INFO.CONSLEG/></CONS.ACT>",
)
_IMAGE = ("L_204400007EN.000101.fmx.tif", b"II*\x00")


def test_a_consolidated_celex_maps_to_its_base_act_work() -> None:
    from codify.acquisition.adapters.eu.cellar import consolidation_date

    assert celex_to_frbr(_CONS_CELEX) == celex_to_frbr("32042R0001")
    assert consolidation_date(_CONS_CELEX) == "2045-03-01"
    assert consolidation_date("32042R0001") is None
    with pytest.raises(ValueError):
        consolidation_date("02042R0001-20451399")


def _zip_handler(seen: list[tuple[str, str]], body: bytes, status: int = 200):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        seen.append((url, request.headers.get("accept", "")))
        if url.endswith(f"/cellar/celex/{_CONS_CELEX}") and "zip" in request.headers["accept"]:
            return httpx.Response(status, content=body, headers={"etag": '"cons-1"'})
        return httpx.Response(404)

    return handler


async def test_a_consolidated_text_is_read_out_of_its_formex_zip() -> None:
    seen: list[tuple[str, str]] = []
    client, acquirer = _cellar_client(_zip_handler(seen, _formex_zip(_NOTICE, _CONS_ACT, _IMAGE)))
    acquired = await acquirer.fetch(_CONS_REF)
    await client.aclose()
    assert seen == [
        (f"https://example.test/cellar/celex/{_CONS_CELEX}", "application/zip;mtype=fmx4")
    ]
    assert acquired.primary().media_type == "application/xml"
    assert acquired.primary().content == _CONS_ACT[1]
    assert acquired.frbr_work_uri == celex_to_frbr("32042R0001")[0]
    assert acquired.expression_uri == f"{acquired.frbr_work_uri}/eng@2045-03-01"
    assert acquired.upstream_metadata["consolidated"] == "2045-03-01"
    assert acquired.etag == '"cons-1"'


async def test_a_consolidated_text_without_formex_fails_rather_than_taking_xhtml() -> None:
    seen: list[tuple[str, str]] = []
    client, acquirer = _cellar_client(_zip_handler(seen, b"", status=404))
    with pytest.raises(httpx.HTTPStatusError):
        await acquirer.fetch(_CONS_REF)
    await client.aclose()
    assert not [accept for _, accept in seen if "html" in accept]


async def test_a_zip_member_too_large_to_unpack_is_refused(monkeypatch) -> None:
    import codify.acquisition.adapters.eu.cellar as cellar_mod

    monkeypatch.setattr(cellar_mod, "_MAX_CONSOLIDATED_BYTES", len(_CONS_ACT[1]) - 1)
    client, acquirer = _cellar_client(_zip_handler([], _formex_zip(_NOTICE, _CONS_ACT)))
    with pytest.raises(ValueError, match="bytes unpacked"):
        await acquirer.fetch(_CONS_REF)
    await client.aclose()


@pytest.mark.parametrize(
    "members", [(_NOTICE, _IMAGE), (_NOTICE, _CONS_ACT, ("second.xml", _CONS_ACT[1]))]
)
async def test_a_zip_without_exactly_one_consolidated_act_is_refused(
    members: tuple[tuple[str, bytes], ...],
) -> None:
    client, acquirer = _cellar_client(_zip_handler([], _formex_zip(*members)))
    with pytest.raises(RuntimeError, match="CONS.ACT"):
        await acquirer.fetch(_CONS_REF)
    await client.aclose()


async def test_discover_consolidations_lists_a_base_acts_texts_oldest_first() -> None:
    from datetime import date
    from urllib.parse import parse_qs

    queries: list[str] = []
    bindings = [
        {"celex": {"value": "02042R0001-20430601"}, "date": {"value": "2043-06-01"}},
        # Neither is a consolidated text, so neither is listed.
        {"celex": {"value": "32042R0001"}, "date": {"value": "2044-01-01"}},
        {"celex": {"value": "not-a-celex"}, "date": {"value": "2044-06-01"}},
        {"celex": {"value": "02042R0001-20450301"}, "date": {"value": "2045-03-01"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        queries.append(parse_qs(request.content.decode())["query"][0])
        return httpx.Response(200, json={"results": {"bindings": bindings}})

    client, acquirer = _cellar_client(handler)
    refs = await acquirer.discover_consolidations("32042R0001")
    await client.aclose()
    assert [(r.extra["celex"], r.as_of) for r in refs] == [
        ("02042R0001-20430601", date(2043, 6, 1)),
        ("02042R0001-20450301", date(2045, 3, 1)),
    ]
    assert {(r.doctype, r.year, r.number) for r in refs} == {("regulation", 2042, "1")}
    (query,) = queries
    assert 'FILTER(STR(?baseCelex) = "32042R0001")' in query
    assert "act_consolidated_based_on_resource_legal" in query
    assert "/language/ENG>" in query
    assert '"fmx4"' in query


@pytest.mark.parametrize(
    "celex, language",
    [
        ('32042R0001") } #', "eng"),
        (_CONS_CELEX, "eng"),
        ("32042R0001\n", "eng"),
        ("32042R0001", "en> ."),
    ],
)
async def test_discover_consolidations_refuses_what_it_would_quote_into_the_query(
    celex: str, language: str
) -> None:
    client, acquirer = _cellar_client(lambda request: httpx.Response(500))
    with pytest.raises(ValueError):
        await acquirer.discover_consolidations(celex, language=language)
    await client.aclose()
