"""ES, BoeAcquirer unit tests via httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DocumentRef, registered_kinds
from codify.acquisition.adapters.es.boe import BoeAcquirer, BoeItemMissing, eli_xml_url
from codify.acquisition.politeness import PoliteTransport
from codify.jurisdictions import SourceAdapter

_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<documento>
  <metadatos>
    <identificador>BOE-A-2019-90002</identificador>
    <rango codigo="1290">Ley Orgánica</rango>
    <fecha_disposicion>20190403</fecha_disposicion>
    <numero_oficial>9/2019</numero_oficial>
    <titulo>Ley Orgánica 9/2019, de 3 de abril, de ensayo.</titulo>
    <fecha_publicacion>20190404</fecha_publicacion>
    <diario_numero>81</diario_numero>
    <pagina_inicial>9001</pagina_inicial>
    <url_pdf>https://www.boe.es/boe/dias/2019/04/04/pdfs/BOE-A-2019-90002.pdf</url_pdf>
    <url_eli>https://www.boe.es/eli/es/lo/2019/04/03/9</url_eli>
  </metadatos>
  <texto>{texto}</texto>
</documento>"""

_TEXT = '<p class="articulo">Artículo único.</p><p class="parrafo">Texto de ensayo.</p>'
_ELI = "https://www.boe.es/eli/es/lo/2019/04/03/9/dof/spa/xml"
_PDF = b"%PDF-1.4\n%boe"


def _adapter() -> SourceAdapter:
    return SourceAdapter(kind="gazette_xml", name="boe", rate_limit_per_minute=30)


def _ref(**extra: str) -> DocumentRef:
    return DocumentRef(
        jurisdiction_code="es",
        doctype="lo",
        year=2019,
        number="9",
        extra=extra or {"date": "2019-04-03"},
    )


async def _fetch(
    responses: dict[str, httpx.Response], ref: DocumentRef
) -> tuple[object, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return responses.get(str(request.url), httpx.Response(404))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await BoeAcquirer(_adapter(), client=client).fetch(ref), seen
    finally:
        await client.aclose()


def test_registers_gazette_xml() -> None:
    assert "gazette_xml" in registered_kinds()


def test_default_client_is_polite() -> None:
    acquirer = BoeAcquirer(_adapter())
    assert isinstance(acquirer._client._transport, PoliteTransport)
    assert acquirer.politeness.rate_limit_per_minute == 30


@pytest.mark.parametrize(
    ("doctype", "extra", "url"),
    [
        ("lo", {"date": "2019-04-03"}, _ELI),
        ("act", {"date": "2019-04-03"}, "https://www.boe.es/eli/es/l/2019/04/03/9/dof/spa/xml"),
        (
            "real_decreto_ley",
            {"date": "2019-04-03"},
            "https://www.boe.es/eli/es/rdl/2019/04/03/9/dof/spa/xml",
        ),
        ("lo", {"eli": "https://www.boe.es/eli/es/lo/2019/04/03/9"}, _ELI),
        ("lo", {"eli": _ELI}, _ELI),
    ],
)
def test_eli_xml_url(doctype: str, extra: dict[str, str], url: str) -> None:
    ref = DocumentRef(jurisdiction_code="es", doctype=doctype, year=2019, number="9", extra=extra)
    assert eli_xml_url("https://www.boe.es", ref) == url


def test_eli_needs_a_date() -> None:
    with pytest.raises(ValueError, match="extra"):
        eli_xml_url("https://www.boe.es", _ref(date="2019"))


async def test_fetch_takes_the_xml_by_eli() -> None:
    item = _ITEM.format(texto=_TEXT).encode()
    acquired, seen = await _fetch({_ELI: httpx.Response(200, content=item)}, _ref())
    assert seen == [_ELI]  # never the robots-disallowed xml.php route, nor the PDF
    assert [(b.media_type, b.role) for b in acquired.bodies] == [("application/xml", "primary")]
    assert acquired.frbr_work_uri == "/akn/es/act/lo/2019/9"
    assert acquired.expression_uri == "/akn/es/act/lo/2019/9/spa@2019-04-03"
    assert acquired.citation_alt == "Ley Orgánica 9/2019, de 3 de abril, de ensayo."
    assert acquired.gazette_ref is not None
    assert (acquired.gazette_ref.issue, acquired.gazette_ref.page) == ("81", "9001")
    assert acquired.upstream_metadata["boe_id"] == "BOE-A-2019-90002"


async def test_an_item_without_text_hands_on_its_pdf() -> None:
    item = _ITEM.format(texto="").encode()
    pdf_url = "https://www.boe.es/boe/dias/2019/04/04/pdfs/BOE-A-2019-90002.pdf"
    acquired, _ = await _fetch(
        {_ELI: httpx.Response(200, content=item), pdf_url: httpx.Response(200, content=_PDF)},
        _ref(),
    )
    assert [(b.media_type, b.role) for b in acquired.bodies] == [
        ("application/pdf", "primary"),
        ("application/xml", "card"),
    ]
    assert acquired.primary().content == _PDF
    assert acquired.upstream_metadata["text_missing"] is True


async def test_an_unknown_eli_is_missing_not_parsed() -> None:
    # The site answers a missing ELI with a 200 HTML error page.
    page = b"<!DOCTYPE html><html><head><title>Error 404</title></head></html>"
    with pytest.raises(BoeItemMissing):
        await _fetch({_ELI: httpx.Response(200, content=page)}, _ref())
