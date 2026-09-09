"""`lens_runs` CRUD helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import LensRunRow


async def create_lens_run(
    session: AsyncSession,
    *,
    lens_run_id: uuid.UUID,
    lens_name: str,
    version_id: uuid.UUID,
    prompt_version: str | None,
) -> LensRunRow:
    """Open a run row at scan start. Caller commits."""
    row = LensRunRow(
        id=lens_run_id,
        lens_name=lens_name,
        version_id=version_id,
        prompt_version=prompt_version,
        started_at=datetime.now(UTC),
    )
    session.add(row)
    return row


async def set_lens_run_warnings(
    session: AsyncSession,
    *,
    lens_run_id: uuid.UUID,
    warnings: str,
) -> None:
    """Stamp a non-fatal operator-facing message on a lens_run. Status unchanged."""
    await session.execute(
        update(LensRunRow).where(LensRunRow.id == lens_run_id).values(warnings=warnings)
    )


async def complete_lens_run(
    session: AsyncSession,
    *,
    lens_run_id: uuid.UUID,
    summary_text: str | None,
    finding_count: int,
    corruption_count: int,
    drafting_style_count: int,
    scheme_match_count: int,
    status: str = "succeeded",
) -> None:
    await session.execute(
        update(LensRunRow)
        .where(LensRunRow.id == lens_run_id)
        .values(
            completed_at=datetime.now(UTC),
            summary_text=summary_text,
            finding_count=finding_count,
            corruption_count=corruption_count,
            drafting_style_count=drafting_style_count,
            scheme_match_count=scheme_match_count,
            status=status,
        )
    )


async def get_lens_run(session: AsyncSession, lens_run_id: uuid.UUID) -> LensRunRow | None:
    return (
        await session.execute(select(LensRunRow).where(LensRunRow.id == lens_run_id))
    ).scalar_one_or_none()


async def get_latest_lens_run(
    session: AsyncSession,
    *,
    version_id: uuid.UUID,
    lens_name: str,
    succeeded_only: bool = False,
) -> LensRunRow | None:
    """Most recent run for a (version, lens) pair. Backs the SPA masthead.

    `succeeded_only=True` skips cancelled/failed rows, the findings endpoint
    uses it so a cancelled re-run can't scope the table to an empty result and
    surface "no comparison" against a law that does have prior findings."""
    stmt = (
        select(LensRunRow)
        .where(LensRunRow.version_id == version_id)
        .where(LensRunRow.lens_name == lens_name)
        .order_by(LensRunRow.started_at.desc(), LensRunRow.id.desc())
        .limit(1)
    )
    if succeeded_only:
        stmt = stmt.where(LensRunRow.status == "succeeded")
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_lens_runs(
    session: AsyncSession,
    *,
    version_id: uuid.UUID,
    lens_name: str,
    limit: int = 20,
) -> list[LensRunRow]:
    """All runs for a (version, lens), newest first. Ordered like the resolvers
    (started_at desc, then id) so the first row is the run `get_latest_lens_run`
    would pick, and a caller can pin any id it lists."""
    return list(
        (
            await session.execute(
                select(LensRunRow)
                .where(LensRunRow.version_id == version_id)
                .where(LensRunRow.lens_name == lens_name)
                .order_by(LensRunRow.started_at.desc(), LensRunRow.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def list_lens_runs_with_law_rollup(
    session: AsyncSession,
    *,
    lens_name: str,
    jurisdictions: tuple[str, ...] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """All runs for ``lens_name`` joined to their law + jurisdiction, with a
    severity breakdown computed on the fly (FILTER aggregation in one query).
    Newest first. Returns raw mapping rows for the router to project.

    ``jurisdictions`` follows the convention `effective_jurisdictions` sets:
    None is unbounded, an empty tuple grants nothing. The caller passes its
    actor's grants, so omitting a filter cannot mean every tenant.
    """
    sql = """
        SELECT
            lr.id AS run_id,
            lr.lens_name,
            lr.version_id,
            lr.started_at,
            lr.completed_at,
            lr.status,
            lr.finding_count,
            lr.scheme_match_count,
            v.expression_date,
            v.language,
            l.id AS law_id,
            l.number AS law_number,
            l.title AS law_title,
            j.code AS jurisdiction_code,
            COUNT(f.id) FILTER (WHERE f.severity = 'critical') AS findings_critical,
            COUNT(f.id) FILTER (WHERE f.severity = 'high') AS findings_high,
            COUNT(f.id) FILTER (WHERE f.severity = 'medium') AS findings_medium,
            COUNT(f.id) FILTER (WHERE f.severity = 'low') AS findings_low
        FROM lens_runs lr
        LEFT JOIN findings f ON f.lens_run_id = lr.id
        JOIN versions v ON v.id = lr.version_id
        JOIN laws l ON l.id = v.law_id
        JOIN jurisdictions j ON j.id = l.jurisdiction_id
        WHERE lr.lens_name = :lens_name
    """
    if jurisdictions is not None and not jurisdictions:
        return []
    params: dict[str, Any] = {"lens_name": lens_name, "limit": limit}
    if jurisdictions is not None:
        sql += " AND lower(j.code) = ANY(:jurisdictions)"
        params["jurisdictions"] = [j.strip().lower() for j in jurisdictions]
    sql += """
        GROUP BY lr.id, v.id, l.id, j.id
        ORDER BY lr.started_at DESC, lr.id DESC
        LIMIT :limit
    """
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


async def get_latest_succeeded_lens_run_for_law(
    session: AsyncSession,
    *,
    law_id: uuid.UUID,
    lens_name: str,
) -> LensRunRow | None:
    """Latest succeeded run across every version of `law_id`.

    "Latest" is most-recent-started, tiebroken by run id. The coverage rollup
    (`lens_coverage_summary_for_law`) resolves the same way, so a law's coverage
    badge and its findings endpoint always name the same run."""
    from codify.storage.models import Version

    return (
        await session.execute(
            select(LensRunRow)
            .join(Version, Version.id == LensRunRow.version_id)
            .where(Version.law_id == law_id)
            .where(LensRunRow.lens_name == lens_name)
            .where(LensRunRow.status == "succeeded")
            .order_by(LensRunRow.started_at.desc(), LensRunRow.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def lens_coverage_summary_for_law(
    session: AsyncSession,
    *,
    law_id: uuid.UUID,
) -> dict[str, dict[str, Any]]:
    """Per-lens "last succeeded scan" rollup; most recent run wins per lens.

    Same ordering as `get_latest_succeeded_lens_run_for_law` (started_at desc,
    then run id) so the coverage badge and the findings endpoint resolve the
    same run for a law."""
    sql = """
        SELECT
          lr.lens_name,
          lr.id            AS latest_run_id,
          lr.started_at    AS started_at,
          lr.finding_count AS finding_count,
          lr.warnings      AS warnings,
          v.id             AS version_id,
          v.language       AS version_language
        FROM (
          SELECT DISTINCT ON (lr.lens_name)
            lr.id, lr.lens_name, lr.version_id, lr.started_at, lr.finding_count, lr.warnings
          FROM lens_runs lr
          JOIN versions v ON v.id = lr.version_id
          WHERE v.law_id = :law_id AND lr.status = 'succeeded'
          ORDER BY lr.lens_name,
                   lr.started_at DESC,
                   lr.id DESC
        ) lr
        JOIN versions v ON v.id = lr.version_id
    """
    rows = (await session.execute(text(sql), {"law_id": law_id})).mappings().all()
    return {
        r["lens_name"]: {
            "latest_run_id": r["latest_run_id"],
            "version_id": r["version_id"],
            "version_language": r["version_language"],
            "finding_count": int(r["finding_count"] or 0),
            "started_at": r["started_at"],
            "warnings": r["warnings"],
        }
        for r in rows
    }


async def lens_coverage_summary_for_laws(
    session: AsyncSession,
    *,
    law_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, dict[str, Any]]]:
    """Batch variant of `lens_coverage_summary_for_law`. Returns the same
    shape indexed by law id; missing law ids omit rather than key to empty.
    Callers can default to `{}` for laws that were never scanned.

    Same `DISTINCT ON (law_id, lens_name)` ordering as the single-law helper
    (`started_at DESC, id DESC`), so the row picked per law is the same one the
    per-law helper would return.
    """
    if not law_ids:
        return {}
    sql = """
        SELECT
          v.law_id         AS law_id,
          lr.lens_name     AS lens_name,
          lr.id            AS latest_run_id,
          lr.started_at    AS started_at,
          lr.finding_count AS finding_count,
          lr.warnings      AS warnings,
          v.id             AS version_id,
          v.language       AS version_language
        FROM (
          SELECT DISTINCT ON (v.law_id, lr.lens_name)
            lr.id, lr.lens_name, lr.version_id, lr.started_at, lr.finding_count, lr.warnings,
            v.law_id
          FROM lens_runs lr
          JOIN versions v ON v.id = lr.version_id
          WHERE v.law_id = ANY(:law_ids) AND lr.status = 'succeeded'
          ORDER BY v.law_id, lr.lens_name,
                   lr.started_at DESC,
                   lr.id DESC
        ) lr
        JOIN versions v ON v.id = lr.version_id
    """
    rows = (
        (await session.execute(text(sql), {"law_ids": [str(i) for i in law_ids]})).mappings().all()
    )
    out: dict[uuid.UUID, dict[str, dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["law_id"], {})[r["lens_name"]] = {
            "latest_run_id": r["latest_run_id"],
            "version_id": r["version_id"],
            "version_language": r["version_language"],
            "finding_count": int(r["finding_count"] or 0),
            "started_at": r["started_at"],
            "warnings": r["warnings"],
        }
    return out


__all__ = [
    "complete_lens_run",
    "create_lens_run",
    "get_latest_lens_run",
    "get_latest_succeeded_lens_run_for_law",
    "get_lens_run",
    "lens_coverage_summary_for_law",
    "lens_coverage_summary_for_laws",
    "list_lens_runs_with_law_rollup",
]
