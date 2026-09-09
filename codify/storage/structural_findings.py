"""Persist and aggregate the structural scan's verdicts. Failed, passed and
could-not-run stay apart, so an unmeasurable corpus never reads as a clean one."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class FindingRate:
    """One check's outcome distribution within one era."""

    jurisdiction_code: str
    era: str
    check_name: str
    failed: int
    passed: int
    not_run: int

    @property
    def measured(self) -> int:
        return self.failed + self.passed

    @property
    def rate(self) -> float | None:
        """Failures over versions the check could run on, or None if none could."""
        return self.failed / self.measured if self.measured else None


async def record_findings(
    session: AsyncSession,
    *,
    scan_run_id: uuid.UUID,
    version_id: uuid.UUID,
    law_id: uuid.UUID,
    jurisdiction_code: str,
    era: str,
    doctype: str,
    findings: list[tuple[str, bool | None, dict[str, Any]]],
) -> int:
    """Write one row per check. Re-running a scan run over a version replaces its rows."""
    if not findings:
        return 0
    await session.execute(
        text("DELETE FROM structural_findings WHERE scan_run_id = :run AND version_id = :version"),
        {"run": scan_run_id, "version": version_id},
    )
    await session.execute(
        text("""
            INSERT INTO structural_findings
                (scan_run_id, version_id, law_id, jurisdiction_code, era, doctype,
                 check_name, failed, detail)
            VALUES (:run, :version, :law, :juris, :era, :doctype,
                    :check_name, :failed, CAST(:detail AS jsonb))
        """),
        [
            {
                "run": scan_run_id,
                "version": version_id,
                "law": law_id,
                "juris": jurisdiction_code,
                "era": era,
                "doctype": doctype,
                "check_name": check_name,
                "failed": failed,
                "detail": json.dumps(detail, ensure_ascii=False),
            }
            for check_name, failed, detail in findings
        ],
    )
    return len(findings)


async def finding_rates(
    session: AsyncSession, scan_run_id: uuid.UUID, jurisdiction_code: str | None = None
) -> list[FindingRate]:
    """Failed / passed / could-not-run counts per jurisdiction, era and check.
    Grouped by jurisdiction: every profile declaring no eras reports the same
    bucket, so pooling would mix unrelated corpora into one denominator."""
    result = await session.execute(
        text("""
            SELECT jurisdiction_code,
                   era,
                   check_name,
                   count(*) FILTER (WHERE failed IS TRUE)  AS failed,
                   count(*) FILTER (WHERE failed IS FALSE) AS passed,
                   count(*) FILTER (WHERE failed IS NULL)  AS not_run
            FROM structural_findings
            WHERE scan_run_id = :run
              AND (CAST(:juris AS TEXT) IS NULL OR jurisdiction_code = :juris)
            GROUP BY jurisdiction_code, era, check_name
            ORDER BY jurisdiction_code, era, check_name
        """),
        {"run": scan_run_id, "juris": jurisdiction_code},
    )
    return [
        FindingRate(
            jurisdiction_code=row.jurisdiction_code,
            era=row.era,
            check_name=row.check_name,
            failed=row.failed,
            passed=row.passed,
            not_run=row.not_run,
        )
        for row in result
    ]


@dataclass(frozen=True)
class RateDelta:
    """How one check moved between two scans, within one jurisdiction and era.

    A triple present in only one of the two runs still appears, with the missing
    side ``None`` and `in_both` false: a check that stopped running is a change
    worth seeing, and averaging it away is how a regression hides. ``None`` is
    not zero, so a caller reads `in_both` before either side.
    """

    jurisdiction_code: str
    era: str
    check_name: str
    before: FindingRate | None
    after: FindingRate | None

    @property
    def in_both(self) -> bool:
        return self.before is not None and self.after is not None

    @property
    def stopped_running(self) -> bool:
        """Present in the earlier run and gone from the later one. Distinct from
        a rate that could not be computed, which `rate_delta` also reports as
        None, so a summariser filtering on that alone drops this case."""
        return self.before is not None and self.after is None

    @property
    def started_running(self) -> bool:
        """New in the later run. Reported apart from `stopped_running` because
        the two read as opposite directions, and folding them together turns a
        newly added check into a regression."""
        return self.before is None and self.after is not None

    @property
    def rate_delta(self) -> float | None:
        """After minus before, or None when either side had nothing measurable.
        None is not zero: it means the comparison could not be made."""
        if self.before is None or self.after is None:
            return None
        if self.before.rate is None or self.after.rate is None:
            return None
        return self.after.rate - self.before.rate


async def compare_finding_rates(
    session: AsyncSession,
    before_run_id: uuid.UUID,
    after_run_id: uuid.UUID,
    jurisdiction_code: str | None = None,
) -> list[RateDelta]:
    """Per (jurisdiction, era, check), how the two scans differ.

    Two runs over an unchanged corpus and scanner produce an all-zero delta, so a
    non-zero one is attributable to whatever changed between them."""
    before = {
        (r.jurisdiction_code, r.era, r.check_name): r
        for r in await finding_rates(session, before_run_id, jurisdiction_code)
    }
    after = {
        (r.jurisdiction_code, r.era, r.check_name): r
        for r in await finding_rates(session, after_run_id, jurisdiction_code)
    }
    return [
        RateDelta(
            jurisdiction_code=key[0],
            era=key[1],
            check_name=key[2],
            before=before.get(key),
            after=after.get(key),
        )
        for key in sorted(before.keys() | after.keys())
    ]


async def versions_failing(
    session: AsyncSession, scan_run_id: uuid.UUID, check_name: str, era: str | None = None
) -> list[tuple[str, str]]:
    """`(work_uri, detail_json)` for versions that failed one check. A rate with no
    list behind it cannot be hand-checked, which is how a wrong check survives."""
    result = await session.execute(
        text("""
            SELECT l.frbr_work_uri AS uri, sf.detail::text AS detail
            FROM structural_findings sf
            JOIN laws l ON l.id = sf.law_id
            WHERE sf.scan_run_id = :run
              AND sf.check_name = :check_name
              AND sf.failed IS TRUE
              AND (CAST(:era AS TEXT) IS NULL OR sf.era = :era)
            ORDER BY l.frbr_work_uri
        """),
        {"run": scan_run_id, "check_name": check_name, "era": era},
    )
    return [(row.uri, row.detail) for row in result]


async def scanned_versions(
    session: AsyncSession, scan_run_id: uuid.UUID, jurisdiction_code: str | None = None
) -> int:
    """Versions this scan recorded, under the same filter as the rates: a failed
    chunk silently shrinks every denominator, and an unfiltered count beside
    filtered rates overstates the population."""
    result = await session.execute(
        text(
            "SELECT count(DISTINCT version_id) FROM structural_findings "
            "WHERE scan_run_id = :run "
            "  AND (CAST(:juris AS TEXT) IS NULL OR jurisdiction_code = :juris)"
        ),
        {"run": scan_run_id, "juris": jurisdiction_code},
    )
    return int(result.scalar_one())


__all__ = [
    "FindingRate",
    "finding_rates",
    "record_findings",
    "scanned_versions",
    "versions_failing",
]
