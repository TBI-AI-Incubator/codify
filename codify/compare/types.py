"""Pydantic models for compliance comparison results."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeAliasType

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["aligned", "partial", "gap"]

# Non-obligation kinds are not transposition targets.
ProvisionKind = Literal[
    "obligation",
    "definition",
    "recital",
    "eu_internal",
    "structural",
    "member_state_option",
    "eu_infrastructure",
]

# EC legal-alignment scale, including ``inconclusive`` for insufficient evidence.
AlignmentLevel = TypeAliasType(
    "AlignmentLevel",
    Literal[
        "inconclusive",
        "not_aligned",
        "partially_aligned",
        "largely_aligned",
        "fully_aligned",
    ],
)

# Concordance-table method: mandatory, optional, discretionary or not applicable.
ClauseMethod = Literal["normal", "optional", "discretionary", "na"]


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frbr_uri: str
    akn_eid: str
    quote: str


class RejectedCandidate(BaseModel):
    """Persisted retrieval trace; populated only when verdict=gap."""

    model_config = ConfigDict(extra="forbid")

    akn_eid: str
    # Cosine similarity on unit-normalised vectors; aligner emits in [-1, 1].
    similarity: float = Field(ge=-1.0, le=1.0)
    reason: str = Field(max_length=400)


class ProvisionAlignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directive_eid: str
    directive_heading: str | None
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    note: str
    citations: list[Citation] = Field(default_factory=list)
    needs_review: bool
    actionable: bool = True
    provision_kind: ProvisionKind = "obligation"
    clause_method: ClauseMethod = "normal"
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    langfuse_trace_id: str | None = None
    trace_url: str | None = None


class AlignmentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aligned: int
    partial: int
    gap: int
    total: int
    aligned_pct: float
    partial_pct: float
    gap_pct: float
    needs_review: int
    # aligned/partial/gap + pcts are over `total` (actionable only); na is separate.
    na: int = 0
    key_gaps: list[str] = Field(default_factory=list)


class ComparisonReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directive_frbr_uri: str
    domestic_frbr_uri: str
    generated_at: datetime
    seed: int | None
    confidence_threshold: float
    results: list[ProvisionAlignment]
    summary: AlignmentSummary


class LLMCitation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    akn_eid: str
    quote: str


class LLMRejectedCandidate(BaseModel):
    """LLM-supplied rejection trace; populated when verdict='gap'."""

    model_config = ConfigDict(extra="ignore")

    akn_eid: str
    # Truncate server-side before persistence; valid verbose responses are allowed.
    reason: str


class LLMVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    note: str = Field(max_length=1500)
    citations: list[LLMCitation] = Field(default_factory=list)
    actionable: bool = True
    provision_kind: ProvisionKind = "obligation"
    clause_method: ClauseMethod = "normal"
    rejected_candidates: list[LLMRejectedCandidate] = Field(default_factory=list)


__all__ = [
    "AlignmentLevel",
    "AlignmentSummary",
    "Citation",
    "ClauseMethod",
    "ComparisonReport",
    "LLMCitation",
    "LLMRejectedCandidate",
    "LLMVerdict",
    "ProvisionAlignment",
    "ProvisionKind",
    "RejectedCandidate",
    "Verdict",
]
