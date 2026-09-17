"""Bound the body at the closing phrase: what follows is conclusions, never body."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

import structlog

from codify.pipeline.enrich.anchors import StructuralAnchor, _closed_quote_mask

logger = structlog.get_logger()


@dataclass(frozen=True)
class BodyBound:
    """The text and anchors with the closing span cut out, and what the cut held."""

    text: str
    anchors: list[StructuralAnchor]
    conclusions: str | None = None
    cut_at: int | None = None
    excluded_anchors: int = 0
    excluded_chars: int = 0


def closing_offset(
    text: str, phrases: Iterable[str], *, after: int, quoted: Sequence[bool] = ()
) -> int | None:
    """Start of the first line opening with a closing phrase past `after`.
    `quoted` flags offsets inside quoted text, where a phrase is a quotation's."""
    words = [p for p in phrases if p]
    if not words:
        return None
    pattern = re.compile(r"(?m)^[ \t]*(?:" + "|".join(re.escape(w) for w in words) + ")")
    for m in pattern.finditer(text):
        if m.start() > after and not (m.end() <= len(quoted) and quoted[m.end() - 1]):
            return m.start()
    return None


def bound_body_at_closing(
    text: str, anchors: list[StructuralAnchor], phrases: Iterable[str], *, country: str = ""
) -> BodyBound:
    """Cut the span from the closing phrase to the first attachment caption; the
    scan has dropped and counted its markers, and its text becomes the conclusions."""
    words = [p for p in phrases if p]
    if not anchors or not words:
        return BodyBound(text, anchors)
    quoted = _closed_quote_mask(text, country)
    start = closing_offset(text, words, after=min(a.char_offset for a in anchors), quoted=quoted)
    if start is None:
        return BodyBound(text, anchors)
    end = min(
        (a.char_offset for a in anchors if a.kind == "schedule" and a.char_offset >= start),
        default=len(text),
    )
    kept: list[StructuralAnchor] = []
    excluded = 0
    for a in anchors:
        if start <= a.char_offset < end:
            excluded += 1
        elif a.char_offset >= end:
            kept.append(replace(a, char_offset=a.char_offset - (end - start)))
        else:
            kept.append(a)
    conclusions = text[start:end].strip("\n") or None
    logger.info("body_bounded_at_closing", excluded_anchors=excluded, chars=end - start)
    return BodyBound(text[:start] + text[end:], kept, conclusions, start, excluded, end - start)
