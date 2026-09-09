"""Findings CRUD, substrate for the Lens API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, NamedTuple, TypedDict, cast

from sqlalchemy import ARRAY, String, case, delete, desc, func, or_, select, text, update
from sqlalchemy import cast as sa_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from codify.lenses.types import Finding, FindingGroupBy, Severity
from codify.storage._pagination import paginate_by_created_id
from codify.storage.examinations import find_prior_decision, verdict_matches
from codify.storage.models import ExaminationRow, FindingRow, Jurisdiction, Law, Version

_TRACE_ID_KEY = "_langfuse_trace_id"

_SEVERITIES = ("critical", "high", "medium", "low")


class LensRollupRow(TypedDict):
    law_id: uuid.UUID
    law_title: str
    version_id: uuid.UUID
    run_id: uuid.UUID
    by_severity: dict[str, int]
    total: int


def _finding_to_row(finding: Finding) -> FindingRow:
    # Trace id is stashed inside `payload` under a reserved key.
    payload = dict(finding.payload)
    if finding.langfuse_trace_id is not None:
        payload[_TRACE_ID_KEY] = finding.langfuse_trace_id
    return FindingRow(
        id=finding.id,
        lens_run_id=finding.lens_run_id,
        lens_name=finding.lens_name,
        version_id=finding.version_id,
        provision_id=finding.provision_id,
        provision_eid=finding.provision_eid,
        severity=finding.severity,
        confidence=finding.confidence,
        rationale=finding.rationale,
        recommendation=finding.recommendation,
        payload=payload,
        created_at=finding.created_at,
    )


def _row_to_finding(row: FindingRow) -> Finding:
    payload = dict(row.payload or {})
    trace_id = payload.pop(_TRACE_ID_KEY, None)
    return Finding(
        id=row.id,
        lens_run_id=row.lens_run_id,
        lens_name=row.lens_name,
        version_id=row.version_id,
        provision_id=row.provision_id,
        provision_eid=row.provision_eid,
        severity=cast(Severity, row.severity),
        confidence=row.confidence,
        rationale=row.rationale,
        recommendation=row.recommendation,
        payload=payload,
        langfuse_trace_id=trace_id if isinstance(trace_id, str) else None,
        created_at=row.created_at,
    )


async def save_finding(session: AsyncSession, finding: Finding) -> None:
    row = _finding_to_row(finding)
    prior = await find_prior_decision(
        session,
        lens_name=finding.lens_name,
        version_id=finding.version_id,
        provision_eid=finding.provision_eid,
    )
    if prior is not None and verdict_matches(prior, row.payload, row.severity):
        # Flush before the audit row: FK target must exist.
        row.review_status = prior.review_status
        row.reviewed_by = prior.reviewed_by
        row.reviewed_at = prior.reviewed_at
        row.decision_note = prior.decision_note
        session.add(row)
        await session.flush()
        session.add(
            ExaminationRow(
                finding_id=row.id,
                decision="carried_forward",
                actor="system",
                note=f"auto-stickied from finding_id={prior.id}",
                prior_state=prior.review_status,
                run_id=finding.lens_run_id,
            )
        )
        await session.flush()
        return
    if prior is not None:
        row.payload = {
            **row.payload,
            "prior_review": {
                "decision": prior.review_status,
                "actor": prior.reviewed_by,
                "at": prior.reviewed_at.isoformat() if prior.reviewed_at else None,
                "verdict_then": (prior.payload or {}).get("verdict"),
                "severity_then": prior.severity,
            },
        }
    session.add(row)
    await session.flush()


async def save_findings(session: AsyncSession, findings: list[Finding]) -> None:
    """Bulk insert with batched prior-decision carry-forward."""
    if not findings:
        return
    rows = [_finding_to_row(f) for f in findings]
    keys = {PriorKey(r.lens_name, r.version_id, r.provision_eid) for r in rows}
    priors = await _latest_prior_decisions(session, keys)
    exam_rows: list[ExaminationRow] = []
    for row in rows:
        prior = priors.get(PriorKey(row.lens_name, row.version_id, row.provision_eid))
        if prior is not None and verdict_matches(prior, row.payload, row.severity):
            row.review_status = prior.review_status
            row.reviewed_by = prior.reviewed_by
            row.reviewed_at = prior.reviewed_at
            row.decision_note = prior.decision_note
            exam_rows.append(
                ExaminationRow(
                    finding_id=row.id,
                    decision="carried_forward",
                    actor="system",
                    note=f"auto-stickied from finding_id={prior.id}",
                    prior_state=prior.review_status,
                    run_id=row.lens_run_id,
                )
            )
        elif prior is not None:
            row.payload = {
                **row.payload,
                "prior_review": {
                    "decision": prior.review_status,
                    "actor": prior.reviewed_by,
                    "at": prior.reviewed_at.isoformat() if prior.reviewed_at else None,
                    "verdict_then": (prior.payload or {}).get("verdict"),
                    "severity_then": prior.severity,
                },
            }
    session.add_all(rows)
    await session.flush()
    if exam_rows:
        session.add_all(exam_rows)
        await session.flush()


class PriorKey(NamedTuple):
    lens_name: str
    version_id: uuid.UUID
    provision_eid: str


async def _latest_prior_decisions(
    session: AsyncSession,
    keys: set[PriorKey],
) -> dict[PriorKey, FindingRow]:
    if not keys:
        return {}
    lens_names = {k.lens_name for k in keys}
    version_ids = {k.version_id for k in keys}
    eids = {k.provision_eid for k in keys}
    rows = (
        (
            await session.execute(
                select(FindingRow)
                .where(
                    FindingRow.lens_name.in_(lens_names),
                    FindingRow.version_id.in_(version_ids),
                    FindingRow.provision_eid.in_(eids),
                    FindingRow.review_status != "open",
                )
                .order_by(FindingRow.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    out: dict[PriorKey, FindingRow] = {}
    for r in rows:
        key = PriorKey(r.lens_name, r.version_id, r.provision_eid)
        if key in keys and key not in out:
            out[key] = r
    return out


async def get_finding(session: AsyncSession, finding_id: uuid.UUID) -> Finding | None:
    """Load a single finding by id, or None if not found."""
    row = (
        await session.execute(select(FindingRow).where(FindingRow.id == finding_id))
    ).scalar_one_or_none()
    return _row_to_finding(row) if row is not None else None


def _finding_where(
    version_id: uuid.UUID,
    lens_name: str,
    *,
    lens_run_id: uuid.UUID | None = None,
    actionable: bool | None = None,
    severity: str | None = None,
    factor_code: str | None = None,
    risk_class: str | None = None,
    provision_eid: str | None = None,
) -> list[Any]:
    """Shared WHERE clauses for the findings listing, count and aggregation, so a
    page and its total scope identically. Aggregation passes only the run, not the
    listing filters, so stats cover the whole run. `severity` is a column,
    `factor_code` and `risk_class` are payload keys, and `provision_eid` matches
    the provision or any descendant. eIds are underscore-dense, so the prefix
    match sets `autoescape` to stop `art_5` matching `art_50`.
    """
    where = [FindingRow.version_id == version_id, FindingRow.lens_name == lens_name]
    if lens_run_id is not None:
        where.append(FindingRow.lens_run_id == lens_run_id)
    if actionable is True:
        # Default-true semantics: only 'false' is non-actionable; null/absent counts as actionable.
        where.append(FindingRow.payload["actionable"].astext != "false")
    elif actionable is False:
        where.append(FindingRow.payload["actionable"].astext == "false")
    if severity is not None:
        where.append(FindingRow.severity == severity)
    if factor_code is not None:
        where.append(FindingRow.payload["factor_code"].astext == factor_code)
    if risk_class is not None:
        where.append(FindingRow.payload["risk_class"].astext == risk_class)
    if provision_eid is not None:
        where.append(
            or_(
                FindingRow.provision_eid == provision_eid,
                FindingRow.provision_eid.startswith(provision_eid + "__", autoescape=True),
            )
        )
    return where


async def list_findings(
    session: AsyncSession,
    version_id: uuid.UUID,
    lens_name: str,
    *,
    limit: int = 100,
    cursor: uuid.UUID | None = None,
    lens_run_id: uuid.UUID | None = None,
    actionable: bool | None = None,
    severity: str | None = None,
    factor_code: str | None = None,
    risk_class: str | None = None,
    provision_eid: str | None = None,
) -> tuple[list[Finding], uuid.UUID | None]:
    """Findings for a (version, lens). Optionally scoped to one run, which callers
    default to the latest to drop stale re-run findings; to actionable-only
    (`payload->>'actionable' != 'false'`); or filtered by `severity`,
    `factor_code`, `risk_class` or `provision_eid`, exact or section-prefix.
    """
    where = _finding_where(
        version_id,
        lens_name,
        lens_run_id=lens_run_id,
        actionable=actionable,
        severity=severity,
        factor_code=factor_code,
        risk_class=risk_class,
        provision_eid=provision_eid,
    )
    rows, next_cursor = await paginate_by_created_id(
        session,
        FindingRow,
        where=where,
        limit=limit,
        cursor=cursor,
    )
    return [_row_to_finding(r) for r in rows], next_cursor


async def list_review_findings(
    session: AsyncSession,
    *,
    limit: int = 25,
    cursor: uuid.UUID | None = None,
    confidence_threshold: float = 0.7,
    jurisdictions: tuple[str, ...] | None = None,
    status: str = "open",
    lens_name: str | None = None,
    allowed_lenses: tuple[str, ...] | None = None,
) -> tuple[list[FindingRow], uuid.UUID | None]:
    """Status='open' returns un-triaged high-severity low-confidence rows; other
    statuses return everything matching, same ranking. ``allowed_lenses``
    restricts to an org's entitled lenses: ``None`` is no restriction, an empty
    tuple is no findings, which is the fail-closed case.
    """

    severity_rank = case(
        (FindingRow.severity == "critical", 3),
        (FindingRow.severity == "high", 2),
        (FindingRow.severity == "medium", 1),
        (FindingRow.severity == "low", 0),
        else_=0,
    )
    stmt = (
        select(FindingRow)
        .order_by(
            desc(severity_rank),
            FindingRow.confidence,
            desc(FindingRow.created_at),
            desc(FindingRow.id),
        )
        .limit(limit + 1)
    )
    if status == "open":
        # Default queue: only un-triaged high-severity low-confidence rows.
        stmt = stmt.where(
            FindingRow.review_status == "open",
            FindingRow.confidence < confidence_threshold,
            FindingRow.severity.in_(("high", "critical")),
        )
    elif status != "all":
        stmt = stmt.where(FindingRow.review_status == status)

    if lens_name is not None:
        stmt = stmt.where(FindingRow.lens_name == lens_name)
    if allowed_lenses is not None:
        stmt = stmt.where(FindingRow.lens_name.in_(allowed_lenses))

    if jurisdictions is not None and "*" not in jurisdictions:
        stmt = (
            stmt.join(Version, Version.id == FindingRow.version_id)
            .join(Law, Law.id == Version.law_id)
            .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
            .where(Jurisdiction.code.in_(jurisdictions))
        )
    if cursor is not None:
        anchor = (
            await session.execute(select(FindingRow.id).where(FindingRow.id == cursor))
        ).first()
        if anchor is None:
            return [], None
        stmt = stmt.where(FindingRow.id < cursor)

    rows = (await session.execute(stmt)).scalars().all()
    page_rows = list(rows[:limit])
    next_cursor = page_rows[-1].id if len(rows) > limit else None
    return page_rows, next_cursor


async def count_open_findings_for_jurisdictions(
    session: AsyncSession,
    jurisdictions: tuple[str, ...] | None,
    allowed_lenses: tuple[str, ...] | None = None,
) -> int:

    stmt = select(func.count(FindingRow.id)).where(FindingRow.review_status == "open")
    if allowed_lenses is not None:
        stmt = stmt.where(FindingRow.lens_name.in_(allowed_lenses))
    if jurisdictions is not None and "*" not in jurisdictions:
        stmt = (
            stmt.join(Version, Version.id == FindingRow.version_id)
            .join(Law, Law.id == Version.law_id)
            .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
            .where(Jurisdiction.code.in_(jurisdictions))
        )
    return int((await session.execute(stmt)).scalar_one())


async def count_findings(
    session: AsyncSession,
    version_id: uuid.UUID,
    lens_name: str,
    *,
    lens_run_id: uuid.UUID | None = None,
    actionable: bool | None = None,
    severity: str | None = None,
    factor_code: str | None = None,
    risk_class: str | None = None,
    provision_eid: str | None = None,
) -> int:
    stmt = select(func.count(FindingRow.id)).where(
        *_finding_where(
            version_id,
            lens_name,
            lens_run_id=lens_run_id,
            actionable=actionable,
            severity=severity,
            factor_code=factor_code,
            risk_class=risk_class,
            provision_eid=provision_eid,
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


# The dimensions a caller may aggregate findings by. `section` folds by the
# outermost AKN eId segment (the article/section container); the others are a
# column (severity) or a payload key.
_GROUP_KEYS = {
    "severity": FindingRow.severity,
    "factor_code": FindingRow.payload["factor_code"].astext,
    "risk_class": FindingRow.payload["risk_class"].astext,
    "section": func.split_part(FindingRow.provision_eid, "__", 1),
}


async def aggregate_findings(
    session: AsyncSession,
    version_id: uuid.UUID,
    lens_name: str,
    *,
    group_by: FindingGroupBy,
    lens_run_id: uuid.UUID | None = None,
) -> list[tuple[str, int]]:
    """Grouped finding counts for a (version, lens[, run]) by one dimension, most
    frequent first: the flagship "what are the themes" question in one GROUP BY
    instead of downloading every finding. Scopes to the same run the listing
    uses so the numbers agree."""
    key = _GROUP_KEYS[group_by]
    stmt = (
        select(key.label("k"), func.count(FindingRow.id).label("n"))
        .where(*_finding_where(version_id, lens_name, lens_run_id=lens_run_id))
        .group_by(key)
        .order_by(func.count(FindingRow.id).desc(), key)
    )
    rows = (await session.execute(stmt)).all()
    return [("" if k is None else str(k), int(n)) for k, n in rows]


async def lens_rollup(
    session: AsyncSession, *, jurisdiction_code: str, lens_name: str
) -> list[LensRollupRow]:
    """Per-law severity rollup for a lens across a jurisdiction, omitting laws this
    lens has never scanned. Picks the latest succeeded lens_run per law rather
    than the law's newest version, then joins findings on that scanned version, so
    an old scan keeps answering the dashboard through a re-ingest until a new scan
    lands. Same DISTINCT ON pattern as `lens_coverage_summary_for_law`.
    """
    # Most recent scan first with lens_run_id as a deterministic final key, the
    # same ordering every lens surface uses, so the dashboard, the coverage
    # badge and the findings endpoint name one run. `f.lens_run_id =
    # picked.run_id` scopes to that run, so a re-scan's orphan rows tagged with
    # a prior lens_run_id do not double-count.
    sql = """
        SELECT
          v.law_id       AS law_id,
          law.title      AS law_title,
          v.id           AS version_id,
          picked.run_id  AS run_id,
          f.severity     AS severity,
          count(f.id)    AS n
        FROM (
          SELECT DISTINCT ON (v.law_id)
            lr.id AS run_id, lr.version_id, v.law_id
          FROM lens_runs lr
          JOIN versions v ON v.id = lr.version_id
          JOIN laws l ON l.id = v.law_id
          JOIN jurisdictions j ON j.id = l.jurisdiction_id
          WHERE j.code = :code
            AND lr.status = 'succeeded'
            AND lr.lens_name = :lens_name
          ORDER BY v.law_id,
                   lr.started_at DESC,
                   lr.id DESC
        ) picked
        JOIN versions v ON v.id = picked.version_id
        JOIN laws law ON law.id = v.law_id
        LEFT JOIN findings f
               ON f.lens_run_id = picked.run_id
              AND f.lens_name = :lens_name
        GROUP BY v.law_id, law.title, v.id, picked.run_id, f.severity
    """
    rows = (
        await session.execute(text(sql), {"code": jurisdiction_code, "lens_name": lens_name})
    ).all()

    by_law: dict[uuid.UUID, LensRollupRow] = {}
    for law_id, law_title, version_id, run_id, severity, count in rows:
        entry = by_law.setdefault(
            law_id,
            LensRollupRow(
                law_id=law_id,
                law_title=law_title,
                version_id=version_id,
                run_id=run_id,
                by_severity={s: 0 for s in _SEVERITIES},
                total=0,
            ),
        )
        if severity in entry["by_severity"]:
            entry["by_severity"][severity] += int(count)
            entry["total"] += int(count)
    return sorted(by_law.values(), key=lambda e: (-e["total"], e["law_title"]))


class AcquisFindingRow(TypedDict):
    verdict: str | None
    actionable: str | None
    directive_law_id: str
    directive_heading: str | None
    provision_eid: str | None
    domestic_law_id: uuid.UUID
    domestic_law_title: str
    run_id: uuid.UUID
    # False when the answering run assessed this pair and found nothing. The row
    # still stands so the pair counts as assessed rather than as never scanned.
    has_finding: bool


async def replace_lens_run_directives(
    session: AsyncSession,
    *,
    lens_run_id: uuid.UUID,
    directive_law_ids: list[uuid.UUID],
) -> None:
    """Record the directives a run was asked to assess, replacing any prior set.
    Called before the scan so coverage stands even where a directive yields no
    findings, and a retried step replaces rather than accumulates. Ids with no
    matching law are skipped: the scan raises a named error for those, a better
    diagnostic than a foreign-key violation here.
    """
    await session.execute(
        text("DELETE FROM lens_run_directives WHERE lens_run_id = :run_id"),
        {"run_id": str(lens_run_id)},
    )
    if not directive_law_ids:
        return
    await session.execute(
        text(
            "INSERT INTO lens_run_directives (lens_run_id, directive_law_id) "
            "SELECT :run_id, l.id FROM laws l WHERE l.id = ANY(CAST(:dir_ids AS uuid[])) "
            "ON CONFLICT DO NOTHING"
        ),
        {"run_id": str(lens_run_id), "dir_ids": [str(d) for d in directive_law_ids]},
    )


async def acquis_findings_for_directives(
    session: AsyncSession,
    *,
    jurisdiction_code: str,
    directive_law_ids: list[str],
) -> list[AcquisFindingRow]:
    """eu_acquis findings for the given directives in one jurisdiction, scoped to the
    run that currently answers for each (domestic law, directive) pair.

    The pick is per pair, not per law: a run is parameterised by the directives it
    covers, so a law assessed against one directive by a later run keeps the
    findings other runs produced for directives that run did not touch. Superseded
    and failed runs drop out. Candidates come from recorded coverage
    (`lens_run_directives`), so a run that covered a directive and found nothing
    still wins the pair; runs with no coverage recorded fall back to the pairs
    their findings imply, so coverage only adds candidates and a missing row
    cannot hide a run's findings. Every picked pair yields at least one row, a
    clean one coming back with `has_finding=False`, so a caller can tell assessed
    from never scanned.
    """
    if not directive_law_ids:
        return []
    sql = """
        WITH coverage AS (
          SELECT
            v.law_id                    AS domestic_law_id,
            d.directive_law_id::text    AS directive_law_id,
            lr.id                       AS run_id,
            lr.started_at               AS started_at
          FROM lens_run_directives d
          JOIN lens_runs lr ON lr.id = d.lens_run_id
                           AND lr.status = 'succeeded'
                           AND lr.lens_name = 'eu_acquis'
          JOIN versions v ON v.id = lr.version_id
          JOIN laws l ON l.id = v.law_id
          JOIN jurisdictions j ON j.id = l.jurisdiction_id
          WHERE j.code = :code
            AND d.directive_law_id = ANY(CAST(:dir_uuids AS uuid[]))

          UNION

          -- Runs predating recorded coverage, or any that failed to record it:
          -- infer their pairs from findings so this table only ever adds
          -- candidates. A run with coverage never reaches this branch.
          SELECT
            v.law_id                          AS domestic_law_id,
            f.payload->>'directive_law_id'    AS directive_law_id,
            lr.id                             AS run_id,
            lr.started_at                     AS started_at
          FROM findings f
          JOIN lens_runs lr ON lr.id = f.lens_run_id
                           AND lr.status = 'succeeded'
                           AND lr.lens_name = 'eu_acquis'
          JOIN versions v ON v.id = lr.version_id
          JOIN laws l ON l.id = v.law_id
          JOIN jurisdictions j ON j.id = l.jurisdiction_id
          WHERE j.code = :code
            AND f.lens_name = 'eu_acquis'
            AND f.payload->>'directive_law_id' = ANY(CAST(:dir_texts AS text[]))
            AND NOT EXISTS (
              SELECT 1 FROM lens_run_directives d2 WHERE d2.lens_run_id = lr.id
            )
        ),
        picked AS (
          SELECT DISTINCT ON (domestic_law_id, directive_law_id)
            domestic_law_id, directive_law_id, run_id
          FROM coverage
          ORDER BY domestic_law_id, directive_law_id, started_at DESC, run_id DESC
        )
        SELECT
          f.payload->>'verdict'           AS verdict,
          f.payload->>'actionable'        AS actionable,
          p.directive_law_id              AS directive_law_id,
          f.payload->>'directive_heading' AS directive_heading,
          f.provision_eid                 AS provision_eid,
          p.domestic_law_id               AS domestic_law_id,
          l.title                         AS domestic_law_title,
          p.run_id                        AS run_id,
          f.id IS NOT NULL                AS has_finding
        FROM picked p
        JOIN laws l ON l.id = p.domestic_law_id
        LEFT JOIN findings f ON f.lens_run_id = p.run_id
                            AND f.lens_name = 'eu_acquis'
                            AND f.payload->>'directive_law_id' = p.directive_law_id
    """
    rows = (
        await session.execute(
            text(sql),
            {
                "code": jurisdiction_code,
                # Same ids, bound twice: the coverage branch compares uuids (so
                # the index applies), the fallback compares payload text.
                "dir_uuids": list(directive_law_ids),
                "dir_texts": list(directive_law_ids),
            },
        )
    ).mappings()
    return [cast(AcquisFindingRow, dict(r)) for r in rows]


async def delete_findings_for_run(session: AsyncSession, lens_run_id: uuid.UUID) -> int:
    result = await session.execute(delete(FindingRow).where(FindingRow.lens_run_id == lens_run_id))
    await session.flush()
    return int(result.rowcount or 0)


async def mark_finding_accepted(
    session: AsyncSession,
    finding_id: uuid.UUID,
    new_version_id: uuid.UUID,
) -> None:
    """Resolve a finding via suggestion-apply: stamp the new version, set
    review_status='accepted', append an examinations row, single UPDATE."""
    actor = "system-suggestion-apply"
    note = f"resolved via amended version {new_version_id}"
    prior = await session.get(FindingRow, finding_id)
    if prior is None:
        raise LookupError(f"finding {finding_id} not found")
    prior_state = prior.review_status
    now = datetime.now(UTC)
    await session.execute(
        update(FindingRow)
        .where(FindingRow.id == finding_id)
        .values(
            payload=func.jsonb_set(
                FindingRow.payload.cast(JSONB),
                sa_cast(["accepted_as_version_id"], ARRAY(String)),
                func.to_jsonb(str(new_version_id)),
                True,
            ),
            review_status="accepted",
            reviewed_by=actor,
            reviewed_at=now,
            decision_note=note,
        )
    )
    session.add(
        ExaminationRow(
            finding_id=finding_id,
            decision="accepted",
            actor=actor,
            note=note,
            prior_state=prior_state,
        )
    )
    await session.flush()


__all__ = [
    "count_findings",
    "delete_findings_for_run",
    "list_findings",
    "list_review_findings",
    "mark_finding_accepted",
    "save_finding",
    "save_findings",
]
