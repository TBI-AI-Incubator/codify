"""Walk a source AKN document into per-eId units + emit a Bluebell scaffold.

The LLM never sees keywords or numbers, those come from the source via the
scaffold; the LLM only fills per-eid body text + heading.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from lxml import etree
from pydantic import BaseModel

from codify.akn.document import Document
from codify.akn.elements import BodyElement
from codify.akn.vocabulary import provision_text
from codify.pipeline.enrich.kinds import kind_to_kw

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_BODY_WRAPPERS = frozenset({"intro", "content", "wrapUp"})


class SourceUnit(BaseModel):
    """One source AKN element a translation call covers. `akn_eid` matches the field on
    the ingest `StructuralAnchor`, so `assemble_filled_scaffold` splices it in without
    an adapter, and `akn_type` carries the original tag so canonical tags survive a
    Bluebell round-trip.
    """

    akn_eid: str
    kind: str
    akn_type: str
    number: str | None
    depth: int
    heading: str | None
    body_text: str


@dataclass(frozen=True)
class WalkSummary:
    units: int
    skipped_without_eid: int
    skipped_kinds: tuple[str, ...]


def _joined_body(el: BodyElement) -> str:
    parts: list[str] = []
    if el.intro:
        parts.append(el.intro.strip())
    if el.text:
        parts.append(el.text.strip())
    if el.wrap_up:
        parts.append(el.wrap_up.strip())
    return "\n".join(p for p in parts if p)


def _extract_body_lines_by_eid(source_xml: str) -> dict[str, list[str]]:
    """eId → one line per source `<p>` child of intro/content/wrapUp."""
    root = etree.fromstring(source_xml.encode("utf-8"))
    out: dict[str, list[str]] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        eid = el.get("eId")
        if not eid:
            continue
        lines: list[str] = []
        for wrapper in el:
            if etree.QName(wrapper).localname not in _BODY_WRAPPERS:
                continue
            for child in wrapper:
                if etree.QName(child).localname != "p":
                    continue
                text = provision_text(child).strip()
                if text:
                    lines.append(text)
        if lines:
            out[eid] = lines
    return out


def _attachment_units(source_xml: str, eid_to_lines: dict[str, list[str]]) -> list[SourceUnit]:
    """Units for `<attachments>` content, which `Document.body` never sees. The
    attachment element carries the heading; every eId-bearing descendant with body
    lines becomes its own unit, so the write pass patches them like body provisions.
    """
    root = etree.fromstring(source_xml.encode("utf-8"))
    units: list[SourceUnit] = []
    seen: set[str] = set()
    for att in root.iter(f"{{{_AKN_NS}}}attachment"):
        att_eid = att.get("eId")
        if not att_eid or att_eid in seen:
            continue
        heading = None
        for child in att:
            if isinstance(child.tag, str) and etree.QName(child).localname == "heading":
                heading = "".join(child.itertext()).strip() or None
                break
        units.append(
            SourceUnit(
                akn_eid=att_eid,
                kind="schedule",
                akn_type="attachment",
                number=None,
                depth=0,
                heading=heading,
                body_text="\n".join(eid_to_lines.get(att_eid, [])),
            )
        )
        seen.add(att_eid)
        # Bluebell emits flat annex prose as bare <p eId> directly under
        # <mainBody>, with no content wrapper; each is its own one-line unit.
        for main in att.iter(f"{{{_AKN_NS}}}mainBody"):
            for child in main:
                if not isinstance(child.tag, str) or etree.QName(child).localname != "p":
                    continue
                p_eid = child.get("eId")
                text = "".join(child.itertext()).strip()
                if not p_eid or p_eid in seen or not text:
                    continue
                seen.add(p_eid)
                units.append(
                    SourceUnit(
                        akn_eid=p_eid,
                        kind="point",
                        akn_type="p",
                        number=None,
                        depth=1,
                        heading=None,
                        body_text=text,
                    )
                )
        for el in att.iter():
            eid = el.get("eId") if isinstance(el.tag, str) else None
            if not eid or eid == att_eid or eid in seen or eid not in eid_to_lines:
                continue
            seen.add(eid)
            num = None
            for child in el:
                if isinstance(child.tag, str) and etree.QName(child).localname == "num":
                    num = ("".join(child.itertext()).strip()) or None
                    break
            units.append(
                SourceUnit(
                    akn_eid=eid,
                    kind="point",
                    akn_type=etree.QName(el).localname,
                    number=num,
                    depth=1,
                    heading=None,
                    body_text="\n".join(eid_to_lines[eid]),
                )
            )
    return units


def walk_source(
    doc: Document, source_xml: str | None = None
) -> tuple[list[SourceUnit], WalkSummary]:
    """Depth-first walk of `Document.body` plus `<attachments>` content;
    skips eId-less elements."""
    units: list[SourceUnit] = []
    skipped = 0
    skipped_kinds: list[str] = []
    eid_to_lines = _extract_body_lines_by_eid(source_xml) if source_xml else {}

    def _body_for(el: BodyElement) -> str:
        lines = eid_to_lines.get(el.akn_eid)
        if lines:
            return "\n".join(lines)
        return _joined_body(el)

    def visit(elements: list[BodyElement], depth: int) -> None:
        nonlocal skipped
        for el in elements:
            if el.akn_eid:
                units.append(
                    SourceUnit(
                        akn_eid=el.akn_eid,
                        kind=el.kind,
                        akn_type=el.akn_type or el.kind,
                        number=el.number,
                        depth=depth,
                        heading=(el.heading or None),
                        body_text=_body_for(el),
                    )
                )
                visit(el.children, depth + 1)
            else:
                skipped += 1
                skipped_kinds.append(el.akn_type or el.kind)
                # Eidless wrapper, visit its children at the SAME depth so
                # they hang under the eidless wrapper's parent in Bluebell.
                visit(el.children, depth)

    visit(doc.body, depth=0)
    if source_xml:
        units.extend(_attachment_units(source_xml, eid_to_lines))
    return units, WalkSummary(
        units=len(units),
        skipped_without_eid=skipped,
        skipped_kinds=tuple(sorted(set(skipped_kinds))),
    )


def walk_source_units(doc: Document, source_xml: str | None = None) -> Iterator[SourceUnit]:
    """Backwards-compatible iterator API, discards walk diagnostics."""
    units, _ = walk_source(doc, source_xml)
    yield from units


def build_translation_scaffold(
    units: Iterable[SourceUnit],
    *,
    preface_text: str | None = None,
) -> tuple[str, dict[str, SourceUnit]]:
    """Emit Bluebell with source keywords + numbers + indent; bodies blank."""
    unit_list = list(units)
    eid_to_unit: dict[str, SourceUnit] = {u.akn_eid: u for u in unit_list}

    lines: list[str] = []
    if preface_text and preface_text.strip():
        lines.append("PREFACE")
        for raw in preface_text.strip().splitlines():
            stripped = raw.strip()
            if stripped:
                lines.append(f"  {stripped}")
        lines.append("")
    lines.append("BODY")

    for u in unit_list:
        keyword = kind_to_kw(u.kind)
        indent = "  " * (u.depth + 1)
        number = u.number or ""
        header = f"{indent}{keyword} {number}".rstrip()
        lines.append(header)
        lines.append("")

    return "\n".join(lines) + "\n", eid_to_unit


__all__ = [
    "SourceUnit",
    "WalkSummary",
    "build_translation_scaffold",
    "walk_source",
    "walk_source_units",
]
