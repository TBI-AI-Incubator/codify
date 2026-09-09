"""Chapter-locality batching for body-fill. Flat batching split one chapter across
independent calls with no shared register; these groups carry articles from the
same nearest chapter container, and a group's batches run serially so exemplars
feed later ones. No chapter-tier containers falls back to one flat group.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codify.translate.anchors import SourceUnit

# Container kinds we treat as chapter-tier for locality grouping. Widened
# from just "chapter" to catch civil-code hierarchies (book/title/part) so
# an act's top-level container becomes the register-locking unit.
CHAPTER_KINDS: frozenset[str] = frozenset(
    {"book", "tome", "part", "subpart", "title", "subtitle", "chapter", "subchapter"}
)


@dataclass
class ChapterBatchGroup:
    """One chapter's units split into batches. ``label`` is the human-readable
    chapter identifier for logging/prompt context; empty when the group is
    the fallback flat one."""

    label: str
    units: list[SourceUnit] = field(default_factory=list)
    batches: list[list[SourceUnit]] = field(default_factory=list)


def _chapter_label(unit: SourceUnit) -> str:
    heading = (unit.heading or "").strip()
    number = (unit.number or "").strip()
    kind = unit.kind.strip().capitalize()
    if number and heading:
        return f"{kind} {number}: {heading}"
    if heading:
        return f"{kind}: {heading}"
    if number:
        return f"{kind} {number}"
    return unit.akn_eid or kind


def group_by_chapter(units: list[SourceUnit], batch_size: int) -> list[ChapterBatchGroup]:
    """Group flat units into chapter-scoped batches. A `CHAPTER_KINDS` unit opens a
    group and rides in it, so its heading shares a batch with its opening articles;
    earlier units land in a synthetic preamble group; each splits at `batch_size`, in
    source order. No chapter tier yields one unlabelled group, exemplars still threading.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    groups: list[ChapterBatchGroup] = []
    current = ChapterBatchGroup(label="")
    for unit in units:
        if unit.kind.lower() in CHAPTER_KINDS:
            if current.units:
                groups.append(current)
            current = ChapterBatchGroup(label=_chapter_label(unit))
        current.units.append(unit)
    if current.units:
        groups.append(current)

    for group in groups:
        group.batches = [
            group.units[i : i + batch_size] for i in range(0, len(group.units), batch_size)
        ]

    return groups


__all__ = ["CHAPTER_KINDS", "ChapterBatchGroup", "group_by_chapter"]
