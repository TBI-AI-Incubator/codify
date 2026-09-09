"""Evidence that a stored unit carries no law: a non-transcription marker, a
table-shaped row, or a digit-dominant row. Nothing here reads words, so a new
jurisdiction needs at most a marker pattern of its own.

`sentinels-by-layer` (DECISIONS.md) bans a sentinel from stored AKN. One is emitted
anyway by the acquisition path that inlines annexes, so the marker is in the corpus
and must not answer a search or feed a lens as if it were law.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

# Bounded so neither Python nor Postgres backtracks quadratically on a long unit;
# Postgres caps a repetition bound at 255.
PLACEHOLDER_PATTERN = r"^\s*\[[^\]\n]{0,200} not transcribed: [^\]\n]{0,250}\]\s*$"
DEFAULT_MARKERS: tuple[str, ...] = (PLACEHOLDER_PATTERN,)
# Platform defaults, recorded here until a jurisdiction gives a reason to override.
DIGIT_SHARE_THRESHOLD = 0.3
FORM_MAX_WORDS = 25
_TABLE_SHAPE = re.compile(r"\|.+\||\.{4,}|_{3,}|☐")
_OPERATIVE = re.compile(r"\b(shall|must|may|should|is|are|will|has|have|be|means)\b", re.I)


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
    text: str | None, akn_eid: str = "", markers: Sequence[str] = DEFAULT_MARKERS
) -> str | None:
    """Why a unit carries no law, or None when it may: `placeholder` for a whole-string
    marker, `table` for a short unit shaped like a table row or form field, `digits`
    for a digit-dominant annex row with no operative verb. A long unit is law however
    it is punctuated; an empty unit is a heading, judged elsewhere."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    if is_placeholder_only(stripped, markers):
        return "placeholder"
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


__all__ = [
    "DEFAULT_MARKERS",
    "DIGIT_SHARE_THRESHOLD",
    "FORM_MAX_WORDS",
    "PLACEHOLDER_PATTERN",
    "exclusion_reason",
    "is_placeholder_only",
]
