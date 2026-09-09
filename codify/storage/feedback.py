"""Feedback CRUD, provision-grounded user feedback on AI outputs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage._pagination import paginate_by_created_id
from codify.storage.models import FeedbackRow

FeedbackKind = Literal[
    "thumbs",
    "score",
    "note",
    "alignment_correction",
    "suggestion_accepted",
    "suggestion_rejected",
    "suggestion_edited",
]


class Feedback(BaseModel):
    """One user's judgement on an AI output, attached to a provision.

    `value` is shaped by `kind`:
      * thumbs, `{"up": bool}`
      * score, `{"value": int}` (1-5)
      * note, `{}` (the substance is in `comment`)
      * alignment_correction, `{"verdict": "aligned" | "partial" | "gap"}`
    """

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    provision_id: uuid.UUID
    version_id: uuid.UUID
    assessment_id: uuid.UUID | None = None
    finding_id: uuid.UUID | None = None
    langfuse_trace_id: str | None = None
    kind: FeedbackKind
    value: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = None
    user_id: str
    org: str | None = None
    jurisdiction: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeedbackConflict(Exception):
    """Raised when the (user, kind, provision, finding) unique constraint trips."""


def _to_row(f: Feedback) -> FeedbackRow:
    return FeedbackRow(
        id=f.id,
        provision_id=f.provision_id,
        version_id=f.version_id,
        assessment_id=f.assessment_id,
        finding_id=f.finding_id,
        langfuse_trace_id=f.langfuse_trace_id,
        kind=f.kind,
        value=f.value,
        comment=f.comment,
        user_id=f.user_id,
        org=f.org,
        jurisdiction=f.jurisdiction,
        created_at=f.created_at,
    )


def _to_feedback(row: FeedbackRow) -> Feedback:
    return Feedback(
        id=row.id,
        provision_id=row.provision_id,
        version_id=row.version_id,
        assessment_id=row.assessment_id,
        finding_id=row.finding_id,
        langfuse_trace_id=row.langfuse_trace_id,
        kind=cast(FeedbackKind, row.kind),
        value=row.value,
        comment=row.comment,
        user_id=row.user_id,
        org=row.org,
        jurisdiction=row.jurisdiction,
        created_at=row.created_at,
    )


async def save_feedback(session: AsyncSession, feedback: Feedback) -> Feedback:
    """Raises FeedbackConflict on the user_id/kind/provision/finding unique violation.

    The violation is absorbed in a savepoint. A bare rollback took the caller's whole
    transaction: the router applies an accepted suggestion on this session first, so a
    duplicate row discarded the new Version and the finding's acceptance stamp, and the
    client got a 409 having lost the amendment it had just made.
    """
    try:
        async with session.begin_nested():
            session.add(_to_row(feedback))
            await session.flush()
    except IntegrityError as exc:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate == "23505":
            raise FeedbackConflict(
                f"feedback already exists for user={feedback.user_id} "
                f"kind={feedback.kind} provision={feedback.provision_id} "
                f"finding={feedback.finding_id}"
            ) from exc
        raise
    return feedback


async def list_feedback_for_provision(
    session: AsyncSession,
    provision_id: uuid.UUID,
    *,
    limit: int = 100,
    cursor: uuid.UUID | None = None,
) -> tuple[list[Feedback], uuid.UUID | None]:
    rows, next_cursor = await paginate_by_created_id(
        session,
        FeedbackRow,
        where=[FeedbackRow.provision_id == provision_id],
        limit=limit,
        cursor=cursor,
    )
    return [_to_feedback(r) for r in rows], next_cursor


async def list_feedback_for_finding(session: AsyncSession, finding_id: uuid.UUID) -> list[Feedback]:
    """Newest first. Bounded, a finding rarely accumulates more than a
    handful of judgements, so no pagination."""
    rows = (
        await session.execute(
            select(FeedbackRow)
            .where(FeedbackRow.finding_id == finding_id)
            .order_by(desc(FeedbackRow.created_at))
            .limit(200)
        )
    ).scalars()
    return [_to_feedback(r) for r in rows]


__all__ = [
    "Feedback",
    "FeedbackConflict",
    "FeedbackKind",
    "list_feedback_for_finding",
    "list_feedback_for_provision",
    "save_feedback",
]
