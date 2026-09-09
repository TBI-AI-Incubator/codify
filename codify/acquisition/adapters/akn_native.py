"""Generic adapter for publishers serving native Akoma Ntoso over HTTP.

Jurisdiction-agnostic: any config with a `kind: akn_native` source adapter and
a `url_template` gets acquisition for free (legislation.gov.uk, Laws.Africa).
The template expands from the canonical FRBR URI (`{frbr_uri}` for AKN-path
publishers, `{source_path}` for publisher-token paths like `ukpga/2018/12`).

A ref may carry the publisher's own path in `extra["path"]` (a regnal citation
such as `ukpga/Geo6/14-15/48` has no calendar year until fetched); the fetched
document's FRBRWork then names the work. `as_of="enacted"` asks for the
original expression (`/enacted` or `/made`) instead of the current text.
legislation.gov.uk answers 404 for documents that exist only as PDF.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import structlog
from lxml import etree

from codify.acquisition.base import (
    AcquiredDocument,
    Body,
    CorpusManifest,
    DiscoveryQuery,
    DocumentRef,
    PolitenessProfile,
)
from codify.acquisition.politeness import make_polite_client
from codify.akn._schema import parse_xml
from codify.frbr import (
    build_frbr_work_uri,
    expand_source_template,
    original_expression_path,
    parse_source_ref,
)
from codify.jurisdictions import SourceAdapter

logger = structlog.get_logger()


def _frbr_from_document(content: bytes) -> tuple[str | None, str | None]:
    """(work URI in our scheme, publisher expression URI) from the top-level
    identification; (None, None) when the document does not parse."""
    try:
        root = parse_xml(content)
    except (etree.XMLSyntaxError, ValueError):
        return None, None
    ident = root.find("./{*}*/{*}meta/{*}identification")
    if ident is None:
        return None, None

    def value(level: str) -> str | None:
        for tag in ("FRBRuri", "FRBRthis"):
            el = ident.find(f"{{*}}{level}/{{*}}{tag}")
            found = el.get("value") if el is not None else None
            if found:
                return str(found)
        return None

    work = value("FRBRWork")
    parsed = parse_source_ref(work) if work else None
    return (parsed[0] if parsed else None, value("FRBRExpression"))


# Any of these holds text; a document with none and a PDF alternative is a stub.
_TEXT_CONTAINERS = ("body", "mainBody", "judgmentBody", "preface")


def _pdf_only_alternative(content: bytes) -> str | None:
    """The PDF a meta-only document points at, or None for a document with text.

    A publisher can answer 200 with an act that carries its metadata and no
    body, naming the only published form under its proprietary alternatives;
    that is the same fact as its 404 for a PDF-only document."""
    try:
        root = parse_xml(content)
    except (etree.XMLSyntaxError, ValueError):
        return None
    doc = root.find("./{*}*")
    if doc is None or doc.find("{*}meta") is None:
        return None
    if any(doc.find(f"{{*}}{part}") is not None for part in _TEXT_CONTAINERS):
        return None
    for alt in doc.iter("{*}Alternative"):
        uri = alt.get("URI") or alt.get("href") or ""
        if uri.lower().endswith(".pdf"):
            return str(uri)
    return None


class AknNativeAcquirer:
    """Implements the Acquirer protocol without a fixed JURISDICTION."""

    _CONCURRENCY = 4
    _DEFAULT_RATE = 60  # req/min, 1/s, half the empirically-safe ceiling

    def __init__(
        self,
        jurisdiction_code: str,
        adapter: SourceAdapter,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not adapter.url_template:
            raise ValueError(
                f"akn_native adapter {adapter.name!r} ({jurisdiction_code}) has no url_template"
            )
        self.jurisdiction_code = jurisdiction_code
        profile = PolitenessProfile.from_adapter(adapter, concurrency=self._CONCURRENCY)
        if profile.rate_limit_per_minute is None:
            profile = profile.model_copy(update={"rate_limit_per_minute": self._DEFAULT_RATE})
        self.politeness = profile
        self._adapter = adapter
        self._template = adapter.url_template
        self._client = client or make_polite_client(adapter)

    @classmethod
    def from_config(cls, jurisdiction_code: str, adapter: SourceAdapter) -> "AknNativeAcquirer":
        return cls(jurisdiction_code, adapter)

    def _url(self, ref: DocumentRef, work_uri: str) -> str:
        """The fetch URL: the publisher's own path when the ref carries one,
        else the template expanded from the work URI; the original-expression
        segment inserted before the format suffix when `as_of` asks for it."""
        segment = original_expression_path(ref.doctype) if ref.as_of == "enacted" else None
        if ref.as_of == "enacted" and segment is None:
            raise ValueError(f"no original-expression path is known for {ref.doctype!r}")
        path = ref.extra.get("path")
        url = (
            self._template.replace("{source_path}", path)
            if path and "{source_path}" in self._template
            else expand_source_template(self._template, work_uri)
        )
        if url is None:
            raise ValueError(f"url_template {self._template!r} cannot be expanded for {work_uri!r}")
        if segment is not None:
            stem, _, suffix = url.rpartition("/")
            url = f"{stem}/{segment}/{suffix}"
        return url

    def source_url(self, ref: DocumentRef) -> str:
        """The URL `fetch` would request for this ref, without requesting it."""
        work_uri = build_frbr_work_uri(
            self.jurisdiction_code, ref.doctype, ref.year, str(ref.number)
        )
        return self._url(ref, work_uri)

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        work_uri = build_frbr_work_uri(
            self.jurisdiction_code, ref.doctype, ref.year, str(ref.number)
        )
        url = self._url(ref, work_uri)
        # No accept header: the template's explicit /data.{format} path picks
        # the format; negotiation on top of it draws 406s from legislation.gov.uk.
        response = await self._client.get(url)
        if response.status_code == 404:
            # legislation.gov.uk: 404 can mean PDF-only, not absent.
            raise FileNotFoundError(f"{url} -> 404 (document may be PDF-only upstream)")
        if response.status_code == 300:
            # Several documents answer to this path (a regnal-year ambiguity).
            raise FileNotFoundError(
                f"{url} -> 300 (regnal-year ambiguity; the path names more than one document)"
            )
        response.raise_for_status()
        pdf = _pdf_only_alternative(response.content)
        if pdf is not None:
            # Metadata only, the text published as PDF: the 404 case served as 200.
            raise FileNotFoundError(
                f"{url} -> meta-only document, text is PDF-only upstream ({pdf})"
            )
        # The document names its own work; a regnal ref only learns its
        # calendar year here.
        document_work, expression = _frbr_from_document(response.content)
        logger.info("akn_native_fetched", url=url, bytes=len(response.content))
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=document_work or work_uri,
            expression_uri=expression,
            bodies=[Body(media_type="application/xml", content=response.content, role="primary")],
            source_url=url,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            licence=self._adapter.licence,
        )

    async def enumerate(  # noqa: A003
        self, manifest: CorpusManifest
    ) -> AsyncIterator[DocumentRef]:
        for ref in manifest.refs:
            yield ref

    async def discover(self, query: DiscoveryQuery) -> AsyncIterator[DocumentRef]:
        raise NotImplementedError("akn_native has no discovery; use enumerate(manifest)")
        if False:  # pragma: no cover  (typing: makes this an async generator)
            yield
