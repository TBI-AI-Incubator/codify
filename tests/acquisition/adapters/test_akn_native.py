"""Generic akn_native acquirer unit tests via httpx.MockTransport."""

from __future__ import annotations

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
