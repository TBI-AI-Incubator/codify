"""Append-only decision log on findings. UPDATE blocked at DB level."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import ExaminationRow, FindingRow

ReviewStatus = Literal["open", "accepted", "dismissed", "escalated"]
Decision = Literal["accepted", "dismissed", "escalated", "reopened"]

_DECISION_TO_STATUS: dict[Decision, ReviewStatus] = {
    "accepted": "accepted",
    "dismissed": "dismissed",
    "escalated": "escalated",
    "reopened": "open",
}


async def record_decision(
    session: AsyncSession,
    *,
    finding_id: uuid.UUID,
    decision: Decision,
    actor: str,
    note: str | None = None,
    run_id: uuid.UUID | None = None,
) -> FindingRow:
    """Mutate the finding's denorm fields + append the audit row in one
    flush. Returns the updated FindingRow (session-attached, in-sync)."""
    finding = await session.get(FindingRow, finding_id)
    if finding is None:
        raise LookupError(f"finding {finding_id} not found")

    prior_state = finding.review_status
    finding.review_status = _DECISION_TO_STATUS[decision]
    finding.reviewed_by = actor
    finding.reviewed_at = datetime.now(UTC)
    finding.decision_note = note
    session.add(
        ExaminationRow(
            finding_id=finding_id,
            decision=decision,
            actor=actor,
            note=note,
            prior_state=prior_state,
            run_id=run_id,
        )
    )
    await session.flush()
    return finding


async def list_examinations(session: AsyncSession, finding_id: uuid.UUID) -> list[ExaminationRow]:
    result = await session.execute(
        select(ExaminationRow)
        .where(ExaminationRow.finding_id == finding_id)
        .order_by(ExaminationRow.at)
    )
    return list(result.scalars().all())


async def find_prior_decision(
    session: AsyncSession,
    *,
    lens_name: str,
    version_id: uuid.UUID,
    provision_eid: str,
) -> FindingRow | None:
    """Most recent terminal-state finding on the same provision; None if none."""
    result = await session.execute(
        select(FindingRow)
        .where(
            FindingRow.lens_name == lens_name,
            FindingRow.version_id == version_id,
            FindingRow.provision_eid == provision_eid,
            FindingRow.review_status != "open",
        )
        .order_by(FindingRow.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


def verdict_matches(
    left: FindingRow | None, right_payload: dict[str, object], right_severity: str
) -> bool:
    """Same severity + same payload.verdict."""
    if left is None:
        return False
    if left.severity != right_severity:
        return False
    return (left.payload or {}).get("verdict") == right_payload.get("verdict")


__all__ = [
    "Decision",
    "ReviewStatus",
    "find_prior_decision",
    "list_examinations",
    "record_decision",
    "verdict_matches",
]
