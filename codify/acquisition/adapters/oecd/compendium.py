"""Read OECD legal instruments from the Compendium's JSON API.

`legalinstruments.oecd.org` serves an unauthenticated JSON API behind its
single-page app. `GET /api/instruments?lang=en&statusIds=1` lists every
instrument in force with its `lastPublishDate`; `GET /api/instruments/{KEY}`
returns one instrument with bilingual title, type, themes, committee, related
instruments, adherents and monitoring reports, and names the English and French
body text at `/public/doc/{id}/body-text.{lang}.html`.

The primary body is a self-contained HTML document the acquirer assembles: the
compendium's body text under a `<head>` carrying the title, key, type and
adoption date as `<meta>` tags, since the body file alone opens with
"THE COUNCIL," and says nothing about which instrument it is. The format module
(`codify.pipeline.formats.oecd_html`) reads those tags back.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from html import escape
from typing import Any, ClassVar

import httpx
import structlog

from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DiscoveryQuery,
    DocumentRef,
)
from codify.acquisition.politeness import make_polite_client
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter

logger = structlog.get_logger()

# Compendium type id → document class in data/jurisdictions/oecd/config.json.
# 5 is the residual "Arrangement, Understanding and Others" category.
TYPE_TO_DOCTYPE: dict[int, str] = {
    1: "decision",
    2: "recommendation",
    3: "declaration",
    4: "agreement",
    5: "arrangement",
}
DOCTYPE_TO_TYPE: dict[str, int] = {v: k for k, v in TYPE_TO_DOCTYPE.items()}
STATUS_IN_FORCE = 1
LANGUAGES: dict[str, str] = {"en": "eng", "fr": "fra"}


class OecdInstrumentMissing(KeyError):
    """The API knows no instrument by that key."""


def instrument_key(number: str) -> str:
    """`"406"` or `"0406"` → `OECD/LEGAL/0406`; the API path form is `OECD-LEGAL-0406`."""
    return f"OECD/LEGAL/{int(number):04d}"


def _lang_value(node: Any, lang: str = "en") -> str | None:
    """Pick one language from the API's `[{lang, value}]` lists."""
    if isinstance(node, dict):
        node = node.get("name") or node.get("text") or []
    for entry in node or []:
        if isinstance(entry, dict) and entry.get("lang") == lang:
            value = entry.get("value")
            return str(value) if value is not None else None
    return None


def _year(iso: str | None) -> int:
    return date.fromisoformat(iso).year if iso else 0


def wrap_body(body_html: str, *, meta: dict[str, str]) -> bytes:
    """The body text under a head that names the instrument; the format module's input."""
    tags = "".join(
        f'<meta name="oecd.{escape(k)}" content="{escape(v, quote=True)}">'
        for k, v in meta.items()
        if v
    )
    title = escape(meta.get("title", ""))
    lang = escape(meta.get("lang", "en"))
    page = (
        f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">'
        f"<title>{title}</title>{tags}</head><body>{body_html}</body></html>"
    )
    return page.encode("utf-8")


class OecdCompendiumAcquirer(BaseAcquirer):
    JURISDICTION: ClassVar[str] = "oecd"
    DEFAULT_BASE_URL: ClassVar[str] = "https://legalinstruments.oecd.org"
    DEFAULT_RATE: ClassVar[int] = 60
    _CONCURRENCY: ClassVar[int] = 2
    _client: httpx.AsyncClient

    def __init__(self, adapter: SourceAdapter, *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(adapter, client=client)
        self._client = client or make_polite_client(adapter)

    async def _json(self, path: str, **params: str) -> Any:
        resp = await self._client.get(f"{self._base}{path}", params={"lang": "en", **params})
        resp.raise_for_status()
        return resp.json()

    async def discover(self, query: DiscoveryQuery) -> AsyncIterator[DocumentRef]:
        """Every instrument in force, or one class of them; `limit` caps the walk.

        `extra` carries the key, title and `lastPublishDate`, so a caller can skip
        the fetch for an instrument it already holds at that publish date.
        """
        params: dict[str, str] = {"statusIds": str(STATUS_IN_FORCE)}
        if query.doctype:
            type_id = DOCTYPE_TO_TYPE.get(query.doctype)
            if type_id is None:
                return
            params["typeIds"] = str(type_id)
        rows = await self._json("/api/instruments", **params)
        emitted = 0
        for row in rows:
            key = str(row.get("key") or "")
            if not key.startswith("OECD/LEGAL/"):
                continue
            adopted = row.get("adoptionDate")
            year = _year(adopted)
            if query.year_from and year < query.year_from:
                continue
            if query.year_to and year > query.year_to:
                continue
            type_id = int((row.get("type") or {}).get("id") or 0)
            yield DocumentRef(
                jurisdiction_code=self.JURISDICTION,
                doctype=TYPE_TO_DOCTYPE.get(type_id, "arrangement"),
                year=year,
                number=key.rsplit("/", 1)[-1],
                languages=list(LANGUAGES.values()),
                extra={
                    "key": key,
                    "title": str(row.get("title") or "").strip(),
                    "adopted": adopted or "",
                    "last_published": str(row.get("lastPublishDate") or ""),
                },
            )
            emitted += 1
            if query.limit and emitted >= query.limit:
                return

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        key = instrument_key(ref.number)
        path_key = key.replace("/", "-")
        try:
            record = await self._json(f"/api/instruments/{path_key}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise OecdInstrumentMissing(key) from exc
            raise
        if not isinstance(record, dict) or record.get("key") != key:
            raise OecdInstrumentMissing(key)

        type_id = int((record.get("type") or {}).get("id") or 0)
        doctype = TYPE_TO_DOCTYPE.get(type_id, ref.doctype)
        summary = record.get("statusSummary") or {}
        adopted = str(summary.get("adoptionDate") or ref.extra.get("adopted") or "")
        year = _year(adopted) or ref.year
        titles = {lang: _lang_value(record.get("title"), lang) or "" for lang in LANGUAGES}
        refs = (record.get("bodyText") or {}).get("ref") or []
        bodies: list[Body] = []
        for entry in refs:
            lang = str(entry.get("lang") or "")
            uri = entry.get("uri")
            if lang not in LANGUAGES or not uri or entry.get("format") != "html":
                continue
            resp = await self._client.get(f"{self._base}{uri}")
            resp.raise_for_status()
            page = wrap_body(
                resp.text,
                meta={
                    "key": key,
                    "type": doctype,
                    "adopted": adopted,
                    "title": titles[lang],
                    "lang": lang,
                },
            )
            bodies.append(
                Body(
                    content=page,
                    media_type="text/html",
                    role="primary" if lang == "en" else "translation",
                    language=LANGUAGES[lang],
                )
            )
        if not bodies:
            raise OecdInstrumentMissing(f"{key} has no HTML body text")
        number = key.rsplit("/", 1)[-1]
        return AcquiredDocument(
            ref=ref.model_copy(update={"doctype": doctype, "year": year, "number": number}),
            source_url=f"{self._base}/en/instruments/{path_key}",
            bodies=bodies,
            frbr_work_uri=build_frbr_work_uri(self.JURISDICTION, doctype, year, number),
            licence="oecd_terms",
            last_modified=str(record.get("transmittedAt") or "") or None,
            upstream_metadata={
                "key": key,
                "type_id": type_id,
                "status_id": int((record.get("status") or {}).get("id") or 0),
                "adoption_date": adopted or None,
                "in_force_date": summary.get("inForceDate"),
                "title_fr": titles["fr"],
                "themes": [t.get("id") for t in (record.get("themes") or {}).get("theme") or []],
                "committees": [
                    c.get("id")
                    for c in (record.get("committees") or {}).get("parentCommittee") or []
                ],
                "related": [
                    r.get("key") for r in (record.get("relations") or {}).get("relatedTo") or []
                ],
                # adherenceTypeId 1 is a Member; 2 a non-Member adherent, dated. A
                # candidate adhering before accession is a signal for screening.
                "adherence": [
                    {
                        "country_id": c.get("id"),
                        "adherence_type_id": c.get("adherenceTypeId"),
                        "date": c.get("date"),
                    }
                    for c in (summary.get("adherents") or {}).get("countryRef") or []
                ],
                "change_history": [
                    {
                        "date": u.get("date"),
                        "type_id": (u.get("type") or {}).get("id"),
                        "olis": [o.get("uri") for o in u.get("OLISRef") or []],
                    }
                    for u in (record.get("changeHistory") or {}).get("update") or []
                ],
                "monitoring": [
                    {
                        "year": e.get("year"),
                        "olis": [o.get("uri") for o in e.get("OLISRef") or []],
                    }
                    for e in (record.get("monitoring") or {}).get("entry") or []
                ],
            },
        )


__all__ = [
    "DOCTYPE_TO_TYPE",
    "TYPE_TO_DOCTYPE",
    "OecdCompendiumAcquirer",
    "OecdInstrumentMissing",
    "instrument_key",
    "wrap_body",
]
