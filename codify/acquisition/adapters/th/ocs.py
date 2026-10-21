"""TH: the Office of the Council of State's public law search, as JSON.

Search pages answer law codes and an opaque timeline id per law; the document
endpoint answers one timeline (a point in time) as per-unit sections with
Gazette and amendment footnotes, plus every timeline of the law:

- type 1: the original text, 2: an amending Act's own text,
- type 3: the consolidated text after an amendment, 4: the current text.

The work URI is the title-derived identity the `th` config declares, so an Act
lands on the work an earlier ingest minted and an amending Act, whose title
carries its edition, on its own. The primary body is the Thai text in unit
order, for the structuring lane; an English translation rides alongside when
the timeline has one, with the translator's note as provenance.
"""

from __future__ import annotations

import html
import json
import re
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, ClassVar

import httpx
import structlog

from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DiscoveryQuery,
    DocumentRef,
    GazetteRef,
)
from codify.acquisition.index import write_index
from codify.acquisition.politeness import make_polite_client
from codify.frbr import build_frbr_work_uri, identity_from_title
from codify.jurisdictions import SourceAdapter, TitleIdentity, load_config
from codify.lang import normalise_digits

SEARCH_URL = "https://www.ocs.go.th/searchlaw/indexs/list_table_search"
DOC_PATH = "/ocs-api/public/doc/getLawDoc"
GAZETTE_NAME = "ราชกิจจานุเบกษา"

logger = structlog.get_logger(__name__)


def user_agent() -> str:
    """The crawler identity this source is sent: the Core release and its home."""
    try:
        release = version("codify-core")
    except PackageNotFoundError:
        release = "0"
    return f"Codify/{release} (+https://github.com/TBI-AI-Incubator/codify)"


TIMELINE_KINDS = {"1": "original", "2": "amending", "3": "consolidated", "4": "current"}

# "ราชกิจจานุเบกษา เล่ม ๑๓๖/ตอนที่ ๖๗ ก/หน้า ๕๗/๒๒ พฤษภาคม ๒๕๖๒", digits folded.
_GAZETTE_NOTE = re.compile(r"เล่ม\s*(\d+)\s*/\s*ตอนที่\s*([^/]+?)\s*/\s*หน้า\s*(\d+)")
_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<br\s*/?>|</p>|</div>", re.IGNORECASE)


class OcsItemMissing(LookupError):
    """The document endpoint did not answer a law for this timeline."""


def section_text(content: str) -> str:
    """Unit HTML as text: one line per paragraph. Tags are removed without a
    space, since the source splits single words across spans."""
    text = html.unescape(_TAG.sub("", _BREAK.sub("\n", content)))
    lines = (" ".join(line.split()) for line in text.replace("\xa0", " ").splitlines())
    return "\n".join(line for line in lines if line)


def render_text(sections: list[dict[str, Any]]) -> str:
    """The units in their stated order, a blank line between units."""
    ordered = sorted(sections, key=lambda s: s.get("sectionSeq") or 0)
    return "\n\n".join(t for s in ordered if (t := section_text(s.get("sectionContent") or "")))


def gazette_ref(footnotes: list[dict[str, Any]], published: str) -> GazetteRef | None:
    """The first footnote citing the Gazette, which is the law's own publication."""
    for note in footnotes:
        content = normalise_digits(str(note.get("footnoteContent") or ""))
        if GAZETTE_NAME in content and (m := _GAZETTE_NOTE.search(content)):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
                return None
            return GazetteRef(
                name=GAZETTE_NAME,
                year=int(published[:4]),
                issue=f"{m.group(1)}/{m.group(2)}",
                page=m.group(3),
                date=published,
            )
    return None


def timelines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every point in time the law has, oldest first, typed by kind."""
    out = [
        {
            "timeline_id": t.get("timelineId", ""),
            "kind": TIMELINE_KINDS.get(str(t.get("timelineTypeId")), "unknown"),
            "law_code": t.get("timelineLawCode", ""),
            "date": t.get("timelineDateAd", ""),
            "updated": t.get("updateDateAd", ""),
            "has_translation": bool(t.get("hasSectionEn")),
        }
        for t in payload.get("timelines") or []
    ]
    return sorted(out, key=lambda t: (t["date"], t["law_code"]))


def strip_marks(text: str) -> str:
    return html.unescape(_TAG.sub("", text or "")).strip()


class OcsAcquirer(BaseAcquirer):
    JURISDICTION: ClassVar[str] = "th"
    DEFAULT_BASE_URL: ClassVar[str] = "https://searchlaw.ocs.go.th"
    DEFAULT_RATE: ClassVar[int] = 12
    _CONCURRENCY: ClassVar[int] = 1
    _client: httpx.AsyncClient

    def __init__(
        self,
        adapter: SourceAdapter,
        *,
        client: httpx.AsyncClient | None = None,
        title_rule: TitleIdentity | None = None,
    ) -> None:
        super().__init__(adapter, client=client)
        self._client = client or make_polite_client(adapter, user_agent=user_agent())
        self._licence = adapter.licence
        self._title_rule = title_rule
        self._session = str(uuid.uuid4())

    def _rule(self) -> TitleIdentity:
        if self._title_rule is None:
            cfg = load_config(self.JURISDICTION)
            rule = cfg.frbr.title_identity if cfg.frbr is not None else None
            if rule is None:
                raise ValueError("the th config declares no frbr.title_identity")
            self._title_rule = rule
        return self._title_rule

    async def _law_doc(self, timeline_id: str, *, english: bool) -> dict[str, Any]:
        now = datetime.now(UTC)
        body = {
            "reqHeader": {
                "reqId": str(int(now.timestamp() * 1000)),
                "reqChannel": "WEB",
                "reqDtm": now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "reqBy": "unknow",
                "serviceName": "getPublicLawDoc",
                "uuid": str(uuid.uuid4()),
                "sessionId": self._session,
            },
            "reqBody": {"isTransEng": english, "timelineId": timeline_id},
        }
        resp = await self._client.post(f"{self._base}{DOC_PATH}", json=body)
        resp.raise_for_status()
        data = resp.json()
        code = (data.get("respHeader") or {}).get("errorCode")
        payload = data.get("respBody")
        if code != "SUCCESS" or not isinstance(payload, dict) or not payload.get("lawInfo"):
            raise OcsItemMissing(f"timeline {timeline_id!r} answered {code!r}")
        return payload

    def work_uri(self, title: str, doctype: str, year: str) -> str:
        identity = identity_from_title(normalise_digits(title), self._rule())
        if not identity.segment:
            raise OcsItemMissing(f"no title identity in {title!r}")
        return build_frbr_work_uri(self.JURISDICTION, doctype, year, identity.segment)

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        timeline_id = ref.extra.get("timeline_id", "")
        if not timeline_id:
            raise ValueError(f"OCS ref {ref.number!r} needs extra['timeline_id']")
        payload = await self._law_doc(timeline_id, english=False)
        info = payload["lawInfo"]
        text = render_text(payload.get("lawSections") or [])
        if not text:
            raise OcsItemMissing(f"timeline {timeline_id!r} carries no text")
        kind = TIMELINE_KINDS.get(str(info.get("timelineTypeId")), "unknown")
        this = next(
            (
                t
                for t in timelines(payload)
                if t["timeline_id"] == timeline_id and t["kind"] == kind
            ),
            None,
        )
        published = str(info.get("publishDateAd") or "")
        # An enactment is dated by its publication; a consolidation by its own date.
        as_of = published if kind in ("original", "amending") else (this or {}).get("date", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", as_of
        ):
            raise OcsItemMissing(f"timeline {timeline_id!r} has no usable date")
        title = str(info.get("lawNameTh") or "")
        work = self.work_uri(title, ref.doctype, published[:4])
        bodies = [Body(media_type="text/plain", content=text.encode(), language="tha")]
        upstream: dict[str, Any] = {
            "law_code": info.get("lawCode", ""),
            "timeline_id": timeline_id,
            "timeline_law_code": info.get("timelineLawCode", ""),
            "timeline_kind": kind,
            "effective_date": info.get("effectiveDateStartAd", ""),
            "state": info.get("stateId", ""),
            "title_en": info.get("lawNameEn") or "",
            "footnotes": len(payload.get("footnoteList") or []),
            "timelines": timelines(payload),
        }
        if this is not None and this["has_translation"]:
            english = await self._law_doc(timeline_id, english=True)
            translation = english.get("lawTranslation") or {}
            en_text = render_text(translation.get("lawSections") or [])
            if en_text:
                bodies.append(
                    Body(
                        media_type="text/plain",
                        content=en_text.encode(),
                        role="translation",
                        language="eng",
                    )
                )
                # The translator's own statement of who made it and its standing.
                upstream["translation_note"] = next(
                    (
                        strip_marks(str(n.get("footnoteContent") or ""))
                        for n in translation.get("footnoteList") or []
                        if "translation" in str(n.get("footnoteContent") or "").lower()
                    ),
                    "",
                )
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=work,
            expression_uri=f"{work}/tha@{as_of}",
            bodies=bodies,
            source_url=f"{self._base}/council-of-state/#/public/doc/{timeline_id}",
            fetched_at=datetime.now(UTC),
            licence=self._licence,
            citation_alt=title or None,
            gazette_ref=gazette_ref(payload.get("footnoteList") or [], published),
            upstream_metadata=upstream,
        )

    async def search(
        self, keyword: str, *, page: int, per_page: int = 50, states: str = "01,02"
    ) -> dict[str, Any]:
        """One page of the public search over law titles; `states` "" is every state."""
        form = {
            "query[letter]": "",
            "query[tab_type]": "law",
            "query[q]": keyword,
            "query[sort]": "default",
            "query[topic]": "1",
            "query[content]": "0",
            "query[sublaw]": "0",
            "query[synonyms]": "0",
            "query[lawCategoryName]": "",
            "query[stateName]": states,
            "query[year]": "",
            "query[onlyMatchChild]": "1",
            "pagination[page]": str(page),
            "pagination[perpage]": str(per_page),
        }
        resp = await self._client.post(SEARCH_URL, data=form)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def discover(self, query: DiscoveryQuery) -> AsyncIterator[DocumentRef]:
        """Refs for the current timeline of each law whose title matches
        `query.q`, within the year bounds."""
        keyword = str(getattr(query, "q", "") or "")
        if not keyword:
            raise ValueError("OCS discovery needs DiscoveryQuery.q, a title keyword")
        yielded, page = 0, 1
        while True:
            data = await self.search(keyword, page=page)
            for hit in data.get("data") or []:
                year = int(hit.get("year") or 0)
                if (query.year_from and year < query.year_from) or (
                    query.year_to and year > query.year_to
                ):
                    continue
                yield DocumentRef(
                    jurisdiction_code=self.jurisdiction_code,
                    doctype=query.doctype or "act",
                    year=year,
                    number=str(hit.get("lawCode") or ""),
                    languages=["tha"],
                    extra={
                        "timeline_id": str(hit.get("encTimelineID") or ""),
                        "title": strip_marks(str(hit.get("lawNameTh") or "")),
                    },
                )
                yielded += 1
                if query.limit and yielded >= query.limit:
                    return
            pages = int((data.get("meta") or {}).get("pages") or 0)
            if page >= pages:
                return
            page += 1


def index_entry(hit: dict[str, Any]) -> dict[str, Any]:
    """One search hit as an index entry, with the subordinate laws listed under it."""
    return {
        "law_code": str(hit.get("lawCode") or ""),
        "timeline_id": str(hit.get("encTimelineID") or ""),
        "title": strip_marks(str(hit.get("lawNameTh") or "")),
        "year": int(hit.get("year") or 0),
        "state": str(hit.get("state") or ""),
        "english": bool(hit.get("lawEn")),
        "children": [
            {
                "type": strip_marks(str(group.get("name") or "")),
                "title": strip_marks(str(item.get("title") or "")),
                "timeline_id": str(item.get("encTimelineID") or ""),
                "date": str(item.get("date") or ""),
                "state": str(item.get("state") or ""),
                "english": bool(item.get("lawEn")),
            }
            for group in hit.get("childrens") or []
            for item in group.get("items") or []
        ],
    }


class IndexMoved(RuntimeError):
    """The catalogue's total changed mid-walk, so its pages shifted under us."""


def _index_payload(
    entries: dict[str, dict[str, Any]],
    total: int,
    per_page: int,
    done: int,
    *,
    complete: bool,
) -> dict[str, Any]:
    laws = list(entries.values())
    children = {c["timeline_id"]: c for e in laws for c in e["children"] if c["timeline_id"]}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": SEARCH_URL,
        "total": total,
        "per_page": per_page,
        "pages_done": done,
        # False on every checkpoint; a reader must not sample a build still running.
        "complete": complete,
        "entries": laws,
        "stats": {
            "laws": len(laws),
            "missing": total - len(laws),
            "children": len(children),
            "child_types": dict(Counter(c["type"] for c in children.values())),
            "states": dict(Counter(e["state"] for e in laws)),
            "english": sum(e["english"] for e in laws),
        },
    }


async def build_index(
    acquirer: OcsAcquirer,
    *,
    per_page: int = 50,
    checkpoint: Path | None = None,
    resume: bool = False,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> dict[str, Any]:
    """Walk every page of the unfiltered search, every state included. With
    `checkpoint` the payload is written after each page; with `resume` a build
    there over the same total and page size continues where it stopped."""
    first = await acquirer.search("", page=1, per_page=per_page, states="")
    meta = first.get("meta") or {}
    total, pages = int(meta.get("total") or 0), int(meta.get("pages") or 0)
    entries: dict[str, dict[str, Any]] = {}
    page = 1
    if resume and checkpoint is not None and checkpoint.exists():
        prior = json.loads(checkpoint.read_text())
        if prior.get("total") == total and prior.get("per_page") == per_page:
            entries = {e["law_code"]: e for e in prior.get("entries") or []}
            page = int(prior.get("pages_done") or 0) + 1
            logger.info("ocs_index_resumed", page=page, laws=len(entries))
        else:
            logger.info("ocs_index_restarted", was=prior.get("total"), now=total)
    data: dict[str, Any] | None = first if page == 1 else None
    while page <= pages:
        data = data or await acquirer.search("", page=page, per_page=per_page, states="")
        now = int((data.get("meta") or {}).get("total") or 0)
        if now != total:
            raise IndexMoved(f"catalogue total moved from {total} to {now} at page {page}")
        for hit in data.get("data") or []:
            entry = index_entry(hit)
            if entry["law_code"] and entry["timeline_id"]:
                entries.setdefault(entry["law_code"], entry)
        if checkpoint is not None:
            write_index(
                checkpoint,
                _index_payload(entries, total, per_page, page, complete=False),
            )
        if on_progress is not None:
            on_progress(page, pages, len(entries))
        page, data = page + 1, None
    return _index_payload(entries, total, per_page, pages, complete=True)


__all__ = [
    "GAZETTE_NAME",
    "IndexMoved",
    "OcsAcquirer",
    "OcsItemMissing",
    "TIMELINE_KINDS",
    "build_index",
    "gazette_ref",
    "index_entry",
    "render_text",
    "section_text",
    "timelines",
]
