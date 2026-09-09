"""UA, zakon.rada.gov.ua hybrid adapter (HTML card + linked PDF)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import httpx
import structlog

from codify.acquisition.adapters.ua.parsers import extract_pdf_url, extract_title
from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DocumentRef,
    GazetteRef,
)
from codify.acquisition.politeness import make_polite_client
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter, authoritative_language

logger = structlog.get_logger()

SHOW_TEMPLATE = "{base}/laws/show/{number}"
DEFAULT_DOCTYPE = "law"

# Rada kinds -> config doctype keys (frbr.uri_patterns).
_KIND_TO_DOCTYPE = {
    "zakon": "law",
    "kodeks": "code",
    "konstytutsiya": "constitution",
    "postanova": "resolution",
    "ukaz": "decree",
}


def frbr_for(doctype: str, year: int, number: str) -> str:
    return build_frbr_work_uri("ua", _KIND_TO_DOCTYPE.get(doctype, doctype), year, number)


class ZakonRadaAcquirer(BaseAcquirer):
    JURISDICTION: ClassVar[str] = "ua"
    # Open-data channel; contract in ogd/zak/laws/readme.txt.
    DEFAULT_BASE_URL: ClassVar[str] = "https://data.rada.gov.ua"
    # /laws/show accepts exactly this UA literal.
    OPEN_DATA_UA: ClassVar[str] = "OpenData"
    DEFAULT_RATE: ClassVar[int] = 6
    _CONCURRENCY: ClassVar[int] = 1
    _client: httpx.AsyncClient

    def __init__(
        self,
        adapter: SourceAdapter,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(adapter, client=client)
        self._client = client or make_polite_client(adapter, user_agent=self.OPEN_DATA_UA)

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        card_url = ref.extra.get("url") or SHOW_TEMPLATE.format(base=self._base, number=ref.number)
        card = await self._client.get(card_url, headers={"accept": "text/html"})
        card.raise_for_status()
        lang = ref.languages[0] if ref.languages else authoritative_language(ref.jurisdiction_code)
        # Anchored HTML is primary; TXT is an alternate and the gazette PDF is
        # retained as provenance.
        bodies: list[Body] = [
            Body(
                media_type="text/html",
                content=card.content,
                role="primary",
                language=lang,
            )
        ]
        upstream: dict[str, object] = {}
        try:
            txt = await self._client.get(f"{card_url}.txt")
            txt.raise_for_status()
            bodies.append(
                Body(
                    media_type="text/plain",
                    content=txt.content,
                    role="alternate",
                    language=lang,
                )
            )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            logger.warning("ua_txt_fetch_failed", url=f"{card_url}.txt", error=str(exc))
            upstream["txt_fetch_failed"] = True
        pdf_url = extract_pdf_url(card.text, base_url=card_url)
        if pdf_url:
            upstream["pdf_url"] = pdf_url
            try:
                pdf_resp = await self._client.get(pdf_url)
                pdf_resp.raise_for_status()
                bodies.append(
                    Body(
                        media_type="application/pdf",
                        content=pdf_resp.content,
                        role="gazette",
                        language=lang,
                    )
                )
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                logger.warning("ua_pdf_fetch_failed", url=pdf_url, error=str(exc))
                upstream["pdf_fetch_failed"] = True
        # The card JSON's `nazva` is the authoritative full title; the HTML
        # <title> scrape is empty on the OpenData channel this client uses.
        title = ""
        try:
            card_json = await self._client.get(f"{self._base}/laws/card/{ref.number}.json")
            card_json.raise_for_status()
            payload = card_json.json()
            title = str(payload.get("nazva") or "").strip() if isinstance(payload, dict) else ""
            if not title:
                logger.warning("ua_card_no_nazva", number=ref.number)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            logger.warning("ua_card_json_fetch_failed", number=ref.number, error=str(exc))
            upstream["card_json_fetch_failed"] = True
        # The HTML <title> scrape is a fallback but is empty on the OpenData
        # channel, so a missing card title lands as the FRBR-derived stub.
        title = title or extract_title(card.text) or ""
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=frbr_for(ref.doctype or DEFAULT_DOCTYPE, ref.year, ref.number),
            bodies=bodies,
            source_url=card_url,
            fetched_at=datetime.now(UTC),
            etag=card.headers.get("etag"),
            last_modified=card.headers.get("last-modified"),
            licence=None,
            citation_alt=title or None,
            gazette_ref=GazetteRef(
                name="Відомості Верховної Ради України",
                year=ref.year,
                issue=ref.extra.get("vvr_issue", "?"),
            )
            if ref.extra.get("vvr_issue")
            else None,
            ocr_required=False,
            upstream_metadata=upstream,
        )


__all__ = ["ZakonRadaAcquirer", "frbr_for"]
