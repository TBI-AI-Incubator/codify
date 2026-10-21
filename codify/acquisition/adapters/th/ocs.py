"""TH: the Office of the Council of State's public law search, as JSON.

Search pages answer law codes and an opaque timeline id per law; the document
endpoint answers one timeline (a point in time) as per-unit sections with
Gazette and amendment footnotes, plus every timeline of the law:

- type 1: the original text, 2: an amending Act's own text,
- type 3: the consolidated text after an amendment, 4: the current text.

The work URI is the title-derived identity the `th` config declares, so an Act
lands on the work an earlier ingest minted and an amending Act, whose title
carries its edition, on its own. A ref may ask for the law-code identity
instead, for laws whose titles do not tell them apart. The primary body is the
Thai text in unit order, for the structuring lane; an English translation rides
alongside when the timeline has one, with the translator's note as provenance.
"""

from __future__ import annotations

import html
import json
import os
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
from codify.acquisition.index import index_path, write_index
from codify.acquisition.politeness import make_polite_client
from codify.calendar import local_date_from_text
from codify.frbr import build_frbr_work_uri, identity_from_code, identity_from_title
from codify.jurisdictions import SourceAdapter, TitleIdentity, load_config, try_load_config
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

# Title prefix to document class, longest first. Council announcements class as
# notifications, as the works already ingested from Gazette scans do.
DOCTYPE_PREFIXES = (
    ("พระราชบัญญัติประกอบรัฐธรรมนูญ", "organic_act"),
    ("พระราชบัญญัติ", "act"),
    ("พระราชกำหนด", "emergency_decree"),
    ("พระราชกฤษฎีกา", "royal_decree"),
    ("ประมวลกฎหมาย", "code"),
    ("ประมวลรัษฎากร", "code"),
    ("ธรรมนูญการปกครอง", "constitution"),
    ("รัฐธรรมนูญ", "constitution"),
    ("กฎกระทรวง", "ministerial_regulation"),
    ("คำสั่งหัวหน้าคณะ", "council_order"),
    ("คำสั่งคณะ", "council_order"),
    ("ประกาศ", "notification"),
    ("ระเบียบ", "regulation"),
    ("ข้อบังคับ", "rule"),
    ("ข้อกำหนด", "requirement"),
    ("คำสั่ง", "order"),
    ("กฎ", "commission_rule"),
)


def doctype_for_title(title: str) -> str | None:
    """The document class a Thai title's leading term names, or None."""
    head = title.strip()
    return next((doctype for prefix, doctype in DOCTYPE_PREFIXES if head.startswith(prefix)), None)


# "ราชกิจจานุเบกษา เล่ม ๑๓๖/ตอนที่ ๖๗ ก/หน้า ๕๗/๒๒ พฤษภาคม ๒๕๖๒", digits folded.
# "เล่ม 136/ตอนที่ 67 ก/หน้า 57", "…/ตอนพิเศษ 85 ก/…", "…/ตอนที่ 33/ฉบับพิเศษ หน้า 8".
_GAZETTE_NOTE = re.compile(
    r"เล่ม\s*(\d+)\s*/\s*ตอน(?:ที่)?\s*([^/]+?)\s*/\s*(?:ฉบับพิเศษ\s*)?หน้า\s*(\d+)"
)
_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<br\s*/?>|</(?:p|div|td|th|tr|li)>", re.IGNORECASE)


class OcsItemMissing(LookupError):
    """The document endpoint did not answer a law for this timeline."""


def section_text(content: str) -> str:
    """Unit HTML as text: one line per paragraph. Tags are removed without a
    space, since the source splits single words across spans."""
    text = html.unescape(_TAG.sub("", _BREAK.sub("\n", content)))
    lines = (" ".join(line.split()) for line in text.replace("\xa0", " ").splitlines())
    return "\n".join(line for line in lines if line)


_GAZETTE_SITE = "ratchakitcha.soc.go.th"


def holds_text(payload: dict[str, Any]) -> bool:
    """Whether the source holds the law's text. Some laws it holds only as a scan
    (no units), others only as a pointer to the Gazette's own site."""
    for unit in payload.get("lawSections") or []:
        html_ = str(unit.get("sectionContent") or "")
        text = section_text(html_)
        if str(unit.get("sectionTypeId")) != "1" and _GAZETTE_SITE not in text:
            if text or "<img" in html_.lower():
                return True
    return False


def render_text(sections: list[dict[str, Any]]) -> str:
    """The units in their stated order, a blank line between units."""
    ordered = sorted(sections, key=lambda s: s.get("sectionSeq") or 0)
    return "\n\n".join(t for s in ordered if (t := section_text(s.get("sectionContent") or "")))


def own_gazette_note(footnotes: list[dict[str, Any]]) -> str:
    """The first footnote citing the Gazette, which is the law's own publication."""
    notes = (normalise_digits(str(n.get("footnoteContent") or "")) for n in footnotes)
    return next((n for n in notes if GAZETTE_NAME in n), "")


def publication_date(info: dict[str, Any], footnotes: list[dict[str, Any]]) -> str:
    """The earlier of the record's date and its own Gazette note's: a consolidation's
    record can carry a later act's date (an Act of 1979 dated 1991)."""
    # ponytail: a note misdated earlier than the law wins; one in ~3,000 today.
    stated = str(info.get("publishDateAd") or "")
    cfg = try_load_config("th")
    rule = cfg.frbr.calendar_conversion if cfg is not None and cfg.frbr is not None else None
    note = own_gazette_note(footnotes)
    if not (note and rule and rule.date_cues):
        return stated
    noted = local_date_from_text(f"{rule.date_cues[0]} {note.rsplit('/', 1)[-1]}", "th")
    if noted is None or (
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", stated) and noted.isoformat() >= stated
    ):
        return stated
    return noted.isoformat()


def gazette_ref(footnotes: list[dict[str, Any]], published: str) -> GazetteRef | None:
    """The law's own Gazette note as a reference; None when that note is in a form
    not read, never a later citation's."""
    m = _GAZETTE_NOTE.search(own_gazette_note(footnotes))
    if m is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
        return None
    return GazetteRef(
        name=GAZETTE_NAME,
        year=int(published[:4]),
        issue=f"{m.group(1)}/{m.group(2)}",
        page=m.group(3),
        date=published,
    )


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


# The `ref.extra["identity"]` asking for a work by law code, not by title.
LAW_CODE_IDENTITY = "law_code"


def law_code_number(info: dict[str, Any]) -> str:
    """A work number from the source's code for a document: its law's, or an amending
    text's own timeline code, since that text is another act and not the law's."""
    amending = TIMELINE_KINDS.get(str(info.get("timelineTypeId"))) == "amending"
    number = identity_from_code(str(info.get("timelineLawCode" if amending else "lawCode") or ""))
    if not number:
        raise OcsItemMissing(f"timeline {info.get('timelineId')!r} names no law code")
    return number


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
        dump_dir: Path | None = None,
    ) -> None:
        super().__init__(adapter, client=client)
        self._client = client or make_polite_client(
            adapter, user_agent=user_agent(), profile=self.politeness
        )
        self._licence = adapter.licence
        self._title_rule = title_rule
        self._session = str(uuid.uuid4())
        # A crawl's raw documents, read before the network is asked.
        self._dump = dump_dir
        self._dump_absent_logged = False

    @classmethod
    def from_config(cls, jurisdiction_code: str, adapter: object) -> OcsAcquirer:
        """As configured, reading a crawl saved beside the index before the network."""
        if jurisdiction_code != cls.JURISDICTION:
            raise ValueError(
                f"OcsAcquirer only supports {cls.JURISDICTION!r}, got {jurisdiction_code!r}"
            )
        assert isinstance(adapter, SourceAdapter)
        return cls(adapter, dump_dir=index_path(cls.JURISDICTION).parent / "raw")

    def _rule(self) -> TitleIdentity:
        if self._title_rule is None:
            cfg = load_config(self.JURISDICTION)
            rule = cfg.frbr.title_identity if cfg.frbr is not None else None
            if rule is None:
                raise ValueError("the th config declares no frbr.title_identity")
            self._title_rule = rule
        return self._title_rule

    def _saved(self, timeline_id: str, *, english: bool) -> tuple[dict[str, Any], float] | None:
        """A crawl's saved document and when it was saved, or None."""
        if self._dump is None:
            return None
        if not self._dump.is_dir():
            if not self._dump_absent_logged:
                logger.info("ocs_dump_absent", path=str(self._dump.resolve()))
                self._dump_absent_logged = True
            return None
        saved = self._dump / dump_name(timeline_id, english=english)
        if not saved.exists():
            return None
        try:
            return dict(json.loads(saved.read_text())), saved.stat().st_mtime
        except ValueError as exc:
            logger.warning("ocs_dump_unreadable", path=str(saved), detail=str(exc))
            return None

    async def _law_doc(self, timeline_id: str, *, english: bool) -> dict[str, Any]:
        saved = self._saved(timeline_id, english=english)
        if saved is not None:
            return saved[0]
        return await self._fetch_doc(timeline_id, english=english)

    async def _fetch_doc(self, timeline_id: str, *, english: bool) -> dict[str, Any]:
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
        try:
            data = resp.json()
        except ValueError as exc:
            # A maintenance or firewall page answers 200 with HTML.
            raise OcsItemMissing(f"timeline {timeline_id!r} answered no JSON") from exc
        code = (data.get("respHeader") or {}).get("errorCode")
        payload = data.get("respBody")
        what = "English" if english else "Thai"
        if code != "SUCCESS" or not isinstance(payload, dict) or not payload.get("lawInfo"):
            raise OcsItemMissing(f"timeline {timeline_id!r} ({what}) answered {code!r}")
        return payload

    def work_number(self, title: str) -> str:
        identity = identity_from_title(normalise_digits(title), self._rule())
        if not identity.segment:
            raise OcsItemMissing(f"no title identity in {title!r}")
        return identity.segment

    def work_uri(self, title: str, doctype: str, year: str) -> str:
        return build_frbr_work_uri(self.JURISDICTION, doctype, year, self.work_number(title))

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        timeline_id = ref.extra.get("timeline_id", "")
        if not timeline_id:
            raise ValueError(f"OCS ref {ref.number!r} needs extra['timeline_id']")
        identity = ref.extra.get("identity", "")
        if identity not in ("", LAW_CODE_IDENTITY):
            # Read as the title, a misspelt ask would file the law on the work it shares.
            raise ValueError(f"OCS ref {ref.number!r} asks for an unknown identity {identity!r}")
        saved = self._saved(timeline_id, english=False)
        payload = saved[0] if saved else await self._fetch_doc(timeline_id, english=False)
        info = payload["lawInfo"]
        if not holds_text(payload):
            raise OcsItemMissing(f"timeline {timeline_id!r} carries no text")
        kind = TIMELINE_KINDS.get(str(info.get("timelineTypeId")), "unknown")
        if kind == "unknown":
            raise OcsItemMissing(f"timeline {timeline_id!r} is of an unread kind")
        this = next(
            (
                t
                for t in timelines(payload)
                if t["timeline_id"] == timeline_id and t["kind"] == kind
            ),
            None,
        )
        published = publication_date(info, payload.get("footnoteList") or [])
        # An enactment is dated by its publication; a consolidation by its own date.
        as_of = published if kind in ("original", "amending") else (this or {}).get("date", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", as_of
        ):
            raise OcsItemMissing(f"timeline {timeline_id!r} has no usable date")
        title = str(info.get("lawNameTh") or "")
        by_code = identity == LAW_CODE_IDENTITY
        number = law_code_number(info) if by_code else self.work_number(title)
        work = build_frbr_work_uri(self.JURISDICTION, ref.doctype, published[:4], number)
        from codify.acquisition.adapters.th.ocs_akn import to_akn
        from codify.pipeline.formats.eu_directive import on_the_cpu_pool

        # Bluebell parsing is CPU-bound; it runs on the pipeline's pool, not the loop.
        akn = await on_the_cpu_pool(
            lambda: to_akn(
                payload.get("lawSections") or [],
                doctype=ref.doctype,
                number=number,
                work=work,
                published=published,
                expression=f"{work}/tha@{as_of}",
                as_of=as_of,
                title=title,
            )
        )
        bodies = [
            Body(media_type="application/xml", content=akn.encode(), language="tha"),
            # The source document itself, so the conversion and its footnotes can be audited.
            Body(
                media_type="application/json",
                content=json.dumps(payload, ensure_ascii=False).encode(),
                role="card",
            ),
        ]
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
            "from_crawl": saved is not None,
        }
        if any(
            t["timeline_id"] == timeline_id and t["has_translation"] for t in timelines(payload)
        ):
            try:
                english = await self._law_doc(timeline_id, english=True)
            except (OcsItemMissing, httpx.HTTPError) as exc:
                # The Thai text stands without it; the gap is recorded.
                english = {}
                upstream["translation_error"] = str(exc)
            translation = english.get("lawTranslation") or {}
            en_text = render_text(translation.get("lawSections") or [])
            if english and not en_text:
                upstream["translation_error"] = "flagged as translated; no English text"
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
            # A crawled document is as old as the crawl that saved it.
            fetched_at=datetime.fromtimestamp(saved[1], UTC) if saved else datetime.now(UTC),
            licence=self._licence,
            citation_alt=title or None,
            gazette_ref=gazette_ref(payload.get("footnoteList") or [], published),
            upstream_metadata=upstream,
        )

    async def search(
        self,
        keyword: str,
        *,
        page: int,
        per_page: int = 50,
        states: str = "01,02",
        all_children: bool = False,
    ) -> dict[str, Any]:
        """One page of the public search over law titles; `states` "" is every state.
        Each hit lists its subordinate laws: those matching `keyword`, or all of them."""
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
            "query[onlyMatchChild]": "0" if all_children else "1",
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
                title = strip_marks(str(hit.get("lawNameTh") or ""))
                yield DocumentRef(
                    jurisdiction_code=self.jurisdiction_code,
                    # The title's own term names its class; a keyword search mixes classes.
                    doctype=doctype_for_title(title) or query.doctype or "act",
                    year=year,
                    number=str(hit.get("lawCode") or ""),
                    languages=["tha"],
                    extra={"timeline_id": str(hit.get("encTimelineID") or ""), "title": title},
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


class IndexEmpty(RuntimeError):
    """The search answered no catalogue; a maintenance page is not an empty law base."""


def _index_payload(
    entries: dict[str, dict[str, Any]],
    total: int,
    per_page: int,
    done: int,
    *,
    complete: bool,
    dropped: int = 0,
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
            "dropped_hits": dropped,
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
    first = await acquirer.search("", page=1, per_page=per_page, states="", all_children=True)
    meta = first.get("meta") or {}
    total, pages = int(meta.get("total") or 0), int(meta.get("pages") or 0)
    if total <= 0 or pages <= 0:
        raise IndexEmpty(f"the search answered total={total}, pages={pages}")
    entries: dict[str, dict[str, Any]] = {}
    dropped = 0
    page = 1
    if resume and checkpoint is not None and checkpoint.exists():
        prior = json.loads(checkpoint.read_text())
        # A checkpoint that walked every page and still fell short is walked again.
        exhausted = int(prior.get("pages_done") or 0) >= pages
        if prior.get("total") == total and prior.get("per_page") == per_page and not exhausted:
            entries = {e["law_code"]: e for e in prior.get("entries") or []}
            dropped = int((prior.get("stats") or {}).get("dropped_hits") or 0)
            page = int(prior.get("pages_done") or 0) + 1
            logger.info("ocs_index_resumed", page=page, laws=len(entries))
        else:
            logger.info("ocs_index_restarted", was=prior.get("total"), now=total)
    data: dict[str, Any] | None = first if page == 1 else None
    while page <= pages:
        data = data or await acquirer.search(
            "", page=page, per_page=per_page, states="", all_children=True
        )
        now = int((data.get("meta") or {}).get("total") or 0)
        if now != total:
            raise IndexMoved(f"catalogue total moved from {total} to {now} at page {page}")
        for hit in data.get("data") or []:
            entry = index_entry(hit)
            if not (entry["law_code"] and entry["timeline_id"]):
                dropped += 1
                continue
            known = entries.setdefault(entry["law_code"], entry)
            if known is not entry:
                # A law listed twice keeps the subordinate laws of both listings.
                ids = {c["timeline_id"] for c in known["children"]}
                known["children"] += [c for c in entry["children"] if c["timeline_id"] not in ids]
        if checkpoint is not None:
            write_index(
                checkpoint,
                _index_payload(entries, total, per_page, page, complete=False, dropped=dropped),
            )
        if on_progress is not None:
            on_progress(page, pages, len(entries))
        page, data = page + 1, None
    last = await acquirer.search("", page=1, per_page=per_page, states="", all_children=True)
    if int((last.get("meta") or {}).get("total") or 0) != total:
        raise IndexMoved(f"catalogue total moved from {total} during the walk")
    # ponytail: counts only; a law withdrawn and another added between pages balance out.
    complete = len(entries) >= total
    return _index_payload(entries, total, per_page, pages, complete=complete, dropped=dropped)


def dump_name(timeline_id: str, *, english: bool = False) -> str:
    """A timeline's raw-document filename; ids are base64, so `/` and `+` are mapped."""
    safe = timeline_id.replace("/", "_").replace("+", "-")
    return f"{safe}.en.json" if english else f"{safe}.json"


def _write_raw(path: Path, payload: dict[str, Any]) -> None:
    """Whole file or nothing: temp file, fsync, rename."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False))
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def index_timelines(index: dict[str, Any]) -> list[str]:
    """The listed timeline of every law and subordinate law, each once, in index order."""
    ids = (
        tid
        for e in index.get("entries") or []
        for tid in [e["timeline_id"], *(c["timeline_id"] for c in e.get("children") or [])]
    )
    return list(dict.fromkeys(t for t in ids if t))


def failures_path(out: Path) -> Path:
    """Beside the crawl directory, so a reader of its documents never meets it."""
    out = out.resolve()
    return out.with_name(f"{out.name}-failures.json")


class SourceDown(RuntimeError):
    """Too many fetches in a row failed; the crawl stops rather than press on."""


async def crawl(
    acquirer: OcsAcquirer,
    index: dict[str, Any],
    out: Path,
    *,
    kinds: frozenset[str] = frozenset({"current"}),
    stop_after_failures: int = 20,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Save the raw document of every listed timeline under `out`, then of every
    other timeline of the `kinds` asked for, and the English of each that has one.
    A file already there is not fetched again, so a stopped crawl resumes.
    The failures are written beside `out` however the crawl ends."""
    out.mkdir(parents=True, exist_ok=True)
    failures: dict[str, str] = {}
    fetched = textless = 0
    streak = 0

    async def save(tid: str, *, english: bool) -> dict[str, Any] | None:
        nonlocal fetched, streak
        path = out / dump_name(tid, english=english)
        if path.exists():
            try:
                saved = dict(json.loads(path.read_text()))
                streak = 0
                return saved
            except ValueError:
                logger.warning("ocs_dump_unreadable", path=str(path))
        try:
            payload = await acquirer._fetch_doc(tid, english=english)
        except (OcsItemMissing, httpx.HTTPError) as exc:
            failures[dump_name(tid, english=english)] = f"{type(exc).__name__}: {exc}"
            streak += 1
            if streak >= stop_after_failures:
                raise SourceDown(f"{streak} fetches in a row failed; last: {exc}") from exc
            return None
        streak = 0
        fetched += 1
        _write_raw(path, payload)
        return payload

    queue = index_timelines(index)
    seen = set(queue)
    done = 0
    try:
        while done < len(queue):
            tid = queue[done]
            done += 1
            doc = await save(tid, english=False)
            if on_progress is not None:
                on_progress(done, len(queue), tid)
            if doc is None:
                continue
            textless += not holds_text(doc)
            for t in timelines(doc):
                if t["kind"] in kinds and t["timeline_id"] and t["timeline_id"] not in seen:
                    seen.add(t["timeline_id"])
                    queue.append(t["timeline_id"])
                if t["timeline_id"] == tid and t["has_translation"]:
                    await save(tid, english=True)
    finally:
        _write_raw(failures_path(out), failures)
    return {"timelines": len(queue), "fetched": fetched, "textless": textless, "failures": failures}


__all__ = [
    "GAZETTE_NAME",
    "IndexEmpty",
    "IndexMoved",
    "LAW_CODE_IDENTITY",
    "OcsAcquirer",
    "OcsItemMissing",
    "SourceDown",
    "TIMELINE_KINDS",
    "build_index",
    "doctype_for_title",
    "crawl",
    "dump_name",
    "failures_path",
    "gazette_ref",
    "holds_text",
    "own_gazette_note",
    "publication_date",
    "index_entry",
    "index_timelines",
    "law_code_number",
    "render_text",
    "section_text",
    "timelines",
]
