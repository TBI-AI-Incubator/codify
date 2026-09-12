"""Read Estonian legislation from the Riigi Teataja bulk XML archive."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Any, ClassVar

import structlog

from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DocumentRef,
)
from codify.acquisition.index import index_path as acquisition_index_path
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter


def _romanise(text: str) -> str:
    """Romanise Estonian text for FRBR URI slugs per ee/config.json."""
    import re

    trans = {
        "ä": "a",
        "ö": "o",
        "ü": "u",
        "õ": "o",
        "š": "s",
        "ž": "z",
        "Ä": "a",
        "Ö": "o",
        "Ü": "u",
        "Õ": "o",
        "Š": "s",
        "Ž": "z",
    }
    s = text.lower()
    for k, v in trans.items():
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "seadus"


logger = structlog.get_logger()

_INDEX_CACHE: dict[str, dict[str, Any]] = {}
_ZIP_CACHE: dict[str, zipfile.ZipFile] = {}


class EeDatadumpIndexMissing(RuntimeError):
    """Raised when the Estonian datadump index is missing."""


class EeDocNotInDump(KeyError):
    """Raised when requested statute is not in the index."""


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
        entries = self._load_index()
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

        archive = self._zip()
        try:
            raw_xml = archive.read(member)
        except KeyError as exc:
            raise FileNotFoundError(
                f"Member {member!r} for statute {ref.number} not found in archive"
            ) from exc

        source_url = f"https://www.riigiteataja.ee/akt/{doc_id}.xml"
        slug = ref.extra.get("slug")
        if not slug and isinstance(entry, dict):
            slug = entry.get("slug")
        if not slug:
            title = entry.get("title") if isinstance(entry, dict) else ref.extra.get("title") or ""
            slug = _romanise(title) if title else str(ref.number)

        doctype = ref.doctype
        if doctype == "constitution" or (
            isinstance(entry, dict) and entry.get("doctype") == "constitution"
        ):
            frbr_work_uri = "/akn/ee/act/1992/pohiseadus"
        else:
            frbr_work_uri = build_frbr_work_uri("ee", doctype, ref.year, slug)

        return AcquiredDocument(
            ref=ref,
            source_url=source_url,
            bodies=[Body(content=raw_xml, media_type="application/xml")],
            frbr_work_uri=frbr_work_uri,
            licence="open_government",
        )


def build_ee_index(
    archive: Path | str,
    *,
    principal_only: bool = False,
    max_files: int | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Walk an Estonian bulk XML zip archive and index active state statutes."""
    import re
    from datetime import UTC, datetime

    archive_path = Path(archive)
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
                    head = f.read(3000).decode("utf-8", errors="ignore")

                    m_liik = re.search(r"<dokumentLiik>(.*?)</dokumentLiik>", head)
                    liik = m_liik.group(1).strip().lower() if m_liik else ""
                    if liik not in ("seadus", "määrus", "põhiseadus"):
                        skipped += 1
                        continue

                    m_valj = re.search(r"<valjaandja>(.*?)</valjaandja>", head)
                    valj = m_valj.group(1).strip() if m_valj else ""
                    if "volikogu" in valj.lower():
                        skipped += 1
                        continue

                    m_lopp = re.search(r"<kehtivuseLopp>(.*?)</kehtivuseLopp>", head)
                    if m_lopp is not None:
                        skipped += 1
                        continue

                    m_title = re.search(r"<pealkiri>(.*?)</pealkiri>", head)
                    title = m_title.group(1).strip() if m_title else ""
                    if not title:
                        skipped += 1
                        continue

                    m_lyhend = re.search(r"<lyhend>(.*?)</lyhend>", head)
                    lyhend = m_lyhend.group(1).strip() if m_lyhend else ""
                    slug = _romanise(lyhend or title)

                    m_id = re.search(r"<globaalID>(.*?)</globaalID>", head)
                    doc_id = m_id.group(1).strip() if m_id else n.replace(".xml", "")

                    m_date = re.search(
                        r"<(?:aktikuupaev|avaldamineKuupaev)>(\d{4}-\d{2}-\d{2})", head
                    )
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
                        skipped += 1
                        continue

                    is_const = "põhiseadus" in title.lower() or lyhend.lower() == "ps"
                    if is_const:
                        doctype = "constitution"
                    elif liik == "seadus":
                        doctype = "act"
                    else:
                        doctype = "maarrus"

                    entries[doc_id] = {
                        "member": n,
                        "title": title,
                        "doctype": doctype,
                        "slug": slug,
                        "issuer": valj,
                        "date": date_str,
                        "year": int(year) if year.isdigit() else 2022,
                        "principal": is_principal or is_code,
                        "code": is_code,
                    }
            except Exception:
                skipped += 1

            if on_progress is not None and scanned % 10000 == 0:
                on_progress(scanned, total, len(entries))

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "archive": str(archive_path),
        "language": "est",
        "stats": {
            "files_scanned": scanned,
            "indexed": len(entries),
            "skipped": skipped,
        },
        "entries": entries,
    }


__all__ = [
    "EeDatadumpAcquirer",
    "EeDatadumpIndexMissing",
    "EeDocNotInDump",
    "build_ee_index",
]
