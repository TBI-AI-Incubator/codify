"""Per-version structural quality grade.

Folds the ``validate_akn`` findings for one version into a single grade a
delivery pipeline or the SPA chip can read without re-validating:

- **clean**: no finding at ``error`` or ``warning`` severity.
- **warning**: at least one ``warning`` but no ``error`` (advisory ``info``
  findings, e.g. section-numbering gaps, never move the grade off clean).
- **blocking**: at least one ``error`` finding; the earliest error names ``reason``.
- **ungraded**: the validator did not run to completion (``degraded``), so the
  absence of findings is not evidence of cleanliness.

Pure function, mirroring ``codify/translate/quality_grade.py``. The grade is a
verdict, not a gate: ingest persists it; the bulk-delivery pipeline refuses to
package a ``blocking`` version. Deliberately does not promote continuity or
number-gap findings to blocking until the OCR-concatenation corpus sweep is
complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

_GRADE_RUBRIC_VERSION = 1

# validate_akn severities that move the grade. "info" is advisory only.
_BLOCKING_SEVERITY = "error"
_WARNING_SEVERITY = "warning"

StructuralGrade = Literal["clean", "warning", "blocking", "ungraded"]


@dataclass(frozen=True)
class StructuralGradeResult:
    grade: StructuralGrade
    reason: str
    rubric_version: int = _GRADE_RUBRIC_VERSION


def _describe(finding: dict[str, Any]) -> str:
    """A short reason string from a finding's check + message."""
    check = str(finding.get("check") or "check")
    message = str(finding.get("message") or "").strip()
    return f"{check}: {message}" if message else check


def structural_quality_grade(
    findings: list[dict[str, Any]] | None, *, degraded: bool = False
) -> StructuralGradeResult:
    """Grade a version from its ``validate_akn`` findings.

    ``degraded`` is set by the caller when the validator raised part-way; a
    crash must never read as clean, so it downgrades to ``ungraded``.
    """
    if degraded:
        return StructuralGradeResult(grade="ungraded", reason="validator did not complete")

    findings = findings or []

    # Earliest error wins for reason (matches the translation-grade rubric).
    for finding in findings:
        if finding.get("severity") == _BLOCKING_SEVERITY:
            return StructuralGradeResult(grade="blocking", reason=_describe(finding))

    for finding in findings:
        if finding.get("severity") == _WARNING_SEVERITY:
            return StructuralGradeResult(grade="warning", reason=_describe(finding))

    return StructuralGradeResult(grade="clean", reason="")


__all__ = [
    "StructuralGrade",
    "StructuralGradeResult",
    "structural_quality_grade",
]
