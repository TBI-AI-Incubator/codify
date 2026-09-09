"""eId → source-page mapping for the repair loop.

The ingest ``page_texts`` artifact concatenates per-page OCR text and discards
the per-page boundaries, so there is no stored bridge from a provision back to
the scanned page it came from. This rebuilds that bridge:

  * ``combine_with_spans`` (``ocr.combine_page_texts_with_spans``) assembles the
    combined text and records each page's ``[start, end)`` span in it, from one
    pass, so text and offsets cannot drift apart.
  * ``eid_to_page`` scans anchors over that text and maps each eId's char
    offset to its page via the span table.

Spans are persisted into ``page_texts.content_json`` at extract time; for older
versions the caller re-derives them from the PDF.
"""

from __future__ import annotations

from typing import Any

from codify.pipeline.enrich.anchors import (
    _normalise_num,
    _rank_map_for,
    _rank_of,
    cached_regex,
    scan_anchors,
)
from codify.pipeline.enrich.ocr import PageSpan, combine_page_texts_with_spans

# Assembly moved to `ocr`, next to `combine_page_texts`, so the structurer's
# region filter can scope furniture to its own page without importing `repair`.
combine_with_spans = combine_page_texts_with_spans


def spans_to_json(spans: list[PageSpan]) -> list[dict[str, Any]]:
    return [s.model_dump() for s in spans]


def spans_from_json(raw: list[dict[str, Any]] | None) -> list[PageSpan]:
    return [PageSpan.model_validate(s) for s in raw or []]


def offset_to_page(spans: list[PageSpan], offset: int) -> int | None:
    """The page whose span contains ``offset``; clamps to the ends."""
    if not spans:
        return None
    for span in spans:
        if span.start <= offset < span.end:
            return span.page
    return spans[0].page if offset < spans[0].start else spans[-1].page


def eid_to_span(text: str, *, country: str, doctype: str = "act") -> dict[str, tuple[int, int]]:
    """Map each anchor's eId to its body's ``[start, end)`` span in ``text``.

    When a number appears both in a table-of-contents index and again as the
    real article (same kind + number + parent), both anchors are grouped and
    mapped to the occurrence with the most following text, the body, not the
    bare index line, so a repair reads the article, not the TOC entry.
    """
    regex = cached_regex(country, doctype)
    anchors = scan_anchors(text, regex, country=country, doctype=doctype)
    n = len(text)
    ranks = _rank_map_for(country, doctype)
    groups: dict[tuple[str, str | None, str | None], list[tuple[str, int, int]]] = {}
    for i, a in enumerate(anchors):
        if not a.akn_eid:
            continue
        # The span ends at the next SAME-OR-HIGHER-rank anchor: a nested child
        # anchor belongs inside its parent's body, or an article's restore
        # would stop at its own first subsection.
        rank = _rank_of(a.kind, ranks)
        nxt = next(
            (b.char_offset for b in anchors[i + 1 :] if _rank_of(b.kind, ranks) <= rank),
            n,
        )
        key = (a.kind, _normalise_num(a.number), a.parent_eid)
        groups.setdefault(key, []).append((a.akn_eid, a.char_offset, nxt - a.char_offset))

    out: dict[str, tuple[int, int]] = {}
    for members in groups.values():
        body = max(members, key=lambda m: m[2])  # most following text = the body
        for eid, _off, _len in members:
            out[eid] = (body[1], body[1] + body[2])
    return out


def eid_to_page(
    text: str, spans: list[PageSpan], *, country: str, doctype: str = "act"
) -> dict[str, int]:
    """Map each anchor's eId to the source page its body sits on (span start)."""
    out: dict[str, int] = {}
    for eid, (start, _end) in eid_to_span(text, country=country, doctype=doctype).items():
        page = offset_to_page(spans, start)
        if page is not None:
            out[eid] = page
    return out
