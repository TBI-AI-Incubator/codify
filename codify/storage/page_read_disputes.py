"""Append-only reviewer disputes on page reads. UPDATE blocked at DB level.

A dispute labels one region of one read attempt (`page_reads.id`). It is the OCR
trace's only write sink: never a path back to the corpus, which stays the repair
loop. Each row is a gold-set example for the Arabic OCR set.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import PageRead, PageReadDisputeRow

logger = structlog.get_logger()

DisputeVerdict = Literal["confirmed", "disputed", "corrected"]


def _resolve_block(read: PageRead, block_index: int) -> dict[str, Any] | None:
    """The layout block a dispute points at, or None when the index no longer
    lands in the read's layout. One resolver for both the write-time bounds check
    and the export, so the bounds logic is not re-derived per call site."""
    blocks = (read.layout or {}).get("blocks") or [] if isinstance(read.layout, dict) else []
    return blocks[block_index] if 0 <= block_index < len(blocks) else None


async def record_dispute(
    session: AsyncSession,
    *,
    page_read_id: uuid.UUID,
    verdict: DisputeVerdict,
    actor: str,
    block_index: int | None = None,
    corrected_text: str | None = None,
    note: str | None = None,
) -> PageReadDisputeRow:
    """Append one dispute against a read attempt. Caller commits.

    Raises LookupError if the read attempt does not exist (so the FK never bites
    at flush and the caller can 404), or ValueError if block_index is set but does
    not land in the read's layout (so a region-less label never enters the log)."""
    read = await session.get(PageRead, page_read_id)
    if read is None:
        raise LookupError(f"page read {page_read_id} not found")
    if block_index is not None and _resolve_block(read, block_index) is None:
        raise ValueError(f"block_index {block_index} does not resolve in page read {page_read_id}")
    row = PageReadDisputeRow(
        page_read_id=page_read_id,
        verdict=verdict,
        actor=actor,
        block_index=block_index,
        corrected_text=corrected_text,
        note=note,
    )
    session.add(row)
    await session.flush()
    return row


async def list_disputes(session: AsyncSession, page_read_id: uuid.UUID) -> list[PageReadDisputeRow]:
    """Every dispute against a read attempt, oldest first: the decision log."""
    result = await session.execute(
        select(PageReadDisputeRow)
        .where(PageReadDisputeRow.page_read_id == page_read_id)
        .order_by(PageReadDisputeRow.created_at)
    )
    return list(result.scalars().all())


async def export_disputes(session: AsyncSession) -> list[dict[str, Any]]:
    """Every dispute as a gold-set row: the label plus the read it labels. One
    disputed region per row, the accretion shape the Arabic OCR set feeds on."""
    rows = await session.execute(
        select(PageReadDisputeRow, PageRead)
        .join(PageRead, PageReadDisputeRow.page_read_id == PageRead.id)
        .order_by(PageReadDisputeRow.created_at)
    )
    out: list[dict[str, Any]] = []
    for dispute, read in rows.all():
        block = None
        if dispute.block_index is not None:
            block = _resolve_block(read, dispute.block_index)
            if block is None:
                # Written valid, so the layout was mutated in place afterwards. A
                # detached label is visible to whoever curates the set, not silent.
                logger.warning(
                    "gold_export_block_unresolved",
                    page_read_id=str(dispute.page_read_id),
                    block_index=dispute.block_index,
                )
        out.append(
            {
                "page_read_id": str(dispute.page_read_id),
                "block_index": dispute.block_index,
                "block": block,
                "verdict": dispute.verdict,
                "corrected_text": dispute.corrected_text,
                "note": dispute.note,
                "engine": read.engine,
                "model": read.model,
                "read_text": read.text,
                "rival_text": read.rival_text,
                "divergence": read.divergence,
                "actor": dispute.actor,
                "created_at": dispute.created_at.isoformat(),
            }
        )
    return out
