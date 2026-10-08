"""ES: the Boletín Oficial del Estado's per-item XML, fetched by its ELI.

`/eli/es/{type}/{yyyy}/{mm}/{dd}/{number}/dof/spa/xml` serves the item as
enacted: metadata plus classed paragraphs, which
`codify.pipeline.formats.boe` converts without a model. The site's robots.txt
disallows the `xml.php?id=` route, so the ELI is the only way in. An item whose
XML carries no text (some older ones) is handed on as its PDF.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import ClassVar

import httpx
from lxml import etree

from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DocumentRef,
    GazetteRef,
)
from codify.acquisition.politeness import make_polite_client
from codify.akn._schema import parse_xml
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter

GAZETTE_NAME = "Boletín Oficial del Estado"

# Doctype spellings a ref may carry, to ELI type codes.
_ELI_TYPE = {
    "act": "l",
    "ley": "l",
    "ley_organica": "lo",
    "real_decreto_ley": "rdl",
    "real_decreto_legislativo": "rdlg",
    "real_decreto": "rd",
    "orden": "o",
    "constitucion": "c",
}
# BOE rank names to ELI type codes, for an item without an ELI.
_RANK_ELI = {
    "ley": "l",
    "ley organica": "lo",
    "real decreto-ley": "rdl",
    "real decreto legislativo": "rdlg",
    "real decreto": "rd",
    "orden": "o",
    "constitucion": "c",
}


class BoeItemMissing(LookupError):
    """The ELI did not resolve to a BOE item."""


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def iso_date(yyyymmdd: str) -> str:
    """`19840112` -> `1984-01-12`; anything else -> empty."""
    return (
        f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
        if re.fullmatch(r"\d{8}", yyyymmdd)
        else ""
    )


def read_metadata(root: etree._Element) -> dict[str, str]:
    """The `<metadatos>` children of a BOE item as tag -> text."""
    md = root.find("metadatos")
    if md is None:
        raise ValueError("BOE item has no <metadatos>")
    return {c.tag: (c.text or "").strip() for c in md if isinstance(c.tag, str)}


def doctype_for(eli_type: str) -> str:
    """FRBR subtype for an ELI type code. `l` is a plain act; the rest keep the
    code (`lo`, `rdl`, `rd`), as the `es` config's URI patterns spell them."""
    return "act" if eli_type == "l" else eli_type


def item_doctype(meta: dict[str, str]) -> str:
    m = re.search(r"/eli/es/([a-z]+)/\d{4}/", meta.get("url_eli", ""))
    if m:
        return doctype_for(m.group(1))
    rank = _fold(meta.get("rango", ""))
    eli = _RANK_ELI.get(rank)
    if eli is not None:
        return doctype_for(eli)
    return re.sub(r"[^a-z0-9]+", "-", rank).strip("-") or "act"


def item_date(meta: dict[str, str]) -> str:
    """The date the item was made, else the date it was published."""
    date = iso_date(meta.get("fecha_disposicion", "")) or iso_date(
        meta.get("fecha_publicacion", "")
    )
    if not date:
        raise ValueError(f"BOE item {meta.get('identificador')!r} has no date")
    return date


def item_number(meta: dict[str, str]) -> str:
    """`41/1984` -> `41`; an unnumbered item falls back to its BOE identifier."""
    official = meta.get("numero_oficial", "").split("/")[0].strip()
    return official or meta.get("identificador", "").lower() or "boe"


def boe_work_uri(meta: dict[str, str]) -> str:
    return build_frbr_work_uri("es", item_doctype(meta), item_date(meta)[:4], item_number(meta))


def eli_xml_url(base: str, ref: DocumentRef) -> str:
    """The item's XML by ELI, from `extra["eli"]` or from doctype, date and number."""
    eli = ref.extra.get("eli")
    if eli:
        return eli.rstrip("/") + ("" if eli.rstrip("/").endswith("/xml") else "/dof/spa/xml")
    date = ref.extra.get("date", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError(f"BOE ref {ref.number!r} needs extra['date'] (YYYY-MM-DD) or extra['eli']")
    eli_type = _ELI_TYPE.get(ref.doctype, ref.doctype)
    return f"{base}/eli/es/{eli_type}/{date.replace('-', '/')}/{ref.number}/dof/spa/xml"


class BoeAcquirer(BaseAcquirer):
    JURISDICTION: ClassVar[str] = "es"
    DEFAULT_BASE_URL: ClassVar[str] = "https://www.boe.es"
    DEFAULT_RATE: ClassVar[int] = 30
    _CONCURRENCY: ClassVar[int] = 1
    _client: httpx.AsyncClient

    def __init__(self, adapter: SourceAdapter, *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(adapter, client=client)
        self._client = client or make_polite_client(adapter)
        self._licence = adapter.licence

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        url = eli_xml_url(self._base, ref)
        resp = await self._client.get(url, headers={"accept": "application/xml"})
        resp.raise_for_status()
        # A missing ELI answers 200 with an HTML error page.
        try:
            root = parse_xml(resp.content, huge_tree=True)
        except etree.XMLSyntaxError as exc:
            raise BoeItemMissing(f"{url} did not return a BOE item") from exc
        if root.tag != "documento":
            raise BoeItemMissing(f"{url} did not return a BOE item")
        meta = read_metadata(root)
        xml = Body(media_type="application/xml", content=resp.content, language="spa")
        bodies = [xml]
        upstream: dict[str, object] = {"boe_id": meta.get("identificador", "")}
        pdf_url = meta.get("url_pdf", "")
        if pdf_url:
            upstream["pdf_url"] = pdf_url
        texto = root.find("texto")
        if texto is None or not "".join(texto.itertext()).strip():
            if not pdf_url:
                raise BoeItemMissing(f"{url} carries neither text nor a PDF")
            pdf = await self._client.get(pdf_url)
            pdf.raise_for_status()
            # No text to convert: the PDF goes to the structuring lane instead.
            xml.role = "card"
            bodies.insert(
                0, Body(media_type="application/pdf", content=pdf.content, language="spa")
            )
            upstream["text_missing"] = True
        work = boe_work_uri(meta)
        pub = iso_date(meta.get("fecha_publicacion", ""))
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=work,
            expression_uri=f"{work}/spa@{item_date(meta)}",
            bodies=bodies,
            source_url=url,
            fetched_at=datetime.now(UTC),
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            licence=self._licence,
            citation_alt=meta.get("titulo") or None,
            gazette_ref=GazetteRef(
                name=GAZETTE_NAME,
                year=int(pub[:4]),
                issue=meta.get("diario_numero", ""),
                page=meta.get("pagina_inicial") or None,
                date=pub,
            )
            if pub and meta.get("diario_numero")
            else None,
            upstream_metadata=upstream,
        )


__all__ = [
    "BoeAcquirer",
    "BoeItemMissing",
    "GAZETTE_NAME",
    "boe_work_uri",
    "doctype_for",
    "eli_xml_url",
    "item_date",
    "item_doctype",
    "item_number",
    "iso_date",
    "read_metadata",
]
