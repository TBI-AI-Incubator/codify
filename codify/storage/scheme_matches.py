"""Scheme-match CRUD, substrate for the lens scheme-matching path."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from codify.lenses.types import SchemeMatch
from codify.storage.models import FindingRow, SchemeMatchRow


def _to_row(match: SchemeMatch, *, lens_run_id: uuid.UUID) -> SchemeMatchRow:
    return SchemeMatchRow(
        lens_run_id=lens_run_id,
        lens_name=match.lens_name,
        scheme_id=match.scheme_id,
        confidence=match.confidence,
        findings=[str(f) for f in match.findings],
        rationale=match.rationale,
    )


def _to_match(
    row: SchemeMatchRow, *, remediation_templates: Sequence[str] | None = None
) -> SchemeMatch:
    return SchemeMatch(
        lens_name=row.lens_name,
        scheme_id=row.scheme_id,
        confidence=row.confidence,
        findings=[uuid.UUID(f) for f in row.findings],
        rationale=row.rationale,
        remediation_templates=list(remediation_templates or []),
    )


async def save_scheme_matches(
    session: AsyncSession,
    matches: list[SchemeMatch],
    *,
    lens_run_id: uuid.UUID,
) -> None:
    if not matches:
        return
    session.add_all([_to_row(m, lens_run_id=lens_run_id) for m in matches])
    await session.flush()


async def list_scheme_matches_for_run(
    session: AsyncSession,
    lens_run_id: uuid.UUID,
    *,
    remediation_templates: Mapping[str, Sequence[str]] | None = None,
) -> list[SchemeMatch]:
    """Read persisted matches, optionally enriching with a caller-owned catalogue."""
    rows = list(
        (
            await session.execute(
                select(SchemeMatchRow)
                .where(col(SchemeMatchRow.lens_run_id) == lens_run_id)
                .order_by(desc(col(SchemeMatchRow.confidence)), col(SchemeMatchRow.scheme_id))
            )
        ).scalars()
    )
    templates_by_code = remediation_templates or {}
    return [
        _to_match(r, remediation_templates=templates_by_code.get(r.scheme_id, [])) for r in rows
    ]


async def list_scheme_matches_for_version(
    session: AsyncSession,
    version_id: uuid.UUID,
    lens_name: str,
    *,
    remediation_templates: Mapping[str, Sequence[str]] | None = None,
) -> list[SchemeMatch]:
    """Scheme matches from the most recent run that targeted (version_id, lens_name).

    `scheme_matches` doesn't carry version_id, so we resolve the latest
    `lens_run_id` from `findings` (which does) and fetch matches for that run.
    Returns [] when no findings exist yet.
    """
    latest_run = (
        await session.execute(
            select(col(FindingRow.lens_run_id))
            .where(col(FindingRow.version_id) == version_id)
            .where(col(FindingRow.lens_name) == lens_name)
            .order_by(desc(col(FindingRow.created_at)))
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_run is None:
        return []
    return await list_scheme_matches_for_run(
        session, latest_run, remediation_templates=remediation_templates
    )


async def delete_scheme_matches_for_run(session: AsyncSession, lens_run_id: uuid.UUID) -> int:
    result = await session.execute(
        delete(SchemeMatchRow).where(col(SchemeMatchRow.lens_run_id) == lens_run_id)
    )
    await session.flush()
    return int(cast(CursorResult[Any], result).rowcount or 0)


__all__ = [
    "delete_scheme_matches_for_run",
    "list_scheme_matches_for_run",
    "list_scheme_matches_for_version",
    "save_scheme_matches",
]
