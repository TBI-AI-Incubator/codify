"""AKN eId parsing, sibling of apps/web/src/lib/eid.ts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

UNIT_WORDS: dict[str, str] = {
    "book": "Book",
    "part": "Part",
    "tit": "Title",
    "ttl": "Title",
    "title": "Title",
    "chp": "Chapter",
    "chapter": "Chapter",
    "sec": "Section",
    "section": "Section",
    "art": "Article",
    "article": "Article",
    "reg": "Regulation",
    "rule": "Rule",
    "para": "Paragraph",
    "subsec": "Subsection",
    "subpara": "Subparagraph",
    "pt": "Point",
    "point": "Point",
    "item": "Item",
    "content": "",
}

# eId prefix per the AKN 3.0 naming convention, listed only where it differs
# from the element name. Bluebell applies the same table when it serialises, and
# test_the_abbreviations_agree_with_bluebell fails if the two drift. A config
# may override an entry; see HierarchyEntry.eid_abbrev.
# `list` is blockList's prefix, never `point`'s.
EID_ABBREV: dict[str, str] = {
    "alinea": "al",
    "amendmentBody": "body",
    "article": "art",
    "attachment": "att",
    "blockList": "list",
    "chapter": "chp",
    "citation": "cit",
    "citations": "cits",
    "clause": "cl",
    "component": "cmp",
    "componentRef": "cref",
    "components": "cmpnts",
    "debateBody": "body",
    "debateSection": "dbsect",
    "division": "dvs",
    "documentRef": "dref",
    "eventRef": "eref",
    "judgmentBody": "body",
    "listIntroduction": "intro",
    "listWrapUp": "wrapup",
    "mainBody": "body",
    "paragraph": "para",
    "quotedStructure": "qstr",
    "quotedText": "qtext",
    "recital": "rec",
    "recitals": "recs",
    "section": "sec",
    "subchapter": "subchp",
    "subclause": "subcl",
    "subdivision": "subdvs",
    "subparagraph": "subpara",
    "subsection": "subsec",
    "temporalGroup": "tmpg",
    "wrapUp": "wrapup",
}


def eid_abbrev(element: str) -> str:
    """The eId prefix for an AKN element name."""
    return EID_ABBREV.get(element, element)


ContainerKind = Literal[
    "book",
    "part",
    "title",
    "chapter",
    "section",
    "article",
    "paragraph",
    "subparagraph",
    "point",
    "item",
]

CONTAINER_KINDS: tuple[ContainerKind, ...] = (
    "book",
    "part",
    "title",
    "chapter",
    "section",
    "article",
    "paragraph",
    "subparagraph",
    "point",
    "item",
)

_PREFIX_TO_KIND: dict[str, ContainerKind] = {
    "book": "book",
    "part": "part",
    "tit": "title",
    "ttl": "title",
    "title": "title",
    "chp": "chapter",
    "chapter": "chapter",
    "sec": "section",
    "section": "section",
    "art": "article",
    "article": "article",
    "para": "paragraph",
    "subpara": "subparagraph",
    "pt": "point",
    "point": "point",
    "item": "item",
}

_SEG = re.compile(r"^([a-z]+)(?:_(.*))?$", re.IGNORECASE)


@dataclass(frozen=True)
class EidContainer:
    prefix: str
    number: str
    raw: str
    kind: ContainerKind | None


def parse_eid(eid: str) -> list[EidContainer]:
    """Parse an eId into ordered containers. Unknown prefixes get `kind=None`."""
    if not eid:
        return []
    out: list[EidContainer] = []
    for segment in eid.split("__"):
        match = _SEG.match(segment)
        prefix = (match.group(1) if match else segment).lower()
        number = match.group(2) if match and match.group(2) else ""
        out.append(
            EidContainer(
                prefix=prefix,
                number=number,
                raw=segment,
                kind=_PREFIX_TO_KIND.get(prefix),
            )
        )
    return out


def ancestor_chain(eid: str) -> list[str]:
    """All ancestor eIds, root → self (inclusive)."""
    if not eid:
        return []
    segs = eid.split("__")
    return ["__".join(segs[: i + 1]) for i in range(len(segs))]


def parent_eid(eid: str) -> str | None:
    """The eId of the immediate parent container, or None if root-level."""
    if not eid:
        return None
    i = eid.rfind("__")
    return None if i == -1 else eid[:i]


def _compare_number(a: str, b: str) -> int:
    a_chunks = re.split(r"[._]", a)
    b_chunks = re.split(r"[._]", b)
    for i in range(max(len(a_chunks), len(b_chunks))):
        av = a_chunks[i] if i < len(a_chunks) else ""
        bv = b_chunks[i] if i < len(b_chunks) else ""
        a_is_num = av.isdigit()
        b_is_num = bv.isdigit()
        if a_is_num and b_is_num:
            an, bn = int(av), int(bv)
            if an != bn:
                return -1 if an < bn else 1
            continue
        if av != bv:
            return -1 if av < bv else 1
    return 0


def compare_document_order(a: str, b: str) -> int:
    """Document-order comparator: numeric per chunk where possible."""
    as_ = parse_eid(a)
    bs = parse_eid(b)
    length = max(len(as_), len(bs))
    for i in range(length):
        ai = as_[i] if i < len(as_) else None
        bi = bs[i] if i < len(bs) else None
        if ai is None:
            return -1
        if bi is None:
            return 1
        if ai.prefix != bi.prefix:
            ak = CONTAINER_KINDS.index(ai.kind) if ai.kind else 999
            bk = CONTAINER_KINDS.index(bi.kind) if bi.kind else 999
            if ak != bk:
                return -1 if ak < bk else 1
            return -1 if ai.prefix < bi.prefix else 1
        num_cmp = _compare_number(ai.number, bi.number)
        if num_cmp != 0:
            return num_cmp
    return 0


__all__ = [
    "CONTAINER_KINDS",
    "ContainerKind",
    "EidContainer",
    "UNIT_WORDS",
    "ancestor_chain",
    "compare_document_order",
    "parent_eid",
    "parse_eid",
]
