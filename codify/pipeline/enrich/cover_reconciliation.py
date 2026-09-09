"""Cover / table-of-contents reconciliation for OCR ingest.

The cover is an independent cardinality signal the drafters wrote, and comparing the
SET it declares against the emitted eIds catches whole-article drops that break no
monotonicity. Fail-open: nothing plausibly TOC-shaped returns ``None``. The marker
regex alone is insufficient, since body headings match it, so a contents keyword or a
high marker density is also required.
"""

from __future__ import annotations

import re

from codify.lang import normalise_digits

# Window scanned for a cover TOC, roughly three pages of Arabic legal prose. Bounded
# so a mid-body article list cannot be mistaken for the cover. Character-based rather
# than form-feed-based because `_strip_control_chars` in the extract stage removes
# U+000C, so no page-break delimiter survives to here.
COVER_TEXT_CHARS = 6000

# Deliberately narrow: the marker word must start a line and be followed by a number,
# separated only by whitespace or a dash (ASCII, en or em, since typesetters use all
# three). Cover pages set one marker per line, where body prose glues them into
# sentences.
_COVER_ARTICLE_RE = re.compile(
    r"(?m)^\s*(?:المادة|Article|Art\.)\s*[\-–—]?\s*"
    r"(?P<num>\d+|[٠-٩]+|[۰-۹]+)"
    r"\s*(?:[\-–—:.;\s]|$)"
)

# Explicit contents-page keyword. If present anywhere in the window it is
# a strong signal that what follows is a TOC and not the body proper.
_CONTENTS_KEYWORD_RE = re.compile(
    r"\b(?:Contents|Table\s+of\s+Contents|Index)\b|المحتويات|الفهرس|جدول\s+المحتويات",
    re.IGNORECASE,
)

# A cover TOC is dense: many markers within a short span, well above the
# rate of prose-embedded references. Below this floor treat as prose.
_MIN_MARKERS_FOR_TOC = 5

# Fraction of non-empty lines in the window that must be marker lines
# before density alone counts as a TOC signal. Empirically the reviewer's
# TOC shapes sit around 0.7-0.9; ordinary body prose sits near 0.05.
_MIN_MARKER_LINE_DENSITY = 0.5

# BIDI controls that OCR of RTL PDFs leaks into the text. At the start of a line they
# stop the line-anchored cover regex firing, so they are stripped inline and the scan
# sees the shape a reader would.
_BIDI_CONTROLS = str.maketrans("", "", "‎‏‪‫‬‭‮⁦⁧⁨⁩")


def extract_cover_article_numbers(text: str) -> list[int] | None:
    """The ordered article numbers in the cover window, or ``None`` when the input is not
    plausibly TOC-shaped. The cover is the first ``COVER_TEXT_CHARS`` characters after
    BIDI strip. Fires only on at least ``_MIN_MARKERS_FOR_TOC`` line-anchored markers plus
    either a contents keyword or marker-line density above the floor; anything else is
    treated as body prose.
    """
    if not text:
        return None
    cover = text[:COVER_TEXT_CHARS].translate(_BIDI_CONTROLS)
    matches = list(_COVER_ARTICLE_RE.finditer(cover))
    if len(matches) < _MIN_MARKERS_FOR_TOC:
        return None
    # Density: fraction of non-empty lines that carry a marker.
    non_empty_lines = [ln for ln in cover.splitlines() if ln.strip()]
    if not non_empty_lines:
        return None
    marker_lines = sum(1 for ln in non_empty_lines if _COVER_ARTICLE_RE.match(ln))
    density = marker_lines / len(non_empty_lines)
    has_contents_keyword = _CONTENTS_KEYWORD_RE.search(cover) is not None
    if not has_contents_keyword and density < _MIN_MARKER_LINE_DENSITY:
        return None
    numbers: list[int] = []
    for m in matches:
        folded = normalise_digits(m.group("num")).strip()
        if folded.isdigit():
            numbers.append(int(folded))
    return numbers or None


def reconcile_cover_vs_body(
    cover_numbers: list[int] | None, body_count: int
) -> dict[str, object] | None:
    """Compare the SET of article numbers the cover declares against the body's emitted
    count, returning a validation-issue payload when they diverge by more than one.

    Set size rather than ``max``, so a legitimate gapped TOC (1, 2, 5) reports three
    declared articles rather than five. Off-by-one is clean: annexes and final articles
    routinely sit outside the TOC. Severity graduates, two being a warning and more an
    error.
    """
    if not cover_numbers:
        return None
    cover_count = len(set(cover_numbers))
    delta = body_count - cover_count
    if abs(delta) <= 1:
        return None
    severity = "error" if abs(delta) > 2 else "warning"
    return {
        "check": "cover_body_article_count_mismatch",
        "severity": severity,
        "cover_count": cover_count,
        "body_count": body_count,
        "delta": delta,
        "message": (
            f"cover TOC declares {cover_count} articles; body emits {body_count} (delta {delta:+d})"
        ),
    }
