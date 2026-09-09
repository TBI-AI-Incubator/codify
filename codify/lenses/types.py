"""Lens contract, Protocol + Finding + SchemeMatch."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from codify.core.llm import LLMClient
    from codify.embed.client import EmbeddingClient

Severity = Literal["low", "medium", "high", "critical"]

# Dimensions a caller may aggregate findings by (shared by the storage GROUP BY
# and the API endpoint so the vocabulary stays in one place).
FindingGroupBy = Literal["severity", "factor_code", "risk_class", "section"]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    lens_run_id: UUID
    lens_name: str
    version_id: UUID
    provision_id: UUID | None = None
    provision_eid: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    recommendation: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    langfuse_trace_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SchemeMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lens_name: str
    scheme_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    findings: list[UUID]
    rationale: str
    # Optional plugin-defined remediation identifiers.
    remediation_templates: list[str] = Field(default_factory=list)


class Lens(Protocol):
    name: str
    taxonomy_models: list[type[BaseModel]]

    def scan(
        self,
        version_id: UUID,
        *,
        llm: LLMClient,
        embedding_client: EmbeddingClient,
        session_factory: async_sessionmaker[AsyncSession],
        lens_run_id: UUID,
        **params: Any,
    ) -> AsyncIterator[Finding]: ...

    async def match_schemes(
        self,
        findings: list[Finding],
        *,
        session_factory: async_sessionmaker[AsyncSession],
        lens_run_id: UUID,
        **params: Any,
    ) -> list[SchemeMatch]: ...


__all__ = ["Finding", "FindingGroupBy", "Lens", "SchemeMatch", "Severity"]
