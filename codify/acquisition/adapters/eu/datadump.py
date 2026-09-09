"""Read EU FMX acts from the Publications Office bulk archive.

One archive per language, so the index names the zip and its language. A
publication splits across files, act body plus one per annex; `_assemble` puts
them back into the single-file shape the parser reads. No rate limit: local.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

import structlog
from lxml import etree

from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DocumentRef,
)
from codify.acquisition.index import index_path as acquisition_index_path
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter
from codify.lang import to_iso639_3

logger = structlog.get_logger()

# One entry per archive, so a bulk run walks the zip once, not once per act.
_BASENAME_CACHE: dict[str, dict[str, list[str]]] = {}
_INDEX_CACHE: dict[str, dict[str, Any]] = {}
_ZIP_CACHE: dict[str, zipfile.ZipFile] = {}


# The act body names its own manifest; the manifest names the annexes.
_MANIFEST_REF = re.compile(rb'<DOCUMENT\.REF[^>]*\sFILE="([^"]+)"')
_ANNEX_ENTRY = re.compile(rb'<DOC\.SUB\.PUB[^>]*\sTYPE="ANNEX"[^>]*>(.*?)</DOC\.SUB\.PUB>', re.S)
_PHYS_REF = re.compile(rb'<REF\.PHYS[^>]*\sFILE="([^"]+)"')


class DatadumpIndexMissing(RuntimeError):
    """Raised when the datadump index file isn't present or is empty."""


class ManifestMissing(LookupError):
    """An act names a manifest the archive does not hold."""


class CelexNotInDump(KeyError):
    """Raised when a requested CELEX isn't in the datadump (repealed or unpublished)."""


class EuDatadumpAcquirer(BaseAcquirer):
    """Fetch EU FMX acts from the OP bulk archive. Local zip read, so no rate limit."""

    JURISDICTION: ClassVar[str] = "eu"

    def __init__(
        self,
        adapter: SourceAdapter,
        *,
        archive_path: Path | None = None,
        index_path: Path | None = None,
    ) -> None:
        super().__init__(adapter)
        env_archive = os.environ.get("EU_DATADUMP_ZIP")
        self._index_path = index_path or acquisition_index_path(self.JURISDICTION)
        # None here means "whatever archive the index was built against", read
        # in _load_index. An archive the index does not describe is unusable.
        self._archive_path = archive_path or (Path(env_archive) if env_archive else None)
        self._index: dict[str, str] | None = None
        self._basenames: dict[str, list[str]] | None = None
        self._language = "eng"
        self._zf: zipfile.ZipFile | None = None

    def _load_index(self) -> dict[str, str]:
        if self._index is not None:
            return self._index
        if not self._index_path.exists():
            raise DatadumpIndexMissing(
                f"datadump index missing at {self._index_path}; "
                f"build it from the archive before fetching"
            )
        key = str(self._index_path)
        payload = _INDEX_CACHE.get(key)
        if payload is None:
            payload = json.loads(self._index_path.read_text())
            _INDEX_CACHE[key] = payload
        entries = payload.get("entries") or {}
        if not entries:
            raise DatadumpIndexMissing(f"datadump index at {self._index_path} has no entries")
        if self._archive_path is None:
            archive = payload.get("archive")
            if not archive:
                raise DatadumpIndexMissing(
                    f"datadump index at {self._index_path} names no archive; set EU_DATADUMP_ZIP"
                )
            self._archive_path = Path(archive)
        # An index built before this field existed is the English archive.
        self._language = payload.get("language") or "eng"
        self._index = entries
        return entries

    def _zip(self) -> zipfile.ZipFile:
        """The open archive, shared per path.

        Opening one reads a central directory of tens of thousands of members.
        `ZipFile` serialises concurrent reads through its own lock.
        """
        if self._zf is None:
            self._load_index()
            assert self._archive_path is not None
            key = str(self._archive_path)
            shared = _ZIP_CACHE.get(key)
            if shared is None:
                if not self._archive_path.exists():
                    raise FileNotFoundError(f"datadump archive missing at {self._archive_path}")
                shared = zipfile.ZipFile(self._archive_path)
                _ZIP_CACHE[key] = shared
            self._zf = shared
        return self._zf

    def _by_basename(self) -> dict[str, list[str]]:
        """Members by name, since a publication's files sit under different UUIDs.

        Cached per archive: a bulk run builds an acquirer per document, and this
        walks every member of a multi-gigabyte zip.
        """
        if self._basenames is None:
            assert self._archive_path is not None
            key = str(self._archive_path)
            cached = _BASENAME_CACHE.get(key)
            if cached is None:
                cached = {}
                for name in self._zip().namelist():
                    cached.setdefault(name.rsplit("/", 1)[-1], []).append(name)
                _BASENAME_CACHE[key] = cached
            self._basenames = cached
        return self._basenames

    def _member(self, basename: str, near: str) -> str | None:
        """The member with this name, preferring the one beside `near`.

        Names repeat across Cellar directories; those pairs hold identical
        bytes today, a property of one snapshot rather than a rule.
        """
        candidates = self._by_basename().get(basename)
        if not candidates:
            return None
        uuid_prefix = near.split("/", 1)[0]
        return next((c for c in candidates if c.split("/", 1)[0] == uuid_prefix), candidates[0])

    def _member_path(
        self, basename: str, near: str, within: frozenset[str] = frozenset()
    ) -> str | None:
        """The path of `basename`, searched within one publication only.

        The referring file's own directory first, then the rest of this
        publication's. Never archive-wide: a basename this publication lacks must
        leave its reference alone rather than pull in a stranger's same-named file.
        """
        candidates = self._by_basename().get(basename, ())
        if not candidates:
            return None
        dirs = [near.split("/", 1)[0], *sorted(within - {near.split("/", 1)[0]})]
        for prefix in dirs:
            member = next((c for c in candidates if c.split("/", 1)[0] == prefix), None)
            if member is not None:
                return member
        return None

    def _metadata_member(self, reference: str, near: str) -> str | None:
        """Resolve an explicit XML link without changing its path or guessing a tie."""
        if (
            reference.startswith("/")
            or "\\" in reference
            or any(part in ("", ".", "..") for part in reference.split("/"))
            or not reference.lower().endswith(".xml")
        ):
            return None
        if "/" in reference:
            return reference if reference in self._zip().namelist() else None
        candidates = self._by_basename().get(reference, ())
        local = [path for path in candidates if path.split("/", 1)[0] == near.split("/", 1)[0]]
        choices = local or candidates
        return choices[0] if len(choices) == 1 else None

    def _image_members(self, act_path: str, filerefs: list[str]) -> dict[str, str | None]:
        """Locate unique images within manifest-linked publication directories.

        Read only act/manifest XML metadata, never image bytes. An explicit path
        that is missing or unsafe cannot fall back to an unrelated basename.
        """
        archive = self._zip()
        if not act_path.lower().endswith(".xml"):
            raise ManifestMissing(f"indexed act is not XML metadata: {act_path}")
        directories = {act_path.split("/", 1)[0]}
        ref = _MANIFEST_REF.search(archive.read(act_path))
        if ref is not None:
            manifest = self._metadata_member(ref.group(1).decode(), act_path)
            if manifest is None:
                raise ManifestMissing(f"{act_path} names missing or ambiguous manifest metadata")
            for entry in _ANNEX_ENTRY.findall(archive.read(manifest)):
                for raw in _PHYS_REF.findall(entry):
                    member = self._metadata_member(raw.decode(), manifest)
                    if member is None:
                        raise ManifestMissing(
                            f"{manifest} names missing or ambiguous annex metadata"
                        )
                    directories.add(member.split("/", 1)[0])
        result: dict[str, str | None] = {}
        for fileref in filerefs:
            if fileref in ("", ".", "..") or "/" in fileref or "\\" in fileref:
                result[fileref] = None
                continue
            candidates = [
                path
                for path in self._by_basename().get(fileref, ())
                if path.split("/", 1)[0] in directories
            ]
            result[fileref] = candidates[0] if len(candidates) == 1 else None
        return result

    def _read_member(
        self, basename: str, near: str, within: frozenset[str] = frozenset()
    ) -> tuple[bytes, str] | None:
        """Read a member only after resolving it within the publication."""
        member = self._member_path(basename, near, within)
        return (self._zip().read(member), member) if member is not None else None

    def _annex_members(self, act_path: str, act_xml: bytes) -> list[str]:
        """Annex members of the publication this act belongs to, in manifest order."""
        ref = _MANIFEST_REF.search(act_xml)
        if ref is None:
            return []
        name = ref.group(1).decode()
        manifest = self._member(name, act_path)
        if manifest is None:
            # The act names its manifest. Absent, we cannot know whether it has
            # annexes, and answering "none" is the loss this adapter exists to
            # prevent.
            raise ManifestMissing(f"{act_path} names manifest {name}, absent from the archive")
        if manifest == act_path:
            return []
        out: list[str] = []
        for entry in _ANNEX_ENTRY.findall(self._zip().read(manifest)):
            for name in _PHYS_REF.findall(entry):
                member = self._member(name.decode(), manifest)
                if member is None:
                    # Same loss as an absent manifest: the act comes back
                    # plausible and short an annex.
                    raise ManifestMissing(
                        f"{manifest} names annex {name.decode()}, absent from the archive"
                    )
                if member != act_path:
                    out.append(member)
        return out

    def close(self) -> None:
        """Release this instance's reference.

        The handle is shared, so closing it here would break every other holder
        mid-read. It stays open for the life of the process.
        """
        self._zf = None
        self._basenames = None

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        celex = ref.extra.get("celex") or ""
        if not celex:
            # Build CELEX from the ref shape, assume directive when unspecified.
            letter = {"directive": "L", "regulation": "R", "decision": "D"}.get(ref.doctype, "L")
            celex = f"3{ref.year}{letter}{int(ref.number):04d}"

        index = self._load_index()
        if celex not in index:
            raise CelexNotInDump(
                f"CELEX {celex} not in datadump (likely repealed, "
                f"out-of-force, or unpublished as FMX)"
            )

        path = index[celex]
        archive = self._zip()
        act_xml = archive.read(path)
        annexes = self._annex_members(path, act_xml)
        # A publication spans UUID directories, so an include may name a sibling
        # beside an annex rather than beside the act. This is the whole set.
        within = frozenset(m.split("/", 1)[0] for m in (path, *annexes))
        include_counts: Counter[str] = Counter()
        content = _assemble(
            act_xml,
            [(archive.read(a), a) for a in annexes],
            lambda fileref, near: self._read_member(fileref, near, within),
            near=path,
            include_counts=include_counts,
        )
        # The archive entry is canonical FMX; derive FRBR URI from the CELEX type.
        doctype = {"L": "directive", "R": "regulation", "D": "decision"}.get(
            letter_from_celex(celex), "directive"
        )
        frbr_work_uri = build_frbr_work_uri("eu", doctype, ref.year, ref.number)

        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=frbr_work_uri,
            bodies=[
                Body(
                    media_type="application/xml",
                    content=content,
                    role="primary",
                    language=self._language,
                )
            ],
            source_url=f"datadump://{Path(archive.filename or '').name}!{path}",
            fetched_at=datetime.now(UTC),
            licence="EU-RU/2011/833",
            upstream_metadata={
                "cellar_uuid": path.split("/", 1)[0],
                "fmx_filename": path.rsplit("/", 1)[-1],
                "annex_count": str(len(annexes)),
                "annex_files": ",".join(a.rsplit("/", 1)[-1] for a in annexes),
                # A resolver failing on everything degrades silently to the state
                # before inlining, so the outcome rides on the document.
                **{f"include_{k}": str(v) for k, v in sorted(include_counts.items())},
            },
        )


# A nested document referencing itself, directly or through another, would
# recurse forever. Annexes nest a level or two in practice.
_MAX_INCLUDE_DEPTH = 4


def _assemble(
    act_xml: bytes,
    annexes: list[tuple[bytes, str]],
    resolve: Callable[[str, str], tuple[bytes, str] | None] | None = None,
    near: str = "",
    include_counts: Counter[str] | None = None,
) -> bytes:
    """Put the annex bodies back inside the act, the single-file shape the parser
    already reads. No annexes leaves the act byte-identical. Each annex carries its
    own archive path and is resolved before it is appended: a reference inside it
    names a file beside *it*, and appending first would lose that.
    """
    if not annexes and (resolve is None or b"INCL.ELEMENT" not in act_xml):
        # Byte-identical is the contract when there is nothing to add: parsing and
        # re-serialising an untouched act would change it for no reason.
        return act_xml
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
    act = etree.fromstring(act_xml, parser)
    counts: Counter[str] = Counter()
    # The act's own references first, while the tree is still only the act: once
    # the annexes are appended a second walk would revisit what they already did.
    if resolve is not None:
        counts += _inline_included(act, parser, resolve, near)
    for annex_xml, annex_path in annexes:
        annex = etree.fromstring(annex_xml, parser)
        if resolve is not None:
            counts += _inline_included(annex, parser, resolve, annex_path or near)
        act.append(annex)
    if resolve is not None:
        if counts:
            # One line per act rather than per reference: a resolver failing on
            # everything is a rate, and a stream of per-item warnings hides it.
            logger.info("formex_includes", act=near, **dict(counts))
            if include_counts is not None:
                include_counts.update(counts)
    return cast(bytes, etree.tostring(act, encoding="utf-8", xml_declaration=True))


def _inline_included(
    root: etree._Element,
    parser: etree.XMLParser,
    resolve: Callable[[str, str], tuple[bytes, str] | None],
    near: str,
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
) -> Counter[str]:
    """Replace each nested-document reference with the document it names.

    A reference that resolves to nothing is left named, which is the state before
    this change: removing it and putting nothing in its place would be the same
    loss this exists to fix, arrived at more quietly.
    """
    counts: Counter[str] = Counter()
    refs = list(root.iter("INCL.ELEMENT"))
    if depth >= _MAX_INCLUDE_DEPTH:
        # Counted, not silent: a cut that reports nothing reads as a clean act.
        counts["too_deep"] += len(refs)
        return counts
    for el in refs:
        ref = el.get("FILEREF") or ""
        if el.get("TYPE") == "TIFF" or not ref:
            continue
        try:
            found = resolve(ref, near)
        except Exception:
            # Broad on purpose: what a corrupt member raises is neither short nor
            # stable, and one damaged file must not cost the whole act.
            logger.warning("formex_include_unresolved", fileref=ref, act=near)
            counts["unresolved"] += 1
            continue
        if not found or not found[0]:
            counts["unresolved"] += 1
            continue
        raw, found_at = found
        # Identity is the member resolved to, not the basename: the same FILEREF
        # beside two publications names two documents, and only one is a cycle.
        if found_at in seen:
            counts["cycle"] += 1
            continue
        try:
            nested = etree.fromstring(raw, parser)
        except etree.XMLSyntaxError:
            logger.warning("formex_include_unparseable", fileref=ref, act=near)
            counts["unparseable"] += 1
            continue
        counts += _inline_included(nested, parser, resolve, found_at, depth + 1, seen | {found_at})
        # The nested root is dropped and its children take the reference's place.
        # Text sitting directly on that root would go with it, so it is carried.
        replacement: list[etree._Element] = []
        if (nested.text or "").strip():
            carried = etree.Element("P")
            carried.text = nested.text
            replacement.append(carried)
        replacement.extend(c for c in nested if isinstance(c.tag, str))
        if not replacement:
            # Nothing to put in its place. Leaving the reference keeps the loss
            # visible to the converter's marker and its count.
            logger.warning("formex_include_empty", fileref=ref, act=near)
            counts["empty"] += 1
            continue
        parent = el.getparent()
        if parent is None:
            continue
        at = list(parent).index(el)
        # lxml keeps text after an element on that element, so removing the
        # reference would take the prose following it inside a paragraph.
        tail = el.tail
        parent.remove(el)
        for offset, child in enumerate(replacement):
            parent.insert(at + offset, child)
        if tail:
            last = replacement[-1]
            last.tail = (last.tail or "") + tail
        counts["inlined"] += 1
    return counts


def letter_from_celex(celex: str) -> str:
    """Extract the sector-3 type letter (L/R/D/H) from a CELEX number."""
    # 3{year:4}{letter}{number:4}
    if len(celex) < 6 or not celex.startswith("3"):
        return "L"
    return celex[5]


# The archive is keyed by Cellar UUID and carries no CELEX manifest, so the
# index is built by reading each act body.

_ACT_ROOTS = {"ACT", "REG", "DIR", "DEC", "CONS_ACT"}
_SKIP_SUFFIXES = (".doc.xml", ".toc.xml", ".doc.fmx.xml", ".toc.fmx.xml", ".tif")

# NO.CURRENT precedes YEAR in FORMEX 4, follows it in 6. Capture either order.
_BIB_NO_DOC = re.compile(
    rb"<NO\.DOC\b[^>]*TYPE=\"OJ\"[^>]*>"
    rb"(?:<[^>]+>[^<]*</[^>]+>)*?"
    rb"<(?P<a_tag>YEAR|NO\.CURRENT)>(?P<a_val>\d+)</(?P=a_tag)>"
    rb"(?:<[^>]+>[^<]*</[^>]+>)*?"
    rb"<(?P<b_tag>YEAR|NO\.CURRENT)>(?P<b_val>\d+)</(?P=b_tag)>",
    re.DOTALL,
)
_TITLE_BLOCK = re.compile(rb"<TI>(.*?)</TI>", re.DOTALL)
_DOCTYPE_KEYWORD = re.compile(
    rb"\b(Directive|Regulation|Decision|Recommendation|Resolution)\b", re.IGNORECASE
)
_LETTER_FOR_KEYWORD = {
    b"directive": "L",
    b"regulation": "R",
    b"decision": "D",
    b"recommendation": "H",
    b"resolution": "H",
}
_ARCHIVE_LANGUAGE = re.compile(r"^LEG_([A-Z]{2})_", re.IGNORECASE)


def _root_name(head: bytes) -> str | None:
    """Root element name, skipping the XML prolog."""
    idx = head.find(b"?>")
    m = re.search(rb"<([A-Z][A-Z0-9._]*)\b", head[idx + 2 :] if idx >= 0 else head)
    return m.group(1).decode("ascii") if m else None


def _letter_from_title(head: bytes) -> str | None:
    """CELEX type letter from the title text; keywords sit inside inline markup."""
    title = _TITLE_BLOCK.search(head)
    if title is None:
        return None
    kw = _DOCTYPE_KEYWORD.search(re.sub(rb"<[^>]+>", b" ", title.group(1)))
    return _LETTER_FOR_KEYWORD.get(kw.group(1).lower()) if kw else None


def _celex_with_reason(content: bytes) -> tuple[str | None, str]:
    """The CELEX and why, when there isn't one.

    An act whose bibliography or title does not match is a different loss from
    a file that was never an act, and counting them together hides it.
    """
    if _root_name(content[:500]) not in _ACT_ROOTS:
        return None, "not_an_act"
    bib = _BIB_NO_DOC.search(content[:8000])
    if bib is None:
        return None, "no_bibliography"
    pairs = {
        bib.group("a_tag").decode("ascii"): bib.group("a_val").decode("ascii"),
        bib.group("b_tag").decode("ascii"): bib.group("b_val").decode("ascii"),
    }
    year, number = pairs.get("YEAR"), pairs.get("NO.CURRENT")
    if not (year and number):
        return None, "no_bibliography"
    letter = _letter_from_title(content[:16000])
    if letter is None:
        return None, "no_doctype_in_title"
    return f"3{year}{letter}{int(number):04d}", "indexed"


def celex_from_formex(content: bytes) -> str | None:
    """Reconstruct a sector-3 CELEX from an act body, or None if it is not one."""
    return _celex_with_reason(content)[0]


def language_from_archive_name(name: str) -> str:
    """`LEG_ES_FMX_...zip` names the Spanish archive. Defaults to English."""
    m = _ARCHIVE_LANGUAGE.match(Path(name).name)
    return to_iso639_3(m.group(1)) if m else "eng"


def _portable_path(archive: Path) -> str:
    """An archive inside the tree is named relative to it, so a committed index
    reads the same on another machine. `EU_DATADUMP_ZIP` overrides either way.
    """
    # `absolute` rather than `resolve`: an archive symlinked into the tree is
    # named by where it sits, not by where it points.
    try:
        return str(archive.absolute().relative_to(Path.cwd()))
    except ValueError:
        return str(archive)


def build_index(
    archive: Path,
    *,
    language: str | None = None,
    max_files: int | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> dict[str, object]:
    """Walk `archive` and map each CELEX to the member holding its act body.

    A CELEX appearing twice keeps the longest path, which is the canonical
    publication rather than a corrigendum stub.
    """
    entries: dict[str, str] = {}
    duplicates = 0
    scanned = 0
    reasons: Counter[str] = Counter()
    with zipfile.ZipFile(archive) as zf:
        names = [
            n
            for n in zf.namelist()
            if not n.endswith("/") and not any(n.endswith(s) for s in _SKIP_SUFFIXES)
        ]
        for name in names:
            if max_files is not None and scanned >= max_files:
                break
            scanned += 1
            try:
                celex, reason = _celex_with_reason(zf.read(name))
            except (OSError, zipfile.BadZipFile) as exc:
                celex, reason = None, "unreadable"
                logger.warning("datadump_member_unreadable", member=name, error=str(exc)[:200])
            reasons[reason] += 1
            if celex is None:
                if reason != "not_an_act":
                    logger.warning("datadump_act_not_indexed", member=name, reason=reason)
            elif celex in entries:
                duplicates += 1
                if len(name) > len(entries[celex]):
                    entries[celex] = name
            else:
                entries[celex] = name
            if on_progress is not None and scanned % 5000 == 0:
                on_progress(scanned, len(names), len(entries))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "archive": _portable_path(archive),
        "language": language or language_from_archive_name(archive.name),
        "entries": dict(sorted(entries.items())),
        "stats": {
            "files_scanned": scanned,
            "celex_indexed": len(entries),
            "skipped_non_act": reasons["not_an_act"],
            # An act the index could not name. Zero is the expected reading.
            "unindexed_acts": reasons["no_bibliography"]
            + reasons["no_doctype_in_title"]
            + reasons["unreadable"],
            "unindexed_by_reason": {
                r: n for r, n in sorted(reasons.items()) if r not in ("indexed", "not_an_act")
            },
            "duplicates": duplicates,
        },
    }


__all__ = [
    "CelexNotInDump",
    "DatadumpIndexMissing",
    "EuDatadumpAcquirer",
    "ManifestMissing",
    "build_index",
    "celex_from_formex",
    "language_from_archive_name",
]
