"""Evidence that a stored unit carries no law: a non-transcription marker, a
table-shaped row, or a digit-dominant row. Nothing here reads words, so a new
jurisdiction needs at most a marker pattern of its own. A unit under a heading the
jurisdiction declares in `pool_excluded_headings` is law, but not law on the
instrument's subjects, and leaves the pool too.

`sentinels-by-layer` (DECISIONS.md) bans a sentinel from stored AKN. One is emitted
anyway by the acquisition path that inlines annexes, so the marker is in the corpus
and must not answer a search or feed a lens as if it were law.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from codify.akn.elements import BodyElement

# Bounded so neither Python nor Postgres backtracks quadratically on a long unit;
# Postgres caps a repetition bound at 255.
PLACEHOLDER_PATTERN = r"^\s*\[[^\]\n]{0,200} not transcribed: [^\]\n]{0,250}\]\s*$"
DEFAULT_MARKERS: tuple[str, ...] = (PLACEHOLDER_PATTERN,)
# Platform defaults, recorded here until a jurisdiction gives a reason to override.
DIGIT_SHARE_THRESHOLD = 0.3
FORM_MAX_WORDS = 25
_TABLE_SHAPE = re.compile(r"\|.+\||\.{4,}|_{3,}|☐")
_OPERATIVE = re.compile(r"\b(shall|must|may|should|is|are|will|has|have|be|means)\b", re.I)
# The kinds the row split stores as sections: only their headings scope a pool exclusion.
SCOPING_KINDS = frozenset({"title", "chapter", "section", "article"})


@lru_cache(maxsize=64)
def _compiled(markers: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(m, re.IGNORECASE) for m in markers)


def is_placeholder_only(text: str | None, markers: Sequence[str] = DEFAULT_MARKERS) -> bool:
    """Whether a provision's whole text is a non-transcription marker."""
    if text is None:
        return False
    # Whole-string by contract, whatever anchors a configuration author supplied.
    return any(p.fullmatch(text) is not None for p in _compiled(tuple(markers)))


def exclusion_reason(
    text: str | None,
    akn_eid: str = "",
    markers: Sequence[str] = DEFAULT_MARKERS,
    *,
    under_excluded_heading: bool = False,
) -> str | None:
    """Why a unit carries no law, or None when it may: `placeholder` for a whole-string
    marker, `heading` for a unit under an excluded heading, `table` for a short unit
    shaped like a table row or form field, `digits` for a digit-dominant annex row with
    no operative verb. A long unit is law however it is punctuated; an empty unit is a
    heading, judged elsewhere."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    if is_placeholder_only(stripped, markers):
        return "placeholder"
    if under_excluded_heading:
        return "heading"
    words = len(stripped.split())
    operative = _OPERATIVE.search(stripped) is not None
    # A short table-shaped unit is data unless it states an obligation or a definition.
    if words < FORM_MAX_WORDS and not operative and _TABLE_SHAPE.search(stripped):
        return "table"
    if akn_eid.startswith("att_") and not operative:
        alnum = sum(c.isalnum() for c in stripped)
        if alnum and sum(c.isdigit() for c in stripped) / alnum > DIGIT_SHARE_THRESHOLD:
            return "digits"
    return None


def is_excluded_heading(heading: str | None, excluded: frozenset[str]) -> bool:
    """Whether a container heading, stripped, is one of `excluded` as written."""
    return bool(heading) and (heading or "").strip() in excluded


def heading_scoped(body: Iterable[BodyElement], excluded: frozenset[str]) -> frozenset[UUID]:
    """Ids of the elements at or under a scoping container whose heading is excluded."""
    if not excluded:
        return frozenset()
    found: set[UUID] = set()

    def visit(elements: Iterable[BodyElement], scoped: bool) -> None:
        for el in elements:
            opens = el.kind in SCOPING_KINDS and is_excluded_heading(el.heading, excluded)
            here = scoped or opens
            if here:
                found.add(el.id)
            visit(el.children, here)

    visit(body, False)
    return frozenset(found)


__all__ = [
    "DEFAULT_MARKERS",
    "DIGIT_SHARE_THRESHOLD",
    "FORM_MAX_WORDS",
    "PLACEHOLDER_PATTERN",
    "SCOPING_KINDS",
    "exclusion_reason",
    "heading_scoped",
    "is_excluded_heading",
    "is_placeholder_only",
]
