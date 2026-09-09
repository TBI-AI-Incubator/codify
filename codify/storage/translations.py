"""Translation-run lifecycle CRUD.

A translation run renders a source `Version` into a target language.
Rows are inserted `status='running'` and flipped to `succeeded` /
`failed` at the end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import TranslationRun


async def get_translation(session: AsyncSession, id_: uuid.UUID) -> TranslationRun | None:
    result = await session.execute(select(TranslationRun).where(TranslationRun.id == id_))
    return result.scalar_one_or_none()


async def finalise_translation(
    session: AsyncSession,
    *,
    id_: uuid.UUID,
    target_version_id: uuid.UUID,
    notes: dict[str, Any],
    audit: dict[str, Any],
) -> TranslationRun | None:
    """Flip ``status='succeeded'`` and link the persisted target Version."""
    row = await get_translation(session, id_)
    if row is None:
        return None
    row.target_version_id = target_version_id
    row.notes = notes
    row.audit = audit
    row.status = "succeeded"
    row.completed_at = datetime.now(UTC)
    row.progress_pct = 100
    await session.flush()
    return row


async def fail_translation(session: AsyncSession, *, id_: uuid.UUID, error: str) -> None:
    """Mark a running translation as failed and persist the error string."""
    row = await get_translation(session, id_)
    if row is None:
        return
    row.status = "failed"
    row.completed_at = datetime.now(UTC)
    row.error = error[:2000]
    await session.flush()


async def list_translations_for_version(
    session: AsyncSession, source_version_id: uuid.UUID
) -> list[TranslationRun]:
    """Every translation run started from a given source version."""
    result = await session.execute(
        select(TranslationRun)
        .where(TranslationRun.source_version_id == source_version_id)
        .order_by(TranslationRun.created_at.desc())
    )
    return list(result.scalars().all())


async def get_translation_for_target_version(
    session: AsyncSession, target_version_id: uuid.UUID
) -> TranslationRun | None:
    """The run whose text the target version actually holds.

    Newest-first alone is wrong: a non-force retranslation links its run to the
    existing target even when the output is discarded in favour of the row
    already there, so the newest run can describe text that was never stored.
    Runs known to have been discarded are skipped. Rows written before the
    marker existed carry no claim either way and are still eligible, so history
    keeps answering.
    """
    result = await session.execute(
        select(TranslationRun)
        .where(
            TranslationRun.target_version_id == target_version_id,
            text("coalesce(translation_runs.audit->>'akn_landed', 'true') <> 'false'"),
        )
        .order_by(TranslationRun.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


__all__ = [
    "fail_translation",
    "finalise_translation",
    "get_translation",
    "get_translation_for_target_version",
    "list_translations_for_version",
]
