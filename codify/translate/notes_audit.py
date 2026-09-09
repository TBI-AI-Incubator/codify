"""Deterministic checks on the notes phase, run after `build_translation_notes` and
before body-fill. Each compares the source Bluebell against the returned notes;
a finding either downgrades `notes_status` (a definitions clause with zero defined
terms, or a target-language mismatch) or stamps a warning-only flag. No LLM.
"""

from __future__ import annotations

import re
from typing import Any, Literal

_HeuristicSeverity = Literal["downgrade", "warn"]


# Definitions-clause headers per source-language family, anchored on the header
# shape so prose using "definitions" substantively does not fire. EN also covers
# Interpretation and "Article 2 - Definitions"; AR the gazette markers; HE הגדרות.
_DEFINITIONS_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "ara": [
        re.compile(r"^\s*التعريفات\b", re.MULTILINE | re.UNICODE),
        re.compile(r"^\s*معنى\s+المصطلحات", re.MULTILINE | re.UNICODE),
        re.compile(r"^\s*في\s+تطبيق\s+أحكام\s+هذا\s+القانون", re.MULTILINE | re.UNICODE),
    ],
    "heb": [re.compile(r"^\s*הגדרות\b", re.MULTILINE | re.UNICODE)],
    "eng": [
        re.compile(r"^\s*(Definitions|Interpretation)\b", re.MULTILINE | re.IGNORECASE),
    ],
}

_TERMS_OF_ART_FLOOR_CHARS = 5_000


def _norm_lang(lang: str) -> str:
    """Normalise a language name/code for equality: lowercase + strip."""
    return (lang or "").strip().lower()


def _has_definitions_clause(bluebell: str, source_language: str | None) -> bool:
    """Return True when the source shows a definitions-clause header. Uses
    the pattern family for the source language; unknown languages fall
    back to the English pattern (defensive: catches an English-language
    definitions header in an act tagged with an unknown source lang)."""
    if not bluebell:
        return False
    patterns = _DEFINITIONS_PATTERNS.get((source_language or "").lower())
    if not patterns:
        patterns = _DEFINITIONS_PATTERNS["eng"]
    return any(p.search(bluebell) for p in patterns)


def run_checks(
    bluebell: str,
    notes: dict[str, Any],
    *,
    target_language: str,
    source_language: str | None = None,
) -> tuple[list[dict[str, str]], _HeuristicSeverity | None]:
    """Run the three deterministic checks, returning `(findings, worst_severity)`:
    `"downgrade"` if any check demands one, `"warn"` if only warnings fired, `None`
    if the notes cleared. Findings are ready to append to `TranslationResult.flags`,
    each carrying `location`, `issue` and `code`.
    """
    findings: list[dict[str, str]] = []
    downgrade = False
    warned = False

    # 1. Definitions-clause heuristic.
    if _has_definitions_clause(bluebell, source_language) and not notes.get("defined_terms"):
        findings.append(
            {
                "location": "notes",
                "issue": (
                    "source appears to contain a definitions clause but "
                    "notes.defined_terms is empty: terminology will drift"
                ),
                "code": "notes_definitions_empty",
            }
        )
        downgrade = True

    # 2. Terms-of-art floor on long acts.
    if len(bluebell) >= _TERMS_OF_ART_FLOOR_CHARS and not notes.get("terms_of_art"):
        findings.append(
            {
                "location": "notes",
                "issue": (
                    f"long source ({len(bluebell):,} chars) but notes.terms_of_art "
                    "is empty; legal-register anchors may be missing"
                ),
                "code": "notes_terms_of_art_empty",
            }
        )
        warned = True

    # Notes may drop the field to empty on a degrade path, where `notes_status`
    # already carries the reason, so this fires only on a non-empty
    # `target_language` that disagrees with the request.
    notes_target = _norm_lang(notes.get("target_language") or "")
    requested = _norm_lang(target_language)
    if notes_target and requested and notes_target != requested:
        findings.append(
            {
                "location": "notes",
                "issue": (
                    f"notes.target_language ({notes_target!r}) does not match the "
                    f"requested target ({requested!r}); the notes bind the wrong "
                    "language"
                ),
                "code": "notes_language_mismatch",
            }
        )
        downgrade = True

    if downgrade:
        return findings, "downgrade"
    if warned:
        return findings, "warn"
    return findings, None


__all__ = ["run_checks"]
