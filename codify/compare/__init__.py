"""Compliance comparator, directive vs domestic, provision-level alignment."""

from codify.compare.comparator import compare, warm_comparator_cache
from codify.compare.scaffold import classify_alignment_level
from codify.compare.types import (
    AlignmentLevel,
    AlignmentSummary,
    Citation,
    ComparisonReport,
    ProvisionAlignment,
    Verdict,
)
from codify.compare.validation import ComparatorValidationError

__all__ = [
    "AlignmentLevel",
    "AlignmentSummary",
    "Citation",
    "ComparatorValidationError",
    "ComparisonReport",
    "ProvisionAlignment",
    "Verdict",
    "classify_alignment_level",
    "compare",
    "warm_comparator_cache",
]
