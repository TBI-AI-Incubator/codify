"""Find HS, CN and TARIC codes. Shape alone cannot: eight digits is also a
date. A column is typed by its header, a mention by its phrase; both English."""

from __future__ import annotations

import re
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
    rf"(?<![\d.])([Ee]x[\s\u00a0]*)?({_CODE_SHAPE}|\d{{2}})(?![\d.])"
    r"[\s\u00a0]*[\u2013\u2014-](?=[\s\u00a0]*[^\W\d_])"
)
# Codes listed ahead of an entry share its description: "7208, 7209 – Flat".
_LISTED = (
    rf"(?<![\d.])(?:[Ee]x[\s\u00a0]*)?(?:{_CODE_SHAPE})(?![\d.])"
    r"[\s\u00a0]*(?:[,;]|\band\b|\bor\b)[\s\u00a0]*"
)
_LIST_TAIL = re.compile(rf"(?:{_LISTED})+\Z")
_LISTED_CODE = re.compile(rf"(?<![\d.])([Ee]x[\s\u00a0]*)?({_CODE_SHAPE})(?![\d.])")
# A range has no single code to stand for it, so its entry is dropped.
_RANGE_TAIL = re.compile(
    rf"(?<![\d.])(?:{_CODE_SHAPE})[\s\u00a0]*(?:\bto\b|[\u2013\u2014-])[\s\u00a0]*\Z"
)
# Chapter, heading, one-dash subheading, subheading, CN, TARIC.
_ENTRY_LENGTHS = frozenset({2, 4, 5, 6, 8, 10})
# "Except:" opens a carve-out. "other than" is left alone: it qualifies a
# description ("ores, other than roasted pyrites"), not the lines after it.
_EXCEPT = re.compile(r"(?:\b|(?<=[a-z]))(?:[Ee]xcept|[Ee]xcluding)(?:\s*:|\s+(?=ex\s|\d))")
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
    entries: list[tuple[int, re.Match[str], list[tuple[str, bool]]]] = []
    for match in _ENTRY.finditer(text):
        digits = re.sub(r"\D", "", match.group(2))
        if len(digits) not in _ENTRY_LENGTHS:
            continue
        glued = match.start() > 0 and text[match.start() - 1].isalpha()
        if glued and len(digits) < 4:
            # "PM10 – particles": a short number fused to a word is a name, not a chapter.
            continue
        head = text[: match.start()]
        start, codes = match.start(), list[tuple[str, bool]]()
        tail = _LIST_TAIL.search(head)
        if tail is not None:
            start = tail.start()
            for code in _LISTED_CODE.finditer(tail.group(0)):
                codes.append((re.sub(r"\D", "", code.group(2)), bool(code.group(1))))
        elif _RANGE_TAIL.search(head):
            entries.append((match.start(), match, []))
            continue
        codes.append((digits, bool(match.group(1))))
        entries.append((start, match, codes))
    cuts = [c for c in _EXCEPT.finditer(text) if _opens_entry(text, c, entries)]
    out: list[tuple[str, bool, bool, str]] = []
    covered: list[str] = []
    excluding, anchors = False, []
    for index, (start, match, codes) in enumerate(entries):
        end = entries[index + 1][0] if index + 1 < len(entries) else len(text)
        previous = entries[index - 1][0] if index else 0
        if any(previous <= c.start() < start for c in cuts):
            excluding, anchors = True, list(covered)
        cut = next((c.start() for c in cuts if start < c.start() < end), None)
        surface = text[start : cut if cut is not None else end].strip().rstrip(";,:").strip()
        for digits, partial in codes:
            if excluding and anchors and not any(digits.startswith(a) for a in anchors):
                excluding = False
            if not excluding:
                covered.append(digits)
            out.append((digits, partial, excluding, surface))
    return out


def _opens_entry(
    text: str,
    cut: re.Match[str],
    entries: list[tuple[int, re.Match[str], list[tuple[str, bool]]]],
) -> bool:
    """Whether an entry follows "Except" directly: "excluding 10 % alloy" is prose."""
    following = next((start for start, _, _ in entries if start >= cut.end()), None)
    return following is not None and not text[cut.end() : following].strip(" :\u00a0")


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
