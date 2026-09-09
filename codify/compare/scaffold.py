"""Deterministic helpers, provision iteration, eId indexing, summary."""

from __future__ import annotations

import re
from collections.abc import Iterator

from codify.akn.document import Document
from codify.akn.elements import BodyElement
from codify.compare.types import AlignmentLevel, AlignmentSummary, ProvisionAlignment

ASSESSABLE_KINDS: tuple[str, ...] = ("article", "section", "paragraph")


def _walk(elements: list[BodyElement]) -> Iterator[BodyElement]:
    for el in elements:
        yield el
        yield from _walk(el.children)


def markers_for(doc: Document) -> tuple[str, ...]:
    """The placeholder markers that apply to this document's jurisdiction."""
    from codify.jurisdictions import placeholder_markers_for

    return placeholder_markers_for(doc.frbr_work_uri)


def _own_text(el: BodyElement) -> str:
    return "\n".join(p for p in (el.intro, el.text, el.wrap_up) if p).strip()


def is_excluded(el: BodyElement, doc: Document) -> bool:
    """The stored exclusion rule on a unit's own text, as the mapper judges a
    row: a marker child never taints its parent, and a table-shaped child never
    excludes a substantive one. Empty units stay with the structural rule."""
    return unit_excluded(el, markers_for(doc))


def unit_excluded(el: BodyElement, markers: tuple[str, ...]) -> bool:
    from codify.quality.sentinels import exclusion_reason

    return exclusion_reason(_own_text(el), el.akn_eid or "", markers) is not None


def iter_assessable(doc: Document) -> Iterator[BodyElement]:
    for el in _walk(doc.body):
        if el.kind in ASSESSABLE_KINDS:
            yield el


def effective_headings(doc: Document) -> dict[str, str]:
    """Return display headings, inheriting an article heading when needed."""
    out: dict[str, str] = {}

    def visit(elements: list[BodyElement], inherited: str | None) -> None:
        for el in elements:
            own = el.heading or inherited
            if el.akn_eid and own:
                out[el.akn_eid] = own
            # Carry only article-level headings down to paragraphs/points.
            nxt = el.heading if el.kind == "article" else inherited
            visit(el.children, nxt)

    visit(doc.body, None)
    return out


def provision_text(el: BodyElement, markers: tuple[str, ...] | None = None) -> str:
    """The unit's text with its non-assessable descendants folded in. With
    `markers`, descendants the exclusion rule would drop as rows are left out,
    so the comparator reads the same text the row pool holds."""
    parts = [p for p in (el.intro, el.text, el.wrap_up) if p]
    # Include non-assessable sub-points, but avoid double-counting assessable
    # paragraph and section children.
    for child in el.children:
        if child.kind not in ASSESSABLE_KINDS:
            if markers is not None and unit_excluded(child, markers):
                continue
            child_text = provision_text(child, markers)
            if child_text:
                parts.append(child_text)
    return "\n".join(parts).strip()


def is_structural(el: BodyElement) -> bool:
    """Heading-only container, such as ``Section I``; not a target."""
    return not provision_text(el)


_ART_RE = re.compile(r"art_([0-9]+[a-z]?)")


def article_label(eid: str) -> str | None:
    """Derive a citable article label from an eId, when available."""
    nums = _ART_RE.findall(eid)
    return f"Article {nums[-1]}" if nums else None


# Below this floor there is too little evidence for an alignment claim.
_MIN_ASSESSABLE = 5


def classify_alignment_level(aligned: int, partial: int, gap: int) -> AlignmentLevel:
    """Map an obligation distribution to the EC legal-alignment scale, for a
    subject already known to have been assessed. Partial counts as half-credit;
    below `_MIN_ASSESSABLE` the verdict is `inconclusive`. Whether a subject was
    assessed at all is a separate fact (the presence of an assessment); this
    scale never answers it and so never returns `not_assessed`."""
    total = aligned + partial + gap
    if total < _MIN_ASSESSABLE:
        return "inconclusive"
    if gap == 0 and partial == 0:
        return "fully_aligned"
    share = (aligned + 0.5 * partial) / total
    if share >= 0.85:
        return "largely_aligned"
    if share >= 0.50:
        return "partially_aligned"
    return "not_aligned"


def summarise(results: list[ProvisionAlignment]) -> AlignmentSummary:
    actionable = [r for r in results if r.actionable]
    total = len(actionable)
    aligned = sum(1 for r in actionable if r.verdict == "aligned")
    partial = sum(1 for r in actionable if r.verdict == "partial")
    gap = sum(1 for r in actionable if r.verdict == "gap")
    needs_review = sum(1 for r in actionable if r.needs_review)
    na = len(results) - total
    key_gaps: list[str] = []
    for r in actionable:
        if r.verdict != "gap":
            continue
        label = r.directive_heading or article_label(r.directive_eid)
        if label and label not in key_gaps:
            key_gaps.append(label)
    key_gaps = key_gaps[:8]

    def pct(n: int) -> float:
        return (n / total * 100.0) if total else 0.0

    return AlignmentSummary(
        aligned=aligned,
        partial=partial,
        gap=gap,
        total=total,
        aligned_pct=pct(aligned),
        partial_pct=pct(partial),
        gap_pct=pct(gap),
        needs_review=needs_review,
        na=na,
        key_gaps=key_gaps,
    )


__all__ = [
    "ASSESSABLE_KINDS",
    "article_label",
    "classify_alignment_level",
    "effective_headings",
    "is_structural",
    "iter_assessable",
    "provision_text",
    "summarise",
]
