"""AL, qbz.gov.al gazette adapter (Alfresco-backed).

QBZ migrated to an Alfresco CMS exposed through a public REST API; the old
``/eli/...`` direct-PDF routes now return the SPA shell, so this adapter
drives the Alfresco **search** + **content** endpoints instead:

  - search by act-type facet (``qbz:actActType`` = ligj) + filename
    (``ligj-{YYYY-MM-DD}-{number}.pdf``), preferring the consolidated
    ("i përditësuar") rendition, clean text, not scanned.
  - download the resulting node's content.

Auth is a fixed public read token the SPA ships to every visitor; override
via the ``QBZ_AUTH`` env var or the adapter's ``auth`` note if it rotates.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DiscoveryQuery,
    DocumentRef,
)
from codify.acquisition.politeness import make_polite_client
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter, authoritative_language

_SEARCH_PATH = "/alfresco/api/-default-/public/search/versions/1/search"
_CONTENT_PATH = "/alfresco/api/-default-/public/alfresco/versions/1/nodes/{id}/content"
_TYPE_URI = "http://qbz.gov.al/resource/authority/document-type/{}"
_LIGJ_TYPE = _TYPE_URI.format("ligj")
# QBZ act types we ingest. `ligj` = law (framework); `vendim` = Council-of-
# Ministers decision (VKM, the implementing secondary legislation); `udhezim`
# = instruction; `urdher` = order. All share the `{type}-{YYYY}-{MM}-{DD}-{num}`
# filename shape and the `qbz:actActType` facet.
ACT_TYPES: frozenset[str] = frozenset({"ligj", "vendim", "udhezim", "urdher"})
# Public read token the QBZ SPA sends on every request (not a secret).
_DEFAULT_AUTH = (
    "Basic Z2V0TmFtZShxYnpyZWFkKTpERUNSKDEzQzgwMDVDODk3MUI0MjZBNjg0MUE2ODE3N0VG"
    "ODJDQTNBMEI0RjI3NzZEMUIxQkY1QUNCNzEyRjM4OTAwQkIp"
)


def frbr_for(year: int, number: str, act_type: str = "ligj") -> str:
    return build_frbr_work_uri("al", act_type, year, number)


def _name_re(act_type: str) -> re.Pattern[str]:
    # Primary node filename: `{type}-{YYYY}-{MM}-{DD}-{number}[ suffix].{ext}`.
    # Year+number (off the name) drive the FRBR and the per-doc resolve.
    return re.compile(rf"^{re.escape(act_type)}-(\d{{4}})-\d{{2}}-\d{{2}}-(\d+)\b")


class QbzAcquirer(BaseAcquirer):
    JURISDICTION: ClassVar[str] = "al"
    DEFAULT_BASE_URL: ClassVar[str] = "https://qbz.gov.al"
    DEFAULT_RATE: ClassVar[int] = 6  # QBZ WAF requires a slow request cadence.
    _CONCURRENCY: ClassVar[int] = 1
    _client: httpx.AsyncClient

    def __init__(
        self,
        adapter: SourceAdapter,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(adapter, client=client)
        self._source = adapter  # typed access to discovery config
        self._client = client or make_polite_client(adapter)
        self._auth = os.environ.get("QBZ_AUTH") or _DEFAULT_AUTH

    async def _search(
        self, query: str, *, max_items: int = 25, ligj_only: bool = True
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "query": {"query": query, "language": "afts"},
            "paging": {"skipCount": 0, "maxItems": max_items},
        }
        # Renditions lack the act-type facet, so prefix lookups stay unfiltered.
        if ligj_only:
            body["filterQueries"] = [{"query": f'qbz:actActType:"{_LIGJ_TYPE}"'}]
        resp = await self._client.post(
            f"{self._base}{_SEARCH_PATH}",
            headers={"authorization": self._auth, "accept": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        return [e["entry"] for e in resp.json()["list"]["entries"]]

    async def discover_acts(
        self,
        title_query: str,
        *,
        act_type: str = "ligj",
        page_size: int = 100,
        max_total: int = 5000,
    ) -> list[dict[str, Any]]:
        """Enumerate acts of `act_type` (ligj / vendim / udhezim / urdher) whose
        title matches `title_query` (an AFTS fragment, e.g.
        ``cm:title:(prokurim* OR koncesion*)``), paginating the Alfresco search.
        Returns one entry per distinct (year, number), the act-type facet only
        tags the primary node, so each act appears once. The basket feeds a bulk
        ingest; per-doc fetch still re-resolves to the consolidated rendition.
        """
        if act_type not in ACT_TYPES:
            raise ValueError(f"unknown act_type {act_type!r}; expected one of {sorted(ACT_TYPES)}")
        type_uri = _TYPE_URI.format(act_type)
        name_re = _name_re(act_type)
        out: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        skip = 0
        while skip < max_total:
            body: dict[str, Any] = {
                "query": {"query": title_query, "language": "afts"},
                "filterQueries": [{"query": f'qbz:actActType:"{type_uri}"'}],
                "include": ["properties"],
                "paging": {"skipCount": skip, "maxItems": page_size},
            }
            resp = await self._client.post(
                f"{self._base}{_SEARCH_PATH}",
                headers={"authorization": self._auth, "accept": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            lst = resp.json()["list"]
            entries = lst["entries"]
            for e in entries:
                node = e["entry"]
                m = name_re.match(node.get("name", ""))
                if not m:
                    continue
                year, number = int(m.group(1)), m.group(2)
                if (year, number) in seen:
                    continue
                seen.add((year, number))
                props = node.get("properties", {}) or {}
                out.append(
                    {
                        "doctype": act_type,
                        "year": year,
                        "number": number,
                        "title": props.get("qbz:actTitle") or props.get("cm:title") or "",
                        "act_date": props.get("qbz:actDate"),
                        "name": node.get("name"),
                        "node_id": node.get("id"),
                        "frbr": frbr_for(year, number, act_type),
                    }
                )
            skip += len(entries)
            if not entries or not lst["pagination"].get("hasMoreItems"):
                break
        return out

    async def discover(self, query: DiscoveryQuery) -> AsyncIterator[DocumentRef]:
        """Protocol discovery: the per-doctype AFTS title query comes from the
        adapter's `discovery.queries` config; yields one ref per matched act."""
        disc = self._source.discovery
        if disc is None:
            raise ValueError("al/qbz adapter has no discovery config")
        if not query.doctype:
            raise ValueError("DiscoveryQuery.doctype is required for al/qbz")
        afts = disc.queries.get(query.doctype)
        if afts is None:
            raise ValueError(f"no discovery query for doctype {query.doctype!r}")
        found = await self.discover_acts(
            afts, act_type=query.doctype, max_total=query.limit or disc.max_total
        )
        for entry in found:
            yield DocumentRef(
                jurisdiction_code=self.jurisdiction_code,
                doctype=query.doctype,
                year=int(entry["year"]),
                number=str(entry["number"]),
                extra={"title": entry.get("title") or ""},
            )

    async def _resolve_node(self, ref: DocumentRef) -> dict[str, Any]:
        """Find the best QBZ node for a (year, number) act: prefer the
        consolidated PDF, then the original PDF. The act type comes from
        ``ref.doctype`` (ligj / vendim / …).

        Filenames are ``{type}-{YYYY-MM-DD}-{number}[ suffix].{ext}``. We don't
        know the day, so first match on tokenised name parts to discover the
        dated prefix, then pull every rendition of that act.
        """
        act_type = ref.doctype or "ligj"
        # Name query carries the act type, so don't also force the `ligj` facet
        # (that would filter a vendim/udhezim lookup down to nothing).
        hits = await self._search(
            f"cm:name:{act_type} AND cm:name:{ref.year} AND cm:name:{ref.number}",
            ligj_only=False,
        )
        prefix_re = re.escape(f"{act_type}-{ref.year}")
        num_re = re.escape(ref.number)
        name_re = re.compile(rf"({prefix_re}-\d\d-\d\d-{num_re})\b")
        prefix = None
        for e in hits:
            m = name_re.match(e.get("name", ""))
            if m:
                prefix = m.group(1)
                break
        if prefix is None:
            raise FileNotFoundError(f"no QBZ {act_type} node for {ref.year}/{ref.number}")
        renditions = await self._search(f"cm:name:{prefix}*", ligj_only=False)
        pdfs = [e for e in renditions if e.get("content", {}).get("mimeType") == "application/pdf"]
        if not pdfs:
            raise FileNotFoundError(f"no PDF rendition for QBZ {act_type} {ref.year}/{ref.number}")
        # Consolidated rendition first ("i përditësuar"), newest if several.
        pdfs.sort(key=lambda e: ("përditësuar" in e["name"], e.get("modifiedAt", "")), reverse=True)
        return pdfs[0]

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        node = await self._resolve_node(ref)
        url = f"{self._base}{_CONTENT_PATH.format(id=node['id'])}?attachment=true"
        resp = await self._client.get(url, headers={"authorization": self._auth})
        resp.raise_for_status()
        bodies = [
            Body(
                media_type="application/pdf",
                content=resp.content,
                role="primary",
                language=ref.languages[0]
                if ref.languages
                else authoritative_language(ref.jurisdiction_code),
            )
        ]
        consolidated = "përditësuar" in node.get("name", "")
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=frbr_for(ref.year, ref.number, ref.doctype or "ligj"),
            bodies=bodies,
            source_url=url,
            fetched_at=datetime.now(UTC),
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            licence=None,
            gazette_ref=None,
            # Prefer consolidated born-digital renditions; older originals may scan.
            ocr_required=ref.year < 2010 and not consolidated,
            upstream_metadata={"qbz_node_id": node["id"], "consolidated": consolidated},
        )


__all__ = ["QbzAcquirer", "frbr_for"]
