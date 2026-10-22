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
from codify.acquisition.adapters.ee.remote import MemberLocation, RemoteFile, leading
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


def _read_head(f: IO[bytes]) -> str:
    """A member's leading text through its title, cut as the remote read cuts it."""
    return leading(f.read(_HEAD_LIMIT), _HEAD_END, _HEAD_LIMIT).decode("utf-8", errors="ignore")


def _index_entry(
    member: str, head: str, *, today: date, principal_only: bool
) -> tuple[str, dict[str, Any]] | None:
    """A member's key and entry, or None for another kind of document, a municipal act,
    a redaction no longer in force, or one with no title."""
    liik = _field(head, "dokumentLiik").lower()
    if liik not in ("seadus", "määrus", "põhiseadus"):
        return None

    valj = _field(head, "valjaandja")
    if _MUNICIPAL_ISSUER.search(valj):
        return None

    lopp = _field(head, "kehtivuseLopp")
    if lopp and date.fromisoformat(lopp[:10]) < today:
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

    m_date = re.search(r"<(?:aktikuupaev|avaldamineKuupaev)>(\d{4}-\d{2}-\d{2})", head)
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

    return doc_id, {
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
    }


def build_ee_index(
    archive: Path | str,
    *,
    principal_only: bool = False,
    max_files: int | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Walk an Estonian bulk XML zip archive and index the state statutes in force."""
    from datetime import UTC, datetime

    archive_path = Path(archive)
    today = date.today()
    entries: dict[str, Any] = {}
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
                        n, _read_head(f), today=today, principal_only=principal_only
                    )
                if found is None:
                    skipped += 1
                else:
                    entries[found[0]] = found[1]
            except Exception:
                skipped += 1

            if on_progress is not None and scanned % 10000 == 0:
                on_progress(scanned, total, len(entries))

    try:
        portable_archive = str(archive_path.absolute().relative_to(Path.cwd()))
    except ValueError:
        portable_archive = str(archive_path)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "archive": portable_archive,
        "language": "est",
        "stats": {
            "files_scanned": scanned,
            "indexed": len(entries),
            "skipped": skipped,
        },
        "entries": entries,
    }


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

        def entry(info: zipfile.ZipInfo) -> tuple[str, dict[str, Any]] | None:
            loc = MemberLocation.of(info)
            head = remote.member_head(loc, until=_HEAD_END, limit=_HEAD_LIMIT)
            try:
                found = _index_entry(
                    info.filename,
                    head.decode("utf-8", errors="ignore"),
                    today=today,
                    principal_only=False,
                )
            except ValueError:
                return None  # an unreadable date, skipped as the local walk skips it
            return None if found is None else (found[0], {**found[1], "location": loc.to_extra()})

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
            "indexed": len(found),
            "skipped": len(changed) - len(found),
        },
        "entries": dict(found),
    }


__all__ = [
    "EeDatadumpAcquirer",
    "EeDatadumpIndexMissing",
    "EeDocNotInDump",
    "TooManyMembers",
    "build_ee_index",
    "index_remote_ee_archive",
]
