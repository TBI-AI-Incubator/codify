"""Composite per-version translation grade over the Phase 1-4 signals. **A** no
predicate fires; **B** a soft-warn only; **C** any hard-fail, earliest match
winning `reason`; **ungraded** for an empty audit dict. Thresholds are named at
module level and `_GRADE_RUBRIC_VERSION` rides the output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

_GRADE_RUBRIC_VERSION = 1

# Numeric-slot recall floor: a single dropped fine amount, date, or
# article reference sits below this threshold.
_RECALL_FLOOR = 0.99
# Body-fill fallback ratio ceiling: above this, more than one in twenty
# provisions was left as untranslated source.
_FALLBACK_RATIO_CEILING = 0.05
# Judge failure ratio floor: above this the adequacy signal is thin.
_JUDGE_FAIL_RATIO_CEILING = 1.0 / 3.0

QualityGrade = Literal["A", "B", "C", "ungraded"]


@dataclass(frozen=True)
class QualityGradeResult:
    grade: QualityGrade
    reason: str
    rubric_version: int = _GRADE_RUBRIC_VERSION


def _fallback_ratio(audit: dict[str, Any]) -> float:
    fallback = int(audit.get("bodies_fallback_to_source") or 0)
    units = int(audit.get("units_total") or 0)
    if units <= 0:
        # No unit count on the audit: fall back to no signal so the check
        # cannot false-fire on a legitimately-empty document.
        return 0.0
    return fallback / units


def _judge_fail_ratio(audit: dict[str, Any]) -> float:
    """Phase 4 producer records ``judge_sample_size`` as the count of
    provisions actually judged (verdict returned), and ``judge_failed_count``
    as the count where the LLM call did not return. The total attempted is
    the sum; the failure ratio is failed / total_attempted."""
    sample = int(audit.get("judge_sample_size") or 0)
    failed = int(audit.get("judge_failed_count") or 0)
    total = sample + failed
    if total <= 0:
        return 0.0
    return failed / total


def _any_judge_fail_verdict(audit: dict[str, Any]) -> bool:
    verdicts = audit.get("judge_verdicts") or {}
    for v in verdicts.values():
        if isinstance(v, dict) and v.get("overall") == "fail":
            return True
    return False


def _any_judge_warn_verdict(audit: dict[str, Any]) -> bool:
    verdicts = audit.get("judge_verdicts") or {}
    for v in verdicts.values():
        if isinstance(v, dict) and v.get("overall") == "warn":
            return True
    return False


def _has_flag_code(flags: list[dict[str, str]], code_prefix: str) -> bool:
    return any(f.get("code", "").startswith(code_prefix) for f in flags)


# Keys the grade reads. The front-matter counters are deliberately absent: they
# gate delivery rather than feed the grade, the persist gate names their absence
# itself, and requiring them would regrade every earlier artifact as ungraded.
_EXPECTED_SIGNAL_KEYS = (
    "numeric_slot_recall",
    "notes_status",
    # Denominator for the fallback-ratio hard fail, drift-guarded so a producer
    # that stops writing it downgrades to ungraded rather than silently zeroing
    # the ratio. Pre-fix artifacts never wrote it and regrade as ungraded.
    "units_total",
)


def translation_quality_grade(
    audit: dict[str, Any] | None, flags: list[dict[str, str]] | None = None
) -> QualityGradeResult:
    """Compose the per-version grade from the translate_document audit
    dict and its flags list. Returns ``QualityGradeResult`` carrying
    ``grade``, ``reason`` (empty for A), and the rubric version."""
    if not audit:
        return QualityGradeResult(grade="ungraded", reason="audit missing")

    # A refactor renaming or dropping one of these keys would otherwise upgrade a
    # defective run to A, every `dict.get` default being clean. Downgrade to
    # ungraded so an operator sees the drift.
    missing = [k for k in _EXPECTED_SIGNAL_KEYS if k not in audit]
    if missing:
        return QualityGradeResult(
            grade="ungraded",
            reason=f"audit shape drift: missing keys {missing}",
        )

    flags = flags or []

    # ── Hard-fail predicates (earliest match wins for reason) ──────────

    fallback_ratio = _fallback_ratio(audit)
    if fallback_ratio > _FALLBACK_RATIO_CEILING:
        return QualityGradeResult(
            grade="C",
            reason=(
                f"bodies_fallback_to_source ratio {fallback_ratio:.1%} exceeds "
                f"{_FALLBACK_RATIO_CEILING:.0%} ceiling"
            ),
        )

    if audit.get("notes_status") == "failed":
        return QualityGradeResult(
            grade="C", reason="notes phase failed; terminology binder never landed"
        )

    body_alien = int(audit.get("body_alien_script_hits") or 0)
    if body_alien > 0:
        # `<p>` under `<body>` dominated by source script means whole untranslated
        # paragraphs. The `normalise_line_count` back-fill and the per-batch
        # fallback both shipped grade A before anything counted the residue.
        return QualityGradeResult(
            grade="C",
            reason=(
                f"{body_alien} body <p>(s) dominated by source-script text; "
                "untranslated source prose in the target body"
            ),
        )

    heading_alien = int(audit.get("heading_alien_script_hits") or 0)
    if heading_alien > 0:
        return QualityGradeResult(
            grade="C",
            reason=(
                f"{heading_alien} <heading>(s) dominated by source-script text; "
                "untranslated source prose in the target headings"
            ),
        )

    stray_by_eid = audit.get("stray_sentinels_by_eid") or {}
    if stray_by_eid:
        eids = sorted(stray_by_eid)[:3]
        return QualityGradeResult(
            grade="C",
            reason=f"undecoded numeric-slot sentinel(s) in target under {', '.join(eids)}",
        )

    money_missing = audit.get("money_missing_by_eid") or {}
    if money_missing:
        eids = sorted(money_missing)[:3]
        return QualityGradeResult(
            grade="C",
            reason=f"monetary amount(s) dropped from target under {', '.join(eids)}",
        )

    money_duplicated = audit.get("money_duplicated_by_eid") or {}
    if money_duplicated:
        eids = sorted(money_duplicated)[:3]
        return QualityGradeResult(
            grade="C",
            reason=f"monetary amount(s) double-emitted in target under {', '.join(eids)}",
        )

    recall = float(audit.get("numeric_slot_recall") or 1.0)
    if recall < _RECALL_FLOOR:
        return QualityGradeResult(
            grade="C",
            reason=(
                f"numeric_slot_recall {recall:.1%} below {_RECALL_FLOOR:.0%} floor; "
                "a numeric token was lost after repair"
            ),
        )

    if _any_judge_fail_verdict(audit):
        return QualityGradeResult(
            grade="C", reason="judge verdict overall=fail on a sampled provision"
        )

    if _judge_fail_ratio(audit) > _JUDGE_FAIL_RATIO_CEILING:
        return QualityGradeResult(
            grade="C", reason="judge LLM failed on more than one third of the sample"
        )

    # ── Soft-warn predicates (any single hit collapses to B) ───────────

    if audit.get("register_drifted"):
        return QualityGradeResult(grade="B", reason="deontic register drifted")

    if audit.get("notes_status") == "partial":
        return QualityGradeResult(grade="B", reason="notes phase partial")

    if audit.get("clause_gaps_by_eid"):
        return QualityGradeResult(grade="B", reason="clause parity gap on at least one provision")

    if audit.get("header_bleed_hits"):
        return QualityGradeResult(grade="B", reason="OCR header bleed still present in target")

    if _any_judge_warn_verdict(audit):
        return QualityGradeResult(
            grade="B", reason="judge verdict overall=warn on a sampled provision"
        )

    if audit.get("exemplar_pool_active") is False and audit.get("judge_enabled"):
        # Only forces B when the source language had no registered
        # deontic pattern AND the operator opted into the judge phase;
        # a plain non-judge run does not read as a defect.
        return QualityGradeResult(
            grade="B", reason="exemplar pool inactive for source language; register unlocked"
        )

    if _has_flag_code(flags, "translate_clause_gap") or _has_flag_code(flags, "judge_"):
        return QualityGradeResult(
            grade="B", reason="soft-warn flag present without matching audit key"
        )

    return QualityGradeResult(grade="A", reason="")


def stamp_grade_on_audit(audit: dict[str, Any], grade_result: QualityGradeResult) -> dict[str, Any]:
    """In-place stamp the grade fields onto an audit dict. Live persist
    and the backfill workflow both call this so the two paths cannot
    drift on which keys land."""
    audit["quality_grade"] = grade_result.grade
    audit["quality_grade_reason"] = grade_result.reason
    audit["quality_grade_rubric_version"] = grade_result.rubric_version
    return audit


__all__ = [
    "QualityGrade",
    "QualityGradeResult",
    "stamp_grade_on_audit",
    "translation_quality_grade",
]
