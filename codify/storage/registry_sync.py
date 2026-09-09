"""Idempotent import of an official registry + typed link graph."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

_BATCH = 5_000

# vidnosh relation id -> edge_class; unmapped ids fall back to freetext_reference.
VIDNOSH_TO_EDGE_CLASS: dict[int, str] = {
    1: "mod_textual",  # amends
    3: "mod_force",  # brings into force
    4: "mod_force",  # repeals
    5: "mod_meaning",  # clarifies
    7: "mod_efficacy",  # suspends / vetoes
}


async def upsert_registry_works(
    session: AsyncSession,
    jurisdiction_id: uuid.UUID,
    rows: Iterable[dict[str, Any]],
) -> int:
    """Upsert (external_id keyed) registry rows; returns rows written."""
    stmt = text(
        """
        INSERT INTO registry_works
            (jurisdiction_id, external_id, ref, title, doc_types, status, updated_at)
        VALUES (:j, :external_id, :ref, :title, :doc_types, :status, now())
        ON CONFLICT (jurisdiction_id, external_id) DO UPDATE SET
            ref = EXCLUDED.ref,
            title = EXCLUDED.title,
            doc_types = EXCLUDED.doc_types,
            status = EXCLUDED.status,
            updated_at = now()
        """
    )
    count = 0
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append({"j": jurisdiction_id, **row})
        if len(batch) >= _BATCH:
            await session.execute(stmt, batch)
            count += len(batch)
            batch = []
    if batch:
        await session.execute(stmt, batch)
        count += len(batch)
    return count


async def replace_law_links(
    session: AsyncSession,
    jurisdiction_id: uuid.UUID,
    links: Iterable[dict[str, Any]],
    *,
    source: str,
) -> int:
    """Atomically replace this source's imported edges; returns rows written."""
    await session.execute(
        text("DELETE FROM law_links WHERE jurisdiction_id = :j AND source = :s"),
        {"j": jurisdiction_id, "s": source},
    )
    stmt = text(
        """
        INSERT INTO law_links
            (jurisdiction_id, source_ref, target_ref, relation, edge_class, source)
        VALUES (:j, :source_ref, :target_ref, :relation, :edge_class, :s)
        ON CONFLICT (jurisdiction_id, source_ref, target_ref, relation) DO NOTHING
        """
    )
    count = 0
    batch: list[dict[str, Any]] = []
    for link in links:
        relation = int(link["relation"])
        batch.append(
            {
                "j": jurisdiction_id,
                "s": source,
                "source_ref": link["source_ref"],
                "target_ref": link["target_ref"],
                "relation": relation,
                "edge_class": VIDNOSH_TO_EDGE_CLASS.get(relation, "freetext_reference"),
            }
        )
        if len(batch) >= _BATCH:
            await session.execute(stmt, batch)
            count += len(batch)
            batch = []
    if batch:
        await session.execute(stmt, batch)
        count += len(batch)
    return count


async def link_registry_to_held_laws(session: AsyncSession, jurisdiction_id: uuid.UUID) -> int:
    """Stamp registry_works.law_id where a held law's work URI ends in the ref.

    Matches the raw ref and its citation form (rada era codes are VR
    convocation roman + 11: 198-19 <-> 198-VIII)."""
    result = await session.execute(
        text(
            """
            WITH era(code, roman) AS (VALUES
                ('12','XII'),('13','XIII'),('14','XIV'),('15','IV'),('16','V'),
                ('17','VI'),('18','VII'),('19','VIII'),('20','IX'),('21','X')
            )
            UPDATE registry_works rw
            SET law_id = l.id
            FROM laws l, era e
            WHERE rw.jurisdiction_id = :j
              AND l.jurisdiction_id = :j
              AND rw.law_id IS NULL
              AND (
                l.frbr_work_uri LIKE '%/' || rw.ref
                OR (rw.ref LIKE '%-' || e.code
                    AND l.frbr_work_uri LIKE
                        '%/' || left(rw.ref, length(rw.ref) - length(e.code)) || e.roman)
              )
            """
        ),
        {"j": jurisdiction_id},
    )
    return int(result.rowcount or 0)


async def registry_counts(session: AsyncSession, jurisdiction_id: uuid.UUID) -> dict[str, int]:
    row = (
        await session.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM registry_works WHERE jurisdiction_id = :j) AS works,
                  (SELECT count(*) FROM registry_works
                     WHERE jurisdiction_id = :j AND law_id IS NOT NULL) AS held,
                  (SELECT count(*) FROM law_links WHERE jurisdiction_id = :j) AS links
                """
            ),
            {"j": jurisdiction_id},
        )
    ).one()
    return {"registry_works": row.works, "held_matched": row.held, "law_links": row.links}


# podia event id -> lifecycle EventType; unmapped events are skipped.
PODIA_TO_EVENT: dict[int, str] = {
    0: "amendment",  # edition
    1: "generation",  # entry into force
    3: "repeal",
    4: "generation",  # adoption
    6: "amendment",  # new edition
}


async def import_card_lifecycle(
    session: AsyncSession,
    law_id: uuid.UUID,
    history: list[dict[str, Any]],
    *,
    amender_uri: "Any" = None,
) -> int:
    """Replace the latest version's card-derived lifecycle rows.

    The full card history attaches to the head version as the consolidated
    timeline; per-edition distribution is a Stage-B concern. `history` items:
    {"date": "YYYYMMDD", "podid": int, "pidstava": str|None}; rows are marked
    source_uri='rada-card' and rebuilt per import.
    """
    version_id = (
        await session.execute(
            text(
                """
                SELECT id FROM versions WHERE law_id = :l
                ORDER BY expression_date DESC, ingested_at DESC LIMIT 1
                """
            ),
            {"l": law_id},
        )
    ).scalar_one_or_none()
    if version_id is None:
        return 0
    await session.execute(
        text("DELETE FROM lifecycle_events WHERE version_id = :v AND source_uri = 'rada-card'"),
        {"v": version_id},
    )
    stmt = text(
        """
        INSERT INTO lifecycle_events (version_id, event_date, event_type, source_uri, refers_uri)
        VALUES (:v, :d, :t, 'rada-card', :r)
        """
    )
    count = 0
    for h in history:
        event = PODIA_TO_EVENT.get(int(h.get("podid", -1)))
        raw = str(h.get("date") or "")
        if event is None or len(raw) != 8 or not raw.isdigit():
            continue
        await session.execute(
            stmt,
            {
                "v": version_id,
                "d": date(int(raw[:4]), int(raw[4:6]), int(raw[6:])),
                "t": event,
                "r": (amender_uri(h["pidstava"]) if amender_uri and h.get("pidstava") else None),
            },
        )
        count += 1
    return count


# Registry lifecycle statuses (Rada `stan` codes) → semantic labels.
REGISTRY_STATUS_LABELS: dict[int, str] = {
    0: "undetermined",
    1: "repealed",
    2: "commencing",
    3: "suspended",
    4: "resumed",
    5: "in_force",
    6: "not_yet_in_force",
    7: "not_applied",
    8: "partially_in_force",
    9: "repealed_except_provisions",
}

_ERA_VALUES = (
    "('12','XII'),('13','XIII'),('14','XIV'),('15','IV'),('16','V'),"
    "('17','VI'),('18','VII'),('19','VIII'),('20','IX'),('21','X')"
)


async def law_impact(session: AsyncSession, law_id: uuid.UUID) -> list[dict[str, Any]]:
    """Outgoing law-level references from the law's latest version, with the
    official registry status of each target where the registry knows it."""
    rows = (
        await session.execute(
            text(
                f"""
                WITH era(code, roman) AS (VALUES {_ERA_VALUES}),
                latest AS (
                    SELECT id FROM versions
                    WHERE law_id = :law_id
                    ORDER BY ingested_at DESC LIMIT 1
                ),
                refs AS (
                    SELECT x.target_uri, x.target_law_id, x.edge_class
                    FROM cross_references x
                    JOIN provisions p ON p.id = x.source_provision_id
                    WHERE p.version_id = (SELECT id FROM latest)
                      AND (x.target_law_id IS NOT NULL OR x.target_uri LIKE '/akn/%')
                      -- extractor emits 'None' URI slots when a citation
                      -- lacks full identity; no target to show for those
                      AND x.target_uri NOT LIKE '%/None%'
                      AND x.target_uri NOT LIKE '%None/%'
                )
                SELECT
                    COALESCE(r.target_uri, '') AS target_uri,
                    r.target_law_id,
                    COALESCE(tl.title, rw.title) AS title,
                    rw.ref AS registry_ref,
                    rw.status AS registry_status,
                    count(*) AS ref_count,
                    count(*) FILTER (WHERE r.edge_class LIKE 'mod_%') AS mod_count
                FROM refs r
                LEFT JOIN laws tl ON tl.id = r.target_law_id
                LEFT JOIN era e ON r.target_uri ~ ('-' || e.roman || '$')
                LEFT JOIN registry_works rw ON rw.id = COALESCE(
                    (SELECT rw1.id FROM registry_works rw1
                     WHERE rw1.law_id = r.target_law_id LIMIT 1),
                    (SELECT rw2.id FROM registry_works rw2
                     JOIN jurisdictions j2 ON j2.id = rw2.jurisdiction_id
                     WHERE e.code IS NOT NULL
                       AND rw2.ref = regexp_replace(
                               split_part(r.target_uri, '/', 6),
                               '-' || e.roman || '$', '-' || e.code)
                       AND j2.code = split_part(r.target_uri, '/', 3)
                     LIMIT 1)
                )
                GROUP BY r.target_uri, r.target_law_id, tl.title, rw.title,
                         rw.ref, rw.status
                ORDER BY mod_count DESC, ref_count DESC
                """  # noqa: S608
            ),
            {"law_id": law_id},
        )
    ).mappings()
    return [dict(r) for r in rows]
