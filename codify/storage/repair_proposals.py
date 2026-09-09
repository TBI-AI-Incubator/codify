"""Repair proposals: the agent's plans as durable, decidable records.

Low/medium-risk rows record what a run applied, visible for review. High-risk
rows queue `pending`; a human decision moves them to `approved`/`rejected`, and
only `persist_approved_repair`, the sole ``reviewed_by`` write path, turns an
approved row into a persisted document change.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from codify.storage.models import RepairProposalRow, Version
from codify.storage.versions import update_repaired_akn

ProposalDecision = Literal["approved", "rejected"]


async def record_proposals(
    session: AsyncSession,
    *,
    version_id: uuid.UUID,
    run_id: uuid.UUID | None,
    proposals: list[dict[str, Any]],
    akn_sha256: str,
    source_sha256: str,
    model: str,
    prompt_version: str,
) -> None:
    """Persist a run's proposal payloads (the loop's `RepairOutcome.proposals`).

    ``akn_sha256`` pins the STORED document the rows were recorded against, an
    other-writers fence for later apply, never a validity proof. Caller commits.

    Row ids are deterministic per (run, ordinal): a durable retry that re-runs
    the insert after an earlier commit merges onto the same rows instead of
    minting duplicates a reviewer could approve twice.
    """
    for ordinal, payload in enumerate(proposals):
        row_id = (
            uuid.uuid5(uuid.NAMESPACE_OID, f"repair-proposal:{run_id}:{ordinal}")
            if run_id
            else uuid.uuid4()
        )
        await session.merge(
            RepairProposalRow(
                id=row_id,
                version_id=version_id,
                run_id=run_id,
                eid=str(payload.get("eid") or ""),
                check_name=str(payload.get("check") or ""),
                finding=dict(payload.get("finding") or {}),
                ops=list(payload.get("ops") or []),
                reasoning=str(payload.get("reasoning") or ""),
                risk_class=str(payload.get("risk") or "low"),
                status=str(payload.get("status") or "pending"),
                audit=dict(payload.get("audit") or {}),
                decision_note=str(payload.get("decision_note") or "") or None,
                akn_sha256=akn_sha256,
                source_sha256=source_sha256,
                model=model,
                prompt_version=prompt_version,
            )
        )
    await session.flush()


async def list_proposals(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[RepairProposalRow]:
    query = (
        select(RepairProposalRow)
        .where(col(RepairProposalRow.version_id) == version_id)
        .order_by(col(RepairProposalRow.created_at).desc())
        .limit(limit)
    )
    if status:
        query = query.where(col(RepairProposalRow.status) == status)
    return list((await session.execute(query)).scalars().all())


async def get_proposal(session: AsyncSession, proposal_id: uuid.UUID) -> RepairProposalRow | None:
    return await session.get(RepairProposalRow, proposal_id)


async def decide_proposal(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    *,
    decision: ProposalDecision,
    actor: str,
    note: str = "",
) -> RepairProposalRow:
    """A human decision on a pending proposal. Caller commits.

    Raises LookupError for an unknown row, ValueError when the row is not
    pending (a decided row is never re-decided; re-run repair to re-propose).
    """
    row = await session.get(RepairProposalRow, proposal_id, with_for_update=True)
    if row is None:
        raise LookupError(f"repair proposal {proposal_id} not found")
    if row.status != "pending":
        raise ValueError(f"proposal is {row.status}, not pending")
    if not actor.strip():
        raise ValueError("a decision needs a named actor")
    row.status = decision
    row.decided_by = actor
    row.decided_at = datetime.now(UTC)
    row.decision_note = note
    session.add(row)
    await session.flush()
    return row


async def mark_superseded(session: AsyncSession, proposal_id: uuid.UUID, *, note: str) -> None:
    """Caller commits. Only an undecided or approved-but-unapplied row can be
    superseded, anything else would overwrite a decision record."""
    row = await session.get(RepairProposalRow, proposal_id)
    if row is None:
        raise LookupError(f"repair proposal {proposal_id} not found")
    if row.status not in {"pending", "approved"}:
        raise ValueError(f"cannot supersede a {row.status} proposal")
    row.status = "superseded"
    row.decision_note = note
    session.add(row)
    await session.flush()


async def persist_approved_repair(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    new_akn_xml: str,
    *,
    attribution: str,
) -> RepairProposalRow:
    """The only path from an approved proposal to a persisted document change,
    and the only caller of ``update_repaired_akn(reviewed_by=...)``.

    Refuses anything but an `approved` row (an already-`applied` row returns
    as-is, so a durable retry after a committed write is a no-op, not an
    error). A drifted staleness pin supersedes the row and RETURNS it, never
    a raise across the caller's transaction, which would roll the supersede
    back and strand the proposal approved-forever. Caller commits either way;
    read `row.status` to learn which write happened.
    """
    # Both rows locked: concurrent durable retries must serialise on the
    # status check, and a concurrent writer must not slip a new akn_xml in
    # between the hash check and the update.
    row = await session.get(RepairProposalRow, proposal_id, with_for_update=True)
    if row is None:
        raise LookupError(f"repair proposal {proposal_id} not found")
    if row.status == "applied":
        return row
    if row.status != "approved":
        raise ValueError(f"proposal is {row.status}; only an approved row may persist")
    if not row.decided_by:
        raise ValueError("approved proposal carries no approver")
    version = await session.get(Version, row.version_id, with_for_update=True)
    if version is None:
        raise LookupError(f"version {row.version_id} not found")
    current_sha = hashlib.sha256(version.akn_xml.encode("utf-8")).hexdigest()
    if row.akn_sha256 and current_sha != row.akn_sha256:
        await mark_superseded(
            session,
            proposal_id,
            note="document changed since proposal; re-run repair to re-propose",
        )
        return row
    await update_repaired_akn(
        session, row.version_id, new_akn_xml, attribution, reviewed_by=row.decided_by
    )
    row.status = "applied"
    session.add(row)
    await session.flush()
    return row


__all__ = [
    "decide_proposal",
    "get_proposal",
    "list_proposals",
    "mark_superseded",
    "persist_approved_repair",
    "record_proposals",
]
