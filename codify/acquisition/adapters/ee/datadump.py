"""Read Estonian legislation from the Riigi Teataja bulk XML archive."""

from __future__ import annotations

import asyncio
import json
import os
import re
import zipfile
from datetime import date
from html import unescape
from pathlib import Path
from typing import IO, Any, ClassVar

import httpx
import structlog

from codify.acquisition.adapters.ee.identity import classify, read_identity, romanise
from codify.acquisition.adapters.ee.remote import MemberLocation, RemoteFile
from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DocumentRef,
)
from codify.acquisition.index import index_path as acquisition_index_path
from codify.akn._schema import parse_xml
from codify.jurisdictions import SourceAdapter

logger = structlog.get_logger()

_INDEX_CACHE: dict[str, dict[str, Any]] = {}
_ZIP_CACHE: dict[str, zipfile.ZipFile] = {}


class EeDatadumpIndexMissing(RuntimeError):
    """Raised when the Estonian datadump index is missing."""


class EeDocNotInDump(KeyError):
    """Raised when requested statute is not in the index."""


class TooManyMembers(ValueError):
    """More members changed since the day than the caller will read."""


def _read_remote_member(url: str, loc: MemberLocation) -> bytes:
    with RemoteFile(url) as remote:
        return remote.member(loc)


class EeDatadumpAcquirer(BaseAcquirer):
    """Acquire Estonian legislation from the local Riigi Teataja zip archive."""

    JURISDICTION: ClassVar[str] = "ee"

    def __init__(
        self,
        adapter: SourceAdapter,
        *,
        archive_path: Path | None = None,
        index_path: Path | None = None,
    ) -> None:
        super().__init__(adapter)
        env_archive = os.environ.get("EE_DATADUMP_ZIP")
        self._index_path = index_path or acquisition_index_path(self.JURISDICTION)
        self._archive_path = archive_path or (Path(env_archive) if env_archive else None)
        self._index: dict[str, Any] | None = None
        self._zf: zipfile.ZipFile | None = None

    def _load_index(self) -> dict[str, Any]:
        if self._index is not None:
            return self._index
        if not self._index_path.exists():
            raise EeDatadumpIndexMissing(f"Estonian datadump index missing at {self._index_path}")
        key = str(self._index_path)
        payload = _INDEX_CACHE.get(key)
        if payload is None:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
            _INDEX_CACHE[key] = payload
        entries = payload.get("entries") or {}
        if not entries:
            raise EeDatadumpIndexMissing(
                f"Estonian datadump index at {self._index_path} has no entries"
            )
        if self._archive_path is None:
            archive = payload.get("archive")
            if not archive:
                raise EeDatadumpIndexMissing(
                    f"Estonian datadump index at {self._index_path} names no archive; "
                    "set EE_DATADUMP_ZIP"
                )
            self._archive_path = Path(archive)
        self._index = entries
        return entries

    def _zip(self) -> zipfile.ZipFile:
        if self._zf is None:
            self._load_index()
            assert self._archive_path is not None
            key = str(self._archive_path)
            zf = _ZIP_CACHE.get(key)
            if zf is None:
                if not self._archive_path.exists():
                    raise FileNotFoundError(f"Estonian archive not found at {self._archive_path}")
                zf = zipfile.ZipFile(self._archive_path)
                _ZIP_CACHE[key] = zf
            self._zf = zf
        return self._zf

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        remote = ref.extra.get("archive")
        # A member located in a published archive is read from it, without an index.
        entries = {} if remote else self._load_index()
        doc_id = str(ref.number)
        entry = entries.get(doc_id)
        member = ref.extra.get("member")

        if not member:
            if entry is not None:
                member = entry.get("member") if isinstance(entry, dict) else str(entry)
            else:
                wanted_title = ref.extra.get("title")
                for k, v in entries.items():
                    if isinstance(v, dict) and (v.get("title") == wanted_title or k == doc_id):
                        member = v.get("member")
                        doc_id = k
                        entry = v
                        break

        if not member:
            raise EeDocNotInDump(f"Statute {ref.number!r} not in Estonian datadump index")

        if remote:
            raw_xml = await asyncio.to_thread(
                _read_remote_member, remote, MemberLocation.from_extra(ref.extra)
            )
        else:
            archive = self._zip()
            try:
                raw_xml = archive.read(member)
            except KeyError as exc:
                raise FileNotFoundError(
                    f"Member {member!r} for statute {ref.number} not found in archive"
                ) from exc

        source_url = f"https://www.riigiteataja.ee/akt/{doc_id}.xml"
        # From the member, not the index or ref: an index built earlier can carry a stale slug.
        frbr_work_uri = read_identity(parse_xml(raw_xml, huge_tree=True)).work_uri

        return AcquiredDocument(
            ref=ref,
            source_url=source_url,
            bodies=[Body(content=raw_xml, media_type="application/xml")],
            frbr_work_uri=frbr_work_uri,
            licence="open_government",
        )


# Every local government body the archive names: a rural municipality, town or
# borough council or government. Their acts are municipal whichever series printed them.
_MUNICIPAL_ISSUER = re.compile(r"(?:valla|linna|alevi)(?:volikogu|valitsus)", re.I)
# The title follows the metadata block. Across a full archive it ends by byte 11,492.
_HEAD_END = b"</aktinimi>"
_HEAD_LIMIT = 65536


def _field(head: str, tag: str) -> str:
    """A metadata element's text."""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", head)
    return unescape(m.group(1)).strip() if m else ""


_BODY = re.compile(r"<sisu\b[^>]*/>|<sisu\b[^>]*>(.*?)</sisu>", re.S)
_MARKUP = re.compile(r"<!\[CDATA\[|\]\]>|<[^>]+>")
_REPEALING_ACT = re.compile(
    r"<muutmismarge\b[^>]*>.*?<aktViide\b[^>]*>(\d+)</aktViide>|<sisu\b[^>]*>.*?href=\"\./(\d+)\"",
    re.S,
)
# Within the adoption block only: past its end, a reference is a later publication's.
_ORIGINAL_ACT = re.compile(
    r"<vastuvoetud\b[^>]*>(?:(?!</vastuvoetud>).)*?<aktViide\b[^>]*>(\d+)</aktViide>", re.S
)


def _original_act(prefix: str) -> str | None:
    """The publisher id of the act's own first publication."""
    m = _ORIGINAL_ACT.search(prefix)
    return m.group(1) if m else None


def _repealing_act(prefix: str, own_ids: set[str]) -> str | None:
    """The publication the repeal note cites, unless it is the act's own: a temporary act
    that lapses cites itself, which names no repealing act."""
    m = _REPEALING_ACT.search(prefix)
    cited = (m.group(1) or m.group(2)) if m else None
    return cited if cited and cited not in own_ids else None


def _is_repeal_stub(prefix: str) -> bool:
    """A redaction kept only to mark the act repealed: no body, or a short "Kehtetu" note."""
    m = _BODY.search(prefix)
    if m is None:
        # No body in a member read whole; a body past the read limit is not absent.
        return "</oigusakt>" in prefix
    text = " ".join(unescape(_MARKUP.sub(" ", m.group(1) or "")).split())
    return not text or (len(text) < 400 and re.match(r"kehtetu\b", text, re.I) is not None)


def _prefix(f: IO[bytes]) -> bytes:
    """A member's first bytes, as the remote read takes them."""
    return f.read(_HEAD_LIMIT)


def _group(head: str) -> str | None:
    """The id shared by every redaction of one act, or None where the member names none."""
    m = re.search(r"<terviktekstiGrupiID\b[^>]*>(\d+)</terviktekstiGrupiID>", head)
    return m.group(1) if m else None


def _index_entry(
    member: str,
    data: bytes,
    *,
    today: date,
    principal_only: bool,
    keep_ended: bool = False,
) -> tuple[str, dict[str, Any], str] | None:
    """A member's key, entry and kind (``entry``, ``repeal`` for a redaction that only marks
    a repeal, or ``ended`` for a text whose validity ended before `today`, kept only with
    `keep_ended`), or None for another kind of document, a municipal act, a redaction
    no longer in force, or one with no title."""
    prefix = data.decode("utf-8", errors="ignore")
    cut = data.find(_HEAD_END)
    head = prefix if cut < 0 else data[: cut + len(_HEAD_END)].decode("utf-8", errors="ignore")
    liik = _field(head, "dokumentLiik").lower()
    if liik not in ("seadus", "määrus", "põhiseadus"):
        return None

    valj = _field(head, "valjaandja")
    if _MUNICIPAL_ISSUER.search(valj):
        return None

    lopp = _field(head, "kehtivuseLopp")
    ended = bool(lopp) and date.fromisoformat(lopp[:10]) < today
    if ended and not keep_ended:
        return None

    # Newer files give the title an id attribute.
    m_title = re.search(r"<pealkiri\b[^>]*>.*?</pealkiri>", head, re.S)
    # Parsed, as fetch and the transformer read it: CDATA and entities decode.
    title = " ".join((parse_xml(m_title.group(0)).text or "").split()) if m_title else ""
    if not title:
        return None

    m_lyhend = re.search(r"<lyhend>(.*?)</lyhend>", head)
    lyhend = unescape(m_lyhend.group(1)).strip() if m_lyhend else ""
    slug = romanise(title)

    doc_id = _field(head, "globaalID") or member.replace(".xml", "")

    m_date = re.search(r"<(?:aktikuupaev|avaldamineKuupaev)\b[^>]*>(\d{4}-\d{2}-\d{2})", head)
    date_str = m_date.group(1) if m_date else ""
    year = date_str.split("-")[0] if date_str else "2022"

    is_principal = liik == "seadus" and not (
        title.endswith(" muutmise seadus")
        or " muutmise ja sellega seonduvalt " in title
        or title.endswith(" täiendamise seadus")
        or title.endswith(" kehtetuks tunnistamise seadus")
    )
    is_code = "seadustik" in title.lower()

    if principal_only and not (is_principal or is_code):
        return None

    original = _original_act(prefix)
    entry: dict[str, Any] = {
        "member": member,
        "title": title,
        "doctype": classify(liik, lyhend, title),
        "slug": slug,
        "issuer": valj,
        "date": date_str,
        "year": int(year) if year.isdigit() else 2022,
        # The parser's expression date: validity start, else the act's own date.
        "expression_date": (_field(head, "kehtivuseAlgus") or date_str)[:10],
        "principal": is_principal or is_code,
        "code": is_code,
        "original": original,
        "group": _group(head),
    }
    # Repealed acts keep a last redaction with no text; it has no end date.
    if _is_repeal_stub(prefix):
        if ended:
            return None
        own = {i for i in (doc_id, original) if i}
        return doc_id, {**entry, "repealed_by": _repealing_act(prefix, own)}, "repeal"
    if ended:
        return doc_id, {**entry, "valid_to": lopp[:10]}, "ended"
    return doc_id, entry, "entry"


def build_ee_index(
    archive: Path | str,
    *,
    principal_only: bool = False,
    max_files: int | None = None,
    on_progress: Any = None,
    as_of: date | None = None,
    keep_ended: bool = False,
) -> dict[str, Any]:
    """Walk an Estonian bulk XML zip archive and index the state statutes not ended by
    `as_of` (today), so a text published ahead of its start is kept. Repeal-only
    redactions go under ``repealed``, which no ingest reads. With `keep_ended`, texts
    ended before `as_of` go under ``ended``, each with its validity end."""
    from datetime import UTC, datetime

    archive_path = Path(archive)
    today = as_of or date.today()
    entries: dict[str, Any] = {}
    repealed: dict[str, Any] = {}
    ended: dict[str, Any] = {}
    sections = {"entry": entries, "repeal": repealed, "ended": ended}
    scanned = 0
    skipped = 0

    with zipfile.ZipFile(archive_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xml")]
        total = len(names)
        for n in names:
            if max_files is not None and scanned >= max_files:
                break
            scanned += 1
            try:
                with zf.open(n) as f:
                    found = _index_entry(
                        n,
                        _prefix(f),
                        today=today,
                        principal_only=principal_only,
                        keep_ended=keep_ended,
                    )
                if found is None or found[2] == "repeal":
                    skipped += 1
                if found is not None:
                    sections[found[2]][found[0]] = found[1]
            except Exception:
                skipped += 1

            if on_progress is not None and scanned % 10000 == 0:
                on_progress(scanned, total, len(entries))

    try:
        portable_archive = str(archive_path.absolute().relative_to(Path.cwd()))
    except ValueError:
        portable_archive = str(archive_path)

    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "archive": portable_archive,
        "language": "est",
        "stats": {
            "files_scanned": scanned,
            "indexed": len(entries),
            "repealed": len(repealed),
            "skipped": skipped,
        },
        "entries": entries,
        "repealed": repealed,
    }
    if keep_ended:
        payload["stats"]["ended"] = len(ended)
        payload["ended"] = ended
    return payload


def index_remote_ee_archive(
    url: str,
    *,
    modified_since: date,
    max_members: int | None = None,
    workers: int = 8,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Index the members an archive changed on or after `modified_since`, reading by range;
    each entry carries its member's location, which is all a fetch needs."""
    from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
    from datetime import UTC, datetime

    today = date.today()
    since = (modified_since.year, modified_since.month, modified_since.day)
    with RemoteFile(url, client=client) as remote:
        changed = [
            i
            for i in remote.directory()
            if i.filename.endswith(".xml") and i.date_time[:3] >= since
        ]
        if max_members is not None and len(changed) > max_members:
            raise TooManyMembers(
                f"{len(changed)} members changed since {modified_since}, "
                f"more than the {max_members} one read takes"
            )

        def entry(info: zipfile.ZipInfo) -> tuple[str, dict[str, Any], str] | None:
            loc = MemberLocation.of(info)
            data = remote.member_prefix(loc, limit=_HEAD_LIMIT)
            try:
                found = _index_entry(info.filename, data, today=today, principal_only=False)
            except ValueError:
                return None  # an unreadable date, skipped as the local walk skips it
            if found is None:
                return None
            return found[0], {**found[1], "location": loc.to_extra()}, found[2]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(entry, i) for i in changed]
            done, _ = wait(futures, return_when=FIRST_EXCEPTION)
            failed = next((f.exception() for f in done if f.exception() is not None), None)
            if failed is not None:
                # One unreadable member fails the index; the rest are not read.
                pool.shutdown(cancel_futures=True)
                raise failed
            found = [r for f in futures if (r := f.result()) is not None]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "archive": url,
        "language": "est",
        "modified_since": modified_since.isoformat(),
        "stats": {
            "files_scanned": len(changed),
            "indexed": sum(1 for f in found if f[2] == "entry"),
            "repealed": sum(1 for f in found if f[2] == "repeal"),
            "skipped": len(changed) - sum(1 for f in found if f[2] == "entry"),
        },
        "entries": {k: e for k, e, kind in found if kind == "entry"},
        "repealed": {k: e for k, e, kind in found if kind == "repeal"},
    }


__all__ = [
    "EeDatadumpAcquirer",
    "EeDatadumpIndexMissing",
    "EeDocNotInDump",
    "TooManyMembers",
    "build_ee_index",
    "index_remote_ee_archive",
]
