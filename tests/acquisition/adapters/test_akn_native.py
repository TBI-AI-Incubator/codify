"""Generic akn_native acquirer unit tests via httpx.MockTransport."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DocumentRef, get_acquirer
from codify.acquisition.adapters.akn_native import AknNativeAcquirer
from codify.jurisdictions import SourceAdapter

_AKN = b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act/></akomaNtoso>'


def _adapter(
    template: str = "https://www.legislation.gov.uk/{source_path}/data.akn",
) -> SourceAdapter:
    return SourceAdapter(
        kind="akn_native",
        name="legislation.gov.uk",
        url_template=template,
    )


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


def _ref(
    doctype: str = "ukpga", year: int = 2018, number: str = "12", as_of: str = "latest"
) -> DocumentRef:
    return DocumentRef(
        jurisdiction_code="gb", doctype=doctype, year=year, number=number, as_of=as_of
    )


def test_registered_for_gb() -> None:
    acquirer = get_acquirer("gb")
    assert isinstance(acquirer, AknNativeAcquirer)
    assert acquirer.jurisdiction_code == "gb"
    assert acquirer.politeness.rate_limit_per_minute == 120  # the config's 2 req/s floor


async def test_fetch_expands_publisher_path() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=_AKN, headers={"etag": '"abc"'})

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    acquired = await acquirer.fetch(_ref(doctype="ukpga"))

    assert seen == ["https://www.legislation.gov.uk/ukpga/2018/12/data.akn"]
    assert acquired.frbr_work_uri == "/akn/gb/act/ukpga/2018/12"
    assert acquired.primary().media_type == "application/xml"
    assert acquired.primary().content == _AKN
    assert acquired.etag == '"abc"'


async def test_fetch_si_uses_uksi_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/uksi/2017/692/" in str(request.url)
        return httpx.Response(200, content=_AKN)

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    acquired = await acquirer.fetch(_ref(doctype="uksi", year=2017, number="692"))
    assert acquired.frbr_work_uri == "/akn/gb/act/uksi/2017/692"


async def test_devolved_token_fetches_its_own_path() -> None:
    """Two tokens sharing a year and number are two works."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=_AKN)

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    scottish = await acquirer.fetch(_ref(doctype="asp", year=2020, number="1"))
    westminster = await acquirer.fetch(_ref(doctype="ukpga", year=2020, number="1"))

    assert seen == [
        "https://www.legislation.gov.uk/asp/2020/1/data.akn",
        "https://www.legislation.gov.uk/ukpga/2020/1/data.akn",
    ]
    assert scottish.frbr_work_uri == "/akn/gb/act/asp/2020/1"
    assert westminster.frbr_work_uri == "/akn/gb/act/ukpga/2020/1"
    assert scottish.frbr_work_uri != westminster.frbr_work_uri


async def test_bare_act_doctype_cannot_be_fetched() -> None:
    """A generic `act` names no publisher type, so it has no fetch URL."""
    acquirer = AknNativeAcquirer(
        "gb", _adapter(), client=_client(httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    with pytest.raises(ValueError, match="cannot be expanded"):
        await acquirer.fetch(_ref(doctype="act"))


async def test_404_raises_file_not_found_with_pdf_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    with pytest.raises(FileNotFoundError, match="PDF-only"):
        await acquirer.fetch(_ref(number="99999"))


async def test_300_regnal_ambiguity_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(300)

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    with pytest.raises(FileNotFoundError, match="regnal"):
        await acquirer.fetch(_ref(year=1801, number="90"))


def test_missing_url_template_rejected() -> None:
    with pytest.raises(ValueError, match="url_template"):
        AknNativeAcquirer("gb", SourceAdapter(kind="akn_native", name="broken"))


# ── expression selector, publisher path, document-derived work ───────────────

_REGNAL_AKN = (
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act><meta>'
    b'<identification source="#src"><FRBRWork>'
    b'<FRBRthis value="http://www.legislation.gov.uk/id/ukpga/1951/48"/>'
    b'<FRBRuri value="http://www.legislation.gov.uk/id/ukpga/1951/48"/></FRBRWork>'
    b'<FRBRExpression><FRBRthis value="http://www.legislation.gov.uk/ukpga/1951/48/enacted"/>'
    b"</FRBRExpression></identification></meta></act></akomaNtoso>"
)


def _recording() -> tuple[list[str], httpx.MockTransport]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=_REGNAL_AKN)

    return seen, httpx.MockTransport(handler)


async def test_as_of_enacted_fetches_the_original_expression() -> None:
    seen, transport = _recording()
    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(transport))
    await acquirer.fetch(_ref(doctype="ukpga", year=1998, number="42", as_of="enacted"))
    await acquirer.fetch(_ref(doctype="uksi", year=2020, number="1500", as_of="enacted"))
    assert seen == [
        "https://www.legislation.gov.uk/ukpga/1998/42/enacted/data.akn",
        "https://www.legislation.gov.uk/uksi/2020/1500/made/data.akn",
    ]


async def test_publisher_path_wins_and_the_document_names_the_work() -> None:
    """A regnal ref carries the publisher path; the calendar-year work comes
    from the fetched document, not from the ref."""
    seen, transport = _recording()
    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(transport))
    ref = DocumentRef(
        jurisdiction_code="gb",
        doctype="ukpga",
        year=1951,
        number="48",
        extra={"path": "ukpga/Geo6/14-15/48"},
    )
    acquired = await acquirer.fetch(ref)
    assert seen == ["https://www.legislation.gov.uk/ukpga/Geo6/14-15/48/data.akn"]
    assert acquired.frbr_work_uri == "/akn/gb/act/ukpga/1951/48"
    assert acquired.expression_uri == "http://www.legislation.gov.uk/ukpga/1951/48/enacted"


async def test_licence_rides_the_adapter() -> None:
    _, transport = _recording()
    adapter = SourceAdapter(
        kind="akn_native",
        name="legislation.gov.uk",
        url_template="https://www.legislation.gov.uk/{source_path}/data.akn",
        licence="OGL-UK-3.0",
    )
    acquired = await AknNativeAcquirer("gb", adapter, client=_client(transport)).fetch(_ref())
    assert acquired.licence == "OGL-UK-3.0"


async def test_as_of_enacted_refuses_a_doctype_with_no_original_path() -> None:
    _, transport = _recording()
    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(transport))
    with pytest.raises(ValueError, match="original-expression"):
        await acquirer.fetch(_ref(doctype="act", as_of="enacted"))


def test_the_identification_is_parsed_not_sniffed() -> None:
    """A prefixed, single-quoted serialisation still names its own work."""
    from codify.acquisition.adapters.akn_native import _frbr_from_document

    doc = (
        b"<akn:akomaNtoso xmlns:akn='http://docs.oasis-open.org/legaldocml/ns/akn/3.0'><akn:act>"
        b"<akn:meta><akn:identification source='#'><akn:FRBRWork>"
        b"<akn:FRBRthis value='http://www.legislation.gov.uk/id/asp/2020/1'/>"
        b"<akn:FRBRuri value='http://www.legislation.gov.uk/id/asp/2020/1'/></akn:FRBRWork>"
        b"<akn:FRBRExpression><akn:FRBRthis value='http://www.legislation.gov.uk/asp/2020/1/2021-01-01'/>"
        b"<akn:FRBRuri value='http://www.legislation.gov.uk/asp/2020/1/2021-01-01'/>"
        b"</akn:FRBRExpression></akn:identification></akn:meta><akn:body/></akn:act></akn:akomaNtoso>"
    )
    assert _frbr_from_document(doc) == (
        "/akn/gb/act/asp/2020/1",
        "http://www.legislation.gov.uk/asp/2020/1/2021-01-01",
    )
    assert _frbr_from_document(b"<not xml") == (None, None)


_STUB = (Path(__file__).parent / "fixtures" / "akn_native_meta_only_stub.akn.xml").read_bytes()


async def test_a_meta_only_document_naming_a_pdf_is_pdf_only() -> None:
    """The publisher answers 200 with metadata and no body for an expression it
    holds only as PDF; that is the 404 case in another coat and fails the same way."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_STUB)

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    with pytest.raises(FileNotFoundError, match="PDF-only") as excinfo:
        await acquirer.fetch(_ref(doctype="nisi", year=1982, number="7", as_of="enacted"))
    assert "xsi_19820007_en.pdf" in str(excinfo.value)


@pytest.mark.parametrize("container", ["body", "mainBody", "judgmentBody", "preface"])
async def test_a_document_with_text_still_parses_beside_a_pdf_alternative(container: str) -> None:
    """Mutation check on the stub rule, one container at a time: any one of them
    means the document carries text, whatever alternatives it lists."""
    full = _STUB.replace(b"</meta>", f"</meta><{container}><p>Text.</p></{container}>".encode())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=full)

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    acquired = await acquirer.fetch(_ref(doctype="nisi", year=1982, number="7"))
    assert acquired.primary().content == full


async def test_an_alternative_that_is_not_a_pdf_does_not_classify() -> None:
    """Only a PDF alternative names a PDF-only expression; an HTML alternative on
    a meta-only document leaves it to the parser as before."""
    html_alt = _STUB.replace(b"xsi_19820007_en.pdf", b"xsi_19820007_en.html")
    assert b".pdf" not in html_alt

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html_alt)

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    acquired = await acquirer.fetch(_ref(doctype="nisi", year=1982, number="7", as_of="enacted"))
    assert acquired.primary().content == html_alt


# ── opt-in PDF fallback ────────────────────────────────────────────────────────

_UK_STUB = _STUB.replace(b"https://publisher.test", b"http://www.legislation.gov.uk")
_PDFS = "http://www.legislation.gov.uk/xsi/1982/7/pdfs"
_ENGLISH = f'<ukm:Alternative URI="{_PDFS}/xsi_19820007_en.pdf" Date="1982-11-12"/>'
_WELSH = f'<ukm:Alternative URI="{_PDFS}/xsi_19820007_we.pdf" Language="Welsh"/>'
_MEMORANDUM = (
    f'<ukm:Alternative URI="{_PDFS}/xsiem_19820007_en.pdf" Title="NI Explanatory Memorandum"/>'
)
_ELSEWHERE = '<ukm:Alternative URI="http://elsewhere.example/xsi_19820007_en.pdf"/>'
_PAGE = f'<ukm:Alternative URI="{_PDFS.rsplit("/", 1)[0]}/xsi_19820007_en.html"/>'


def _stub_naming(*alternatives: str) -> bytes:
    block = "<ukm:Alternatives>" + "".join(alternatives) + "</ukm:Alternatives>"
    return re.sub(
        rb"<ukm:Alternatives>.*?</ukm:Alternatives>", block.encode(), _UK_STUB, flags=re.DOTALL
    )


def _fallback_ref(*languages: str) -> DocumentRef:
    return DocumentRef(
        jurisdiction_code="gb",
        doctype="nisi",
        year=1982,
        number="7",
        as_of="enacted",
        languages=list(languages),
        extra={"pdf_fallback": "1"},
    )


def _serving(meta: bytes, pdf: bytes | None = None) -> tuple[list[str], AknNativeAcquirer]:
    """The metadata document at its own URL, and a PDF naming the URL it was asked at."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if str(request.url).endswith(".pdf"):
            return httpx.Response(200, content=pdf or b"%PDF-1.4 " + str(request.url).encode())
        return httpx.Response(200, content=meta)

    return seen, AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))


async def test_the_fallback_takes_the_pdf_the_metadata_names_over_https() -> None:
    seen, acquirer = _serving(_stub_naming(_ENGLISH))
    acquired = await acquirer.fetch(_fallback_ref())
    assert seen == [
        "https://www.legislation.gov.uk/nisi/1982/7/made/data.akn",
        "https://www.legislation.gov.uk/xsi/1982/7/pdfs/xsi_19820007_en.pdf",
    ]
    body = acquired.primary()
    assert (body.media_type, body.language) == ("application/pdf", "eng")
    assert body.content == b"%PDF-1.4 " + seen[1].encode()
    assert acquired.source_url == seen[1]
    assert acquired.frbr_work_uri == "/akn/gb/act/nisi/1982/7"
    assert acquired.expression_uri == "http://www.legislation.gov.uk/xsi/1982/7/made"


async def test_the_fallback_is_opt_in() -> None:
    seen, acquirer = _serving(_stub_naming(_ENGLISH))
    ref = _fallback_ref().model_copy(update={"extra": {}})
    with pytest.raises(FileNotFoundError, match="PDF-only"):
        await acquirer.fetch(ref)
    assert len(seen) == 1


async def test_a_document_with_text_is_taken_as_text_whatever_the_flag() -> None:
    with_body = _stub_naming(_ENGLISH).replace(b"</meta>", b"</meta><body><p>Text.</p></body>")
    _, acquirer = _serving(with_body)
    acquired = await acquirer.fetch(_fallback_ref())
    assert acquired.primary().media_type == "application/xml"


_WITH_BODY = b"</meta><body><p>Text.</p></body>"


async def test_a_welsh_ref_takes_the_welsh_pdf_of_a_document_that_has_text() -> None:
    """The text a document carries is English; its Welsh is the Welsh PDF."""
    meta = _stub_naming(_WELSH, _ENGLISH).replace(b"</meta>", _WITH_BODY)
    seen, acquirer = _serving(meta)
    acquired = await acquirer.fetch(_fallback_ref("cym"))
    assert (acquired.primary().media_type, acquired.primary().language) == (
        "application/pdf",
        "cym",
    )
    assert seen[1].endswith("xsi_19820007_we.pdf")


async def test_a_welsh_ref_finds_no_welsh_text_in_a_document_with_only_english() -> None:
    meta = _stub_naming(_ENGLISH).replace(b"</meta>", _WITH_BODY)
    seen, acquirer = _serving(meta)
    with pytest.raises(FileNotFoundError, match="no cym PDF"):
        await acquirer.fetch(_fallback_ref("cym"))
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("languages", "taken"),
    [((), "en"), (("eng",), "en"), (("cym",), "we"), (("cym", "eng"), "we")],
)
async def test_the_fallback_takes_the_pdf_in_the_ref_language(
    languages: tuple[str, ...], taken: str
) -> None:
    """Welsh is listed first, as it is for a bilingual instrument; the language decides."""
    seen, acquirer = _serving(_stub_naming(_WELSH, _ENGLISH))
    acquired = await acquirer.fetch(_fallback_ref(*languages))
    assert seen[1].endswith(f"xsi_19820007_{taken}.pdf")
    assert acquired.primary().language == (languages[0] if languages else "eng")
    # The metadata names the English expression; a Welsh text is not that expression.
    assert (acquired.expression_uri is None) == (taken == "we")


async def test_the_fallback_takes_only_a_pdf() -> None:
    seen, acquirer = _serving(_stub_naming(_PAGE, _ENGLISH))
    await acquirer.fetch(_fallback_ref())
    assert seen[1].endswith("xsi_19820007_en.pdf")


async def test_the_fallback_document_carries_the_adapter_licence() -> None:
    adapter = SourceAdapter(
        kind="akn_native",
        name="legislation.gov.uk",
        url_template="https://www.legislation.gov.uk/{source_path}/data.akn",
        licence="OGL-UK-3.0",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        pdf = str(request.url).endswith(".pdf")
        return httpx.Response(200, content=b"%PDF-1.4" if pdf else _stub_naming(_ENGLISH))

    acquirer = AknNativeAcquirer("gb", adapter, client=_client(httpx.MockTransport(handler)))
    assert (await acquirer.fetch(_fallback_ref())).licence == "OGL-UK-3.0"


async def test_the_fallback_takes_no_titled_alternative() -> None:
    seen, acquirer = _serving(_stub_naming(_MEMORANDUM, _ENGLISH))
    await acquirer.fetch(_fallback_ref())
    assert seen[1].endswith("xsi_19820007_en.pdf")

    seen, acquirer = _serving(_stub_naming(_MEMORANDUM))
    with pytest.raises(FileNotFoundError, match="no eng PDF"):
        await acquirer.fetch(_fallback_ref())
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("alternatives", "languages"),
    [((_ELSEWHERE,), ()), ((_ENGLISH,), ("cym",)), ((_ENGLISH,), ("gla",))],
)
async def test_the_fallback_without_a_pdf_to_take_asks_for_none(
    alternatives: tuple[str, ...], languages: tuple[str, ...]
) -> None:
    """Another host's PDF is not taken, and no Welsh or Gaelic PDF is made from the English."""
    seen, acquirer = _serving(_stub_naming(*alternatives))
    with pytest.raises(FileNotFoundError, match="PDF to take"):
        await acquirer.fetch(_fallback_ref(*languages))
    assert len(seen) == 1


async def test_a_download_that_is_not_a_pdf_is_not_taken() -> None:
    _, acquirer = _serving(_stub_naming(_ENGLISH), pdf=b"<html>Not found</html>")
    with pytest.raises(FileNotFoundError, match="not a PDF"):
        await acquirer.fetch(_fallback_ref())


async def test_a_pdf_the_publisher_does_not_have_is_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith(".pdf"):
            return httpx.Response(404)
        return httpx.Response(200, content=_stub_naming(_ENGLISH))

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    with pytest.raises(FileNotFoundError, match="404"):
        await acquirer.fetch(_fallback_ref())


def _redirecting(location: str) -> tuple[list[str], AknNativeAcquirer]:
    """The metadata, then a PDF address that redirects to `location`, which serves a PDF."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if str(request.url).endswith("xsi_19820007_en.pdf"):
            return httpx.Response(302, headers={"location": location})
        if str(request.url).endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF-1.4 moved")
        return httpx.Response(200, content=_stub_naming(_ENGLISH))

    return seen, AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))


async def test_a_pdf_that_redirects_off_the_publisher_is_not_taken() -> None:
    seen, acquirer = _redirecting("https://elsewhere.example/stolen.pdf")
    with pytest.raises(FileNotFoundError, match="off the publisher"):
        await acquirer.fetch(_fallback_ref())
    assert not any("elsewhere.example" in url for url in seen)


async def test_a_pdf_redirect_that_stays_on_the_publisher_is_followed() -> None:
    seen, acquirer = _redirecting("/xsi/1982/7/pdfs/moved.pdf")
    acquired = await acquirer.fetch(_fallback_ref())
    assert seen[-1] == "https://www.legislation.gov.uk/xsi/1982/7/pdfs/moved.pdf"
    assert acquired.primary().content == b"%PDF-1.4 moved"


@pytest.mark.parametrize(("hops", "taken"), [(5, True), (6, False)])
async def test_a_chain_of_five_redirects_is_followed_and_a_sixth_is_not(
    hops: int, taken: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/pdfs/xsi_19820007_en.pdf"):
            return httpx.Response(302, headers={"location": "/hop/1.pdf"})
        if "/hop/" in url:
            n = int(url.rsplit("/", 1)[1].split(".")[0])
            if n < hops:
                return httpx.Response(302, headers={"location": f"/hop/{n + 1}.pdf"})
            return httpx.Response(200, content=b"%PDF-1.4 end")
        return httpx.Response(200, content=_stub_naming(_ENGLISH))

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    if taken:
        assert (await acquirer.fetch(_fallback_ref())).primary().content == b"%PDF-1.4 end"
    else:
        with pytest.raises(FileNotFoundError, match="too many redirects"):
            await acquirer.fetch(_fallback_ref())


async def test_a_pdf_that_redirects_forever_is_not_taken() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith(".pdf"):
            return httpx.Response(302, headers={"location": "/xsi/1982/7/pdfs/again.pdf"})
        return httpx.Response(200, content=_stub_naming(_ENGLISH))

    acquirer = AknNativeAcquirer("gb", _adapter(), client=_client(httpx.MockTransport(handler)))
    with pytest.raises(FileNotFoundError, match="too many redirects"):
        await acquirer.fetch(_fallback_ref())
