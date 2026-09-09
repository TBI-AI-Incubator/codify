"""Carry a placeholder unit's status into the AKN: a unit whose whole text is a
jurisdiction's non-transcription or filler marker gets the `@status` the marker
names (`incomplete`, `removed`, `editorial`), so the fact lives in the document
and reaches exports and readers without the database. A status already present
is left alone: an editor's word outranks a pattern.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import cast

import structlog
from lxml import etree

from codify.akn import AKN_NS
from codify.akn._parser import _extract_plain, _extract_text_and_refs
from codify.akn._schema import safe_parser
from codify.akn.vocabulary import TAG_TO_KIND

logger = structlog.get_logger()

# Every tag the row mapper can turn into a row, from the table it reads: a leaf is
# a row, and a container with its own `<content>` is one too.
UNIT_TAGS: tuple[str, ...] = tuple(TAG_TO_KIND)


def mark_placeholder_status(akn_xml: str, markers: Sequence[tuple[str, str]]) -> str:
    """The document with `@status` set on every unit matching a marker."""
    root = etree.fromstring(akn_xml.encode("utf-8"), parser=safe_parser())
    _mark(root, markers)
    return cast(str, etree.tostring(root, pretty_print=True, encoding="unicode"))


def mark_placeholder_status_if_changed(
    akn_xml: str, markers: Sequence[tuple[str, str]]
) -> str | None:
    """The marked document, or None when no unit needed marking. Both sides go
    through the same serialiser, so layout alone never reads as a change."""
    root = etree.fromstring(akn_xml.encode("utf-8"), parser=safe_parser())
    before = etree.tostring(root, pretty_print=True, encoding="unicode")
    if not _mark(root, markers):
        return None
    after = etree.tostring(root, pretty_print=True, encoding="unicode")
    return cast(str, after) if after != before else None


def _own_text(el: etree._Element) -> str:
    parts: list[str] = []
    intro = el.find(f"{{{AKN_NS}}}intro")
    content = el.find(f"{{{AKN_NS}}}content")
    wrap_up = el.find(f"{{{AKN_NS}}}wrapUp")
    if intro is not None:
        parts.append(_extract_plain(intro))
    if content is not None:
        parts.append(_extract_text_and_refs(content)[0])
    if wrap_up is not None:
        parts.append(_extract_plain(wrap_up))
    return "\n".join(p for p in parts if p.strip()).strip()


def _mark(root: etree._Element, markers: Sequence[tuple[str, str]]) -> int:
    compiled = [(re.compile(pattern, re.IGNORECASE), status) for pattern, status in markers]
    changed = 0
    for tag in UNIT_TAGS:
        for el in root.iter(f"{{{AKN_NS}}}{tag}"):
            # The unit's own text as the mapper stores it: intro, content and wrap-up,
            # read the parser's way. A list item's blocks belong to the unit holding it.
            text = _own_text(el)
            if not text:
                continue
            status = next((st for rx, st in compiled if rx.fullmatch(text)), None)
            if status is None:
                continue
            current = el.get("status")
            if current == status:
                continue
            if current:
                logger.info("placeholder_status_kept", eid=el.get("eId"), status=current)
                continue
            el.set("status", status)
            changed += 1
    return changed


__all__ = ["UNIT_TAGS", "mark_placeholder_status", "mark_placeholder_status_if_changed"]
