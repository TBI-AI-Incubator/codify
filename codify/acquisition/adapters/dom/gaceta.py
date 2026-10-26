"""DO, Gaceta Oficial issues from consultoria.gov.do.

`GET /api/documents` lists the portal's documents as JSON. Gazette issues carry
`category == "gacetas"`, the issue number as `title`, and a `fileUrl` of
`/uploads/<file>.pdf`, sometimes followed by `|/uploads/<thumb>.jpg`. One
acquired document is one whole issue; ingest segments it into acts.

The listing also names the uploading user. Only the fields read below are kept.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx

from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DiscoveryQuery,
    DocumentRef,
    GazetteRef,
)
from codify.acquisition.politeness import make_polite_client
from codify.jurisdictions import SourceAdapter, authoritative_language

DOCTYPE = "gazette"
_LISTING_PATH = "/api/documents"


def work_uri(year: int, number: str) -> str:
    # AKN's officialGazette document type, as the EU Official Journal refs use.
    return f"/akn/do/officialgazette/{year}/{number}"


def _issue(item: dict[str, Any]) -> dict[str, Any] | None:
    """The fields of one published gazette issue, or None for anything else."""
    if item.get("category") != "gacetas" or item.get("status") != "published":
        return None
    number = str(item.get("title") or "").strip()
    pdf = next(
        (
            p.strip()
            for p in str(item.get("fileUrl") or "").split("|")
            if p.strip().endswith(".pdf")
        ),
        None,
    )
    try:
        year = int(item["year"])
    except (KeyError, TypeError, ValueError):
        return None
    if not number or pdf is None:
        return None
    return {
        "id": str(item.get("id") or ""),
        "number": number,
        "year": year,
        "month": item.get("month"),
        "pdf": pdf,
        "created_at": item.get("createdAt"),
    }


class GacetaAcquirer(BaseAcquirer):
    JURISDICTION: ClassVar[str] = "do"
    DEFAULT_BASE_URL: ClassVar[str] = "https://www.consultoria.gov.do"
    DEFAULT_RATE: ClassVar[int] = 20
    _CONCURRENCY: ClassVar[int] = 1
    _client: httpx.AsyncClient

    def __init__(
        self, adapter: SourceAdapter, *, client: httpx.AsyncClient | None = None
    ) -> None:
        super().__init__(adapter, client=client)
        self._client = client or make_polite_client(adapter, profile=self.politeness)
        self._listing: list[dict[str, Any]] | None = None

    async def issues(self) -> list[dict[str, Any]]:
        """Published issues, newest first; fetched once per acquirer."""
        if self._listing is None:
            resp = await self._client.get(f"{self._base}{_LISTING_PATH}")
            resp.raise_for_status()
            found = [i for i in map(_issue, resp.json()) if i is not None]
            found.sort(
                key=lambda i: (
                    i["year"],
                    int(i["number"]) if i["number"].isdigit() else 0,
                ),
                reverse=True,
            )
            self._listing = found
        return self._listing

    async def discover(self, query: DiscoveryQuery) -> AsyncIterator[DocumentRef]:
        # The source holds whole issues only.
        if query.doctype not in (None, DOCTYPE):
            return
        yielded = 0
        for issue in await self.issues():
            if query.year_from is not None and issue["year"] < query.year_from:
                continue
            if query.year_to is not None and issue["year"] > query.year_to:
                continue
            if query.limit is not None and yielded >= query.limit:
                return
            yielded += 1
            yield DocumentRef(
                jurisdiction_code=self.jurisdiction_code,
                doctype=DOCTYPE,
                year=issue["year"],
                number=issue["number"],
                extra={"source_id": issue["id"]},
            )

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        issue = next(
            (
                i
                for i in await self.issues()
                if i["number"] == ref.number and i["year"] == ref.year
            ),
            None,
        )
        if issue is None:
            raise FileNotFoundError(f"no published Gaceta Oficial issue {ref.number!r}")
        url = f"{self._base}{issue['pdf']}"
        resp = await self._client.get(url)
        if resp.status_code == 404:
            raise FileNotFoundError(
                f"Gaceta Oficial issue {ref.number!r} PDF missing at {url}"
            )
        resp.raise_for_status()
        language = ref.languages[0] if ref.languages else authoritative_language("do")
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=work_uri(issue["year"], issue["number"]),
            bodies=[
                Body(
                    media_type="application/pdf",
                    content=resp.content,
                    role="primary",
                    language=language,
                )
            ],
            source_url=url,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            gazette_ref=GazetteRef(
                name="Gaceta Oficial", year=issue["year"], issue=issue["number"]
            ),
            upstream_metadata={
                "source_id": issue["id"],
                "month": issue["month"],
                "created_at": issue["created_at"],
            },
        )


__all__ = ["DOCTYPE", "GacetaAcquirer", "work_uri"]
