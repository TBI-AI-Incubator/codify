"""Find HS, CN and TARIC codes. Shape alone cannot: eight digits is also a
date. A column is typed by its header, a mention by its phrase; both English."""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass
from typing import Literal

from lxml import etree

from codify.akn._schema import parse_xml
from codify.tables.resolve import ResolvedRow, resolve_tables

CodeSystem = Literal["hs", "cn", "taric"]

# Header labels seen in the corpus, lowercased and stripped of punctuation.
# A label naming a subdivision is excluded: that column holds "10", not a code.
_HEADER_SYSTEM: tuple[tuple[re.Pattern[str], CodeSystem], ...] = (
    (re.compile(r"\btaric\s+(additional\s+)?code\b"), "taric"),
    (re.compile(r"\bhs\b|\bharmoni[sz]ed\s+system\b"), "hs"),
    (re.compile(r"\bcn\b|\bcombined\s+nomenclature\b|\bcustoms\s+code\b"), "cn"),
)
_HEADER_EXCLUDE = re.compile(r"sub-?division|additional\s+code\s+ordinal")

# "ex" before a code restricts it to part of the heading, which changes what the
# measure covers, so it is kept rather than normalised away.
_PARTIAL = re.compile(r"^ex[\s ]*", re.IGNORECASE)
_CODE = re.compile(r"^\d{4}(?:[\s. ]?\d{2}){1,3}$|^\d{4}$")

# A code in prose counts only when one of these introduces it.
_PROSE_ANCHOR = re.compile(
    r"(?:cn|hs|taric)\s*(?:code|heading|subheading)s?\b"
    r"|\bcombined\s+nomenclature\b"
    r"|\bfalling\s+within\s+(?:cn\s+)?(?:code|heading)s?\b"
    r"|\btariff\s+(?:heading|code)s?\b",
    re.IGNORECASE,
)
_PROSE_CODE = re.compile(r"\b(ex\s+)?(\d{4}(?:[\s.]\d{2}){1,3}|\d{6}|\d{8}|\d{10})\b")
# Spaced or dotted pairs, a one-dash subheading ("7202 2"), or unbroken digits.
# Separators never mix, so "7208 7209" cannot read as one code.
_CODE_SHAPE = r"\d{4}(?:[.\s\u00a0]\d{2}){1,3}|\d{4}[\s\u00a0]\d|\d{10}|\d{8}|\d{6}|\d{4}"
# A code-column line, "72 – Iron and steel": the dash and word make it a code.
# Stored AKN can run lines together ("Ferro-silicon72023000 –").
_ENTRY = re.compile(
    rf"(?<![\d.])((?<![A-Za-z])(?i:ex)[\s\u00a0]*)?({_CODE_SHAPE}|\d{{2}})(?![\d.])"
    r"[\s\u00a0]*[\u2013\u2014-](?=[\s\u00a0]*[^\W\d_])"
)
# "7208 to 7212 – Flat" has no single code to stand for it, so it yields none.
_RANGE_TAIL = re.compile(
    rf"(?<![\d.])(?:{_CODE_SHAPE})[\s\u00a0]*(?:\bto\b|[\u2013\u2014-])[\s\u00a0]*\Z"
)
_RANGE_REACH = 32
# Chapter, heading, one-dash subheading, subheading, CN, TARIC.
_ENTRY_LENGTHS = frozenset({2, 4, 5, 6, 8, 10})
# "Except:" opens a carve-out. "other than" is left alone: it qualifies a
# description ("ores, other than roasted pyrites"), not the lines after it.
_EXCEPT = re.compile(r"(?:\b|(?<=[a-z]))(?:except|excluding)(?:\s*:|\s+(?=ex\s|\d))", re.IGNORECASE)
_LIST_SPLIT = re.compile(r"\s*(?:[,;]|\band\b)\s*")
# How far after the anchor a code still belongs to it.
_PROSE_WINDOW = 120
# A four-digit code is an HS heading, which CN and TARIC both extend.
_BY_LENGTH: dict[int, CodeSystem] = {4: "hs", 6: "hs", 8: "cn", 10: "taric"}

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


@dataclass(frozen=True)
class GoodsCode:
    """One code, with the form it was printed in and where it was found."""

    system: CodeSystem
    code: str
    surface: str
    partial: bool
    eid: str
    source: Literal["column", "prose"]
    # Outermost first. Empty for a prose mention, which has no table above it.
    lineage: tuple[str, ...] = ()
    # Listed after "Except": the measure names it to carve it out.
    excluded: bool = False


def _text(el: etree._Element) -> str:
    return " ".join("".join(el.itertext()).split())


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def system_for_header(label: str) -> CodeSystem | None:
    """The nomenclature a column header names, or None if it names none."""
    text = label.lower().replace("(", " ").replace(")", " ")
    if _HEADER_EXCLUDE.search(text):
        return None
    for pattern, system in _HEADER_SYSTEM:
        if pattern.search(text):
            return system
    return None


def normalise(value: str) -> tuple[str, bool] | None:
    """Digits and a partial flag, or None when the cell is not a single code.

    A range or a compound expression returns None rather than a truncation: a
    wrong code reads as authoritative, and a missing one can be looked up.
    """
    surface = value.strip().replace(" ", " ")
    partial = bool(_PARTIAL.match(surface))
    body = _PARTIAL.sub("", surface).strip()
    if not _CODE.match(body):
        return None
    digits = re.sub(r"\D", "", body)
    if len(digits) not in (4, 6, 8, 10):
        return None
    return digits, partial


def cell_codes(text: str) -> list[tuple[str, bool, bool, str]]:
    """(digits, partial, excluded, surface) for each code a code-column cell
    states: one bare code, a list of them, or lines of "code – description".
    A carve-out runs from "Except" while its codes narrow one listed before it."""
    single = normalise(text)
    if single is not None:
        return [(single[0], single[1], False, text.strip())]
    pieces = [p for p in _LIST_SPLIT.split(text) if p.strip()]
    listed = [normalise(p) for p in pieces]
    if len(pieces) > 1 and all(listed):
        return [(c[0], c[1], False, p.strip()) for c, p in zip(listed, pieces) if c]
    # (start, digits, partial); digits is None for a range, which still bounds lines.
    entries: list[tuple[int, str | None, bool]] = []
    previous_end = 0
    for match in _ENTRY.finditer(text):
        digits = re.sub(r"\D", "", match.group(2))
        if len(digits) not in _ENTRY_LENGTHS:
            continue
        glued = match.start() > 0 and text[match.start() - 1].isalpha()
        if glued and len(digits) < 4:
            # "PM10 – particles": a short number fused to a word is a name, not a chapter.
            continue
        reach = max(previous_end, match.start() - _RANGE_REACH)
        span = _RANGE_TAIL.search(text, reach, match.start())
        if span is not None:
            entries.append((span.start(), None, False))
        else:
            entries.append((match.start(), digits, bool(match.group(1))))
        previous_end = match.end()
    starts = [start for start, _, _ in entries]
    cuts = [c.start() for c in _EXCEPT.finditer(text) if _opens_entry(text, c, starts)]
    out: list[tuple[str, bool, bool, str]] = []
    covered: set[str] = set()
    excluding, chapter, at = False, "", 0
    for index, (start, stated, partial) in enumerate(entries):
        end = starts[index + 1] if index + 1 < len(entries) else len(text)
        opened = False
        while at < len(cuts) and cuts[at] < start:
            opened, at = True, at + 1
        if opened:
            excluding, chapter = True, ""
        cut = cuts[at] if at < len(cuts) and cuts[at] < end else end
        if stated is None:
            continue
        digits = stated
        if excluding:
            # With nothing listed before the cut, the first carve-out names its chapter.
            chapter = chapter or ("" if covered else digits[:2])
            if not _narrows(digits, covered or {chapter}):
                excluding = False
        if not excluding:
            covered.add(digits)
        surface = text[start:cut].strip().rstrip(";,:").strip()
        out.append((digits, partial, excluding, surface))
    return out


def _narrows(digits: str, prefixes: set[str]) -> bool:
    return any(digits[:n] in prefixes for n in range(2, len(digits) + 1))


def _opens_entry(text: str, cut: re.Match[str], starts: list[int]) -> bool:
    """Whether an entry follows "Except" directly: "excluding 10 % alloy" is prose."""
    index = bisect_left(starts, cut.end())
    return index < len(starts) and not text[cut.end() : starts[index]].strip(" :\u00a0")


def _from_rows(rows: list[ResolvedRow]) -> list[GoodsCode]:
    found: list[GoodsCode] = []
    for row in rows:
        for cell in row.cells:
            system = system_for_header(cell.label)
            if system is None:
                continue
            for code, partial, excluded, surface in cell_codes(cell.text):
                found.append(
                    GoodsCode(
                        system=system,
                        code=code,
                        surface=surface,
                        partial=partial,
                        eid=row.akn_eid,
                        source="column",
                        lineage=row.lineage,
                        excluded=excluded,
                    )
                )
    return found


def _from_prose(root: etree._Element) -> list[GoodsCode]:
    found: list[GoodsCode] = []
    for para in root.iter("{%s}p" % _AKN_NS):
        text = _text(para)
        if not _PROSE_ANCHOR.search(text):
            continue
        eid = ""
        for ancestor in [para, *para.iterancestors()]:
            if ancestor.get("eId"):
                eid = ancestor.get("eId", "")
                break
        anchors = list(_PROSE_ANCHOR.finditer(text))
        for position, anchor in enumerate(anchors):
            label = anchor.group(0).lower()
            named: CodeSystem | None = None
            if "taric" in label:
                named = "taric"
            elif label.startswith("hs"):
                named = "hs"
            elif "cn" in label or "combined nomenclature" in label:
                named = "cn"
            # The next anchor ends this one's claim, or a sentence naming two
            # nomenclatures types every code in it as the first.
            stop = anchor.end() + _PROSE_WINDOW
            if position + 1 < len(anchors):
                stop = min(stop, anchors[position + 1].start())
            window = text[anchor.end() : stop]
            for match in _PROSE_CODE.finditer(window):
                parsed = normalise(match.group(0))
                if parsed is None:
                    continue
                code, partial = parsed
                # An anchor that names no system leaves the length to say:
                # HS is six digits, CN eight, TARIC ten.
                system = named or _BY_LENGTH.get(len(code), "cn")
                found.append(
                    GoodsCode(
                        system=system,
                        code=code,
                        surface=match.group(0).strip(),
                        partial=partial,
                        eid=eid,
                        source="prose",
                    )
                )
    return found


def extract_goods_codes(akn: str | bytes) -> list[GoodsCode]:
    """Every HS, CN or TARIC code the document states, deduplicated."""
    root = parse_xml(akn)
    seen: set[tuple[str, str, str, bool, bool]] = set()
    out: list[GoodsCode] = []
    for code in _from_rows(resolve_tables(akn)) + _from_prose(root):
        key = (code.system, code.code, code.eid, code.partial, code.excluded)
        if key in seen:
            continue
        seen.add(key)
        out.append(code)
    return out


__all__ = ["GoodsCode", "cell_codes", "extract_goods_codes", "normalise", "system_for_header"]
