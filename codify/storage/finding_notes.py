"""Concordance Notes, read/write helpers for `finding_notes` (migration 0046)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import FindingNoteRow, FindingRow


async def get_notes_for_findings(
    session: AsyncSession,
    finding_ids: list[uuid.UUID],
    prompt_version: str,
) -> dict[uuid.UUID, str]:
    """Batch-read; missing keys absent from the result."""
    if not finding_ids:
        return {}
    stmt = select(FindingNoteRow.finding_id, FindingNoteRow.body).where(
        FindingNoteRow.finding_id.in_(finding_ids),
        FindingNoteRow.prompt_version == prompt_version,
    )
    result = await session.execute(stmt)
    return {row.finding_id: row.body for row in result}


async def get_note(
    session: AsyncSession,
    finding_id: uuid.UUID,
    prompt_version: str,
) -> str | None:
    stmt = select(FindingNoteRow.body).where(
        FindingNoteRow.finding_id == finding_id,
        FindingNoteRow.prompt_version == prompt_version,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_note(
    session: AsyncSession,
    finding_id: uuid.UUID,
    prompt_version: str,
    body: str,
) -> None:
    """Idempotent; caller commits."""
    stmt = pg_insert(FindingNoteRow).values(
        finding_id=finding_id,
        prompt_version=prompt_version,
        body=body,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["finding_id", "prompt_version"],
        set_={"body": stmt.excluded.body, "generated_at": stmt.excluded.generated_at},
    )
    await session.execute(stmt)


async def delete_notes_for_version(
    session: AsyncSession,
    version_id: uuid.UUID,
) -> int:
    """Purge notes for a version's findings."""
    finding_ids_subq = select(FindingRow.id).where(FindingRow.version_id == version_id)
    stmt = delete(FindingNoteRow).where(FindingNoteRow.finding_id.in_(finding_ids_subq))
    result = await session.execute(stmt)
    return result.rowcount or 0


_STALE_LENS_RUNS_SQL = text(
    """
    SELECT DISTINCT lr.id
    FROM lens_runs lr
    JOIN findings f ON f.lens_run_id = lr.id
    LEFT JOIN finding_notes fn
      ON fn.finding_id = f.id AND fn.prompt_version = :current
    WHERE lr.lens_name = :lens_name
      AND lr.status = 'succeeded'
      AND fn.finding_id IS NULL
    """
)


async def stale_lens_run_ids_for_lens(
    session: AsyncSession,
    *,
    lens_name: str,
    current_prompt_version: str,
) -> list[uuid.UUID]:
    """lens_runs with any finding lacking a note at current_prompt_version.

    LEFT JOIN + IS NULL catches both a) findings whose note is at a prior
    version and b) findings that never had a note (pre-notes-feature runs).
    Short-circuit rows persist an empty body at current version, so they
    don't count as stale."""
    rows = await session.execute(
        _STALE_LENS_RUNS_SQL,
        {"lens_name": lens_name, "current": current_prompt_version},
    )
    return [r[0] for r in rows]
