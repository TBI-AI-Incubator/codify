"""RS, paragraf.rs adapter.

Paragraf Lex serves consolidated Serbian laws as PDF at slug-keyed URLs:
  https://www.paragraf.rs/propisi_download/{slug}.pdf

The slug is operator-curated in the manifest (DocumentRef.extra['slug']);
discover() is a no-op since paragraf has no public discovery API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import httpx

from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DocumentRef,
)
from codify.acquisition.politeness import make_polite_client
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter, authoritative_language

DOWNLOAD_TEMPLATE = "{base}/propisi_download/{slug}.pdf"


def frbr_for(doctype: str, year: int, number: str) -> str:
    return build_frbr_work_uri("rs", doctype, year, number)


class ParagrafAcquirer(BaseAcquirer):
    JURISDICTION: ClassVar[str] = "rs"
    DEFAULT_BASE_URL: ClassVar[str] = "https://www.paragraf.rs"
    DEFAULT_RATE: ClassVar[int] = 30
    _client: httpx.AsyncClient

    def __init__(
        self,
        adapter: SourceAdapter,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(adapter, client=client)
        self._client = client or make_polite_client(adapter)

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        slug = ref.extra.get("slug")
        if not slug:
            raise ValueError(
                f"RS paragraf fetch requires DocumentRef.extra['slug'] "
                f"(year={ref.year}, number={ref.number!r})"
            )
        url = DOWNLOAD_TEMPLATE.format(base=self._base, slug=slug)
        resp = await self._client.get(url, headers={"accept": "application/pdf,*/*;q=0.5"})
        resp.raise_for_status()
        if not resp.content.startswith(b"%PDF"):
            raise RuntimeError(f"paragraf returned non-PDF content for {slug!r}")
        body = Body(
            media_type="application/pdf",
            content=resp.content,
            role="primary",
            language=ref.languages[0]
            if ref.languages
            else authoritative_language(ref.jurisdiction_code),
        )
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=frbr_for(ref.doctype or "zakon", ref.year, ref.number),
            bodies=[body],
            source_url=url,
            fetched_at=datetime.now(UTC),
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            licence=None,
            citation_alt=ref.extra.get("title_short"),
            ocr_required=False,
            upstream_metadata={
                "slug": slug,
                "acquis_chapter": ref.extra.get("acquis_chapter"),
                "transposes": ref.extra.get("transposes"),
            },
        )


__all__ = ["ParagrafAcquirer", "frbr_for"]
