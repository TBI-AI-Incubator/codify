"""Deterministic clause-count parity. Phases 1-3 catch numeric drop, terminology loss
and register drift, but a quietly dropped subclause has none: `content_coverage`
sits at 0.96 and nothing fires. `check_clause_parity` segments on sentence-ending
punctuation and flags `translate_clause_gap`, per provision from `translate_document`.
"""

from __future__ import annotations

import re

# Threshold: target clauses / source clauses must reach this ratio.
# Below it, a subclause was likely dropped; above it, a legitimate
# restructure (merge or split) is treated as clean.
CLAUSE_PARITY_THRESHOLD = 0.80
# Minimum absolute clause deficit before flagging. Guards against
# false-firing on legitimate 2-into-1 sentence merges (very common when
# translating source-language run-ons into concise legal prose).
CLAUSE_PARITY_MIN_DEFICIT = 2

# Legal abbreviations carrying a full stop, which would otherwise split a clause.
# Masked only where the target family uses them; Arabic and Hebrew prose does not
# carry these Latin forms, so the mask is a no-op there.
_LEGAL_ABBREV_RE_EN = re.compile(
    r"\b(e\.g|i\.e|cf|viz|no|vol|ch|art|sec|para|subsec|paras|arts|secs|Nos|Vol|Ch)\.",
    re.IGNORECASE,
)

# Sentence-ending punctuation per language family. Arabic has its own full stop,
# question and semicolon, so the ASCII equivalents stay out of the class: a raw
# Latin dot in Arabic prose is usually a citation, not a sentence break.
_SENTENCE_END: dict[str, re.Pattern[str]] = {
    "eng": re.compile(r"[.?!;]\s+"),
    "en": re.compile(r"[.?!;]\s+"),
    "ara": re.compile(r"[؟!؛]\s+", re.UNICODE),
    "ar": re.compile(r"[؟!؛]\s+", re.UNICODE),
    "heb": re.compile(r"[.?!;]\s+", re.UNICODE),
    "he": re.compile(r"[.?!;]\s+", re.UNICODE),
}


def _mask_legal_abbrev(text: str, language: str | None) -> str:
    lang = (language or "eng").lower()
    if lang in {"eng", "en", "heb", "he"}:
        return _LEGAL_ABBREV_RE_EN.sub(lambda m: m.group(1), text)
    return text


def count_clauses(text: str, language: str | None = None) -> int:
    """Count sentence-terminated clauses. Falls back to English
    segmentation for unregistered languages so the ratio still means
    something for less-covered targets."""
    if not text or not text.strip():
        return 0
    lang = (language or "eng").lower()
    splitter = _SENTENCE_END.get(lang, _SENTENCE_END["eng"])
    masked = _mask_legal_abbrev(text, lang)
    parts = splitter.split(masked)
    # A trailing empty string appears where the text ends in terminal punctuation
    # plus whitespace. Single-word remainders, usually "and" or a bare number,
    # are structural residue rather than a clause.
    clauses = [p.strip() for p in parts if p.strip() and len(p.strip().split()) > 1]
    return max(len(clauses), 1)


def check_clause_parity(
    source: str, target: str, *, language: str | None = None
) -> tuple[float, bool]:
    """Return `(ratio, gap)`, `target_clauses` over `source_clauses`, 1.0 when source is
    empty. `gap` needs both the ratio below threshold and at least two source clauses
    missing: the absolute floor separates a legitimate 2-into-1 merge (one missing,
    ratio 0.5, no gap) from a real subclause drop.
    """
    src_count = count_clauses(source, language)
    tgt_count = count_clauses(target, language)
    if src_count == 0:
        return 1.0, False
    ratio = tgt_count / src_count
    deficit = src_count - tgt_count
    gap = ratio < CLAUSE_PARITY_THRESHOLD and deficit >= CLAUSE_PARITY_MIN_DEFICIT
    return ratio, gap


__all__ = [
    "CLAUSE_PARITY_MIN_DEFICIT",
    "CLAUSE_PARITY_THRESHOLD",
    "check_clause_parity",
    "count_clauses",
]
