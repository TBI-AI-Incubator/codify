"""Events / audit log, append-only application activity."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage._pagination import paginate_by_created_id
from codify.storage.models import EventRow


class Event(BaseModel):
    """Application-level event. Append-only. Free-form entity_type and
    event_type; new kinds don't need a migration. `jurisdiction_code`,
    `law_title`, `law_id` are derived on read and None on emit."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    actor_id: str
    entity_type: str
    entity_id: uuid.UUID
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    jurisdiction_code: str | None = None
    law_title: str | None = None
    law_id: uuid.UUID | None = None


def _to_row(e: Event) -> EventRow:
    return EventRow(
        id=e.id,
        actor_id=e.actor_id,
        entity_type=e.entity_type,
        entity_id=e.entity_id,
        event_type=e.event_type,
        payload=e.payload,
        created_at=e.created_at,
    )


def _to_event(row: EventRow) -> Event:
    return Event(
        id=row.id,
        actor_id=row.actor_id,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        event_type=row.event_type,
        payload=row.payload,
        created_at=row.created_at,
    )


async def save_event(session: AsyncSession, event: Event) -> Event:
    session.add(_to_row(event))
    await session.flush()
    return event


async def reserve_actor_action(
    session: AsyncSession,
    actor_id: str,
    event_type: str,
    *,
    since: datetime,
    limit: int,
    entity_type: str,
    entity_id: uuid.UUID,
) -> bool:
    """Count this actor's recent actions and record one, atomically.

    Returns False when the actor is already at `limit`, having recorded
    nothing. Counting and inserting as separate statements is the hole a burst
    walks through: every request reads a count below the limit before any of
    them commits, so all of them proceed. The advisory lock is transaction
    scoped and keyed on the actor, so requests for one actor serialise here
    while different actors never wait on each other. The caller commits, which
    releases it.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{event_type}:{actor_id}"},
    )
    used = await count_events_for_actor_since(session, actor_id, event_type, since=since)
    if used >= limit:
        return False
    await save_event(
        session,
        Event(
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
        ),
    )
    return True


async def count_events_for_actor_since(
    session: AsyncSession,
    actor_id: str,
    event_type: str,
    *,
    since: datetime,
) -> int:
    """How many `event_type` events this actor recorded since `since`.

    Backs per-actor rate limits: the table is append-only and shared by every
    worker, so the count holds across replicas and restarts where a
    process-local counter would not.
    """
    result = await session.execute(
        text(
            "SELECT count(*) FROM events "
            "WHERE actor_id = :actor AND event_type = :etype AND created_at >= :since"
        ),
        {"actor": actor_id, "etype": event_type, "since": since},
    )
    return int(result.scalar_one())


async def list_events_for_actor(
    session: AsyncSession,
    actor_id: str,
    *,
    limit: int = 100,
    cursor: uuid.UUID | None = None,
) -> tuple[list[Event], uuid.UUID | None]:
    rows, next_cursor = await paginate_by_created_id(
        session,
        EventRow,
        where=[EventRow.actor_id == actor_id],
        limit=limit,
        cursor=cursor,
    )
    return [_to_event(r) for r in rows], next_cursor


async def list_events_for_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: uuid.UUID,
    *,
    limit: int = 100,
    cursor: uuid.UUID | None = None,
) -> tuple[list[Event], uuid.UUID | None]:
    rows, next_cursor = await paginate_by_created_id(
        session,
        EventRow,
        where=[EventRow.entity_type == entity_type, EventRow.entity_id == entity_id],
        limit=limit,
        cursor=cursor,
    )
    return [_to_event(r) for r in rows], next_cursor


async def list_events_recent(
    session: AsyncSession,
    *,
    jurisdictions: list[str] | None = None,
    event_types: list[str] | None = None,
    since: datetime | None = None,
    limit: int = 100,
    cursor: uuid.UUID | None = None,
) -> tuple[list[Event], uuid.UUID | None]:
    """Corpus event feed, newest first. When a filter is supplied, joins
    entity_id → law → jurisdiction so `jurisdictions` narrows correctly.
    Workspace-scope events (none today) resolve to NULL and are excluded
    whenever `jurisdictions` is set."""
    if not (jurisdictions or event_types or since):
        rows, next_cursor = await paginate_by_created_id(
            session,
            EventRow,
            limit=limit,
            cursor=cursor,
        )
        return [_to_event(r) for r in rows], next_cursor

    # Anchor lookup is unscoped: the cursor identifies a row the client
    # already saw at any filter, so it's fine (and cheaper) to fetch by id.
    # A stale cursor (row deleted) returns ([], None), the same shape as
    # "no more results" so the client stops paging.
    anchor_at: datetime | None = None
    anchor_id: uuid.UUID | None = None
    if cursor is not None:
        anchor_row = (
            await session.execute(
                text("SELECT created_at, id FROM events WHERE id = :id"), {"id": cursor}
            )
        ).first()
        if anchor_row is None:
            return [], None
        anchor_at, anchor_id = anchor_row

    # Build filter fragments so we never bind a NULL array. `expanding=True`
    # rewrites the IN-list at execute time so asyncpg gets one parameter per
    # element instead of a Python list.
    inner_where = ["1 = 1"]
    outer_where = ["1 = 1"]
    params: dict[str, Any] = {"limit": limit + 1}

    if since is not None:
        inner_where.append("e.created_at >= :since")
        params["since"] = since
    if event_types:
        inner_where.append("e.event_type IN :event_types")
        params["event_types"] = tuple(event_types)
    if jurisdictions:
        outer_where.append("j.code IN :jurisdictions")
        params["jurisdictions"] = tuple(jurisdictions)
    if anchor_at is not None:
        outer_where.append(
            "(r.created_at < :anchor_at OR (r.created_at = :anchor_at AND r.id < :anchor_id))"
        )
        params["anchor_at"] = anchor_at
        params["anchor_id"] = anchor_id

    sql = f"""
        WITH resolved AS (
          SELECT
            e.id, e.actor_id, e.entity_type, e.entity_id, e.event_type,
            e.payload, e.created_at,
            CASE
              WHEN e.entity_type = 'law' THEN e.entity_id
              WHEN e.entity_type = 'version' THEN (
                SELECT law_id FROM versions WHERE id = e.entity_id
              )
              WHEN e.entity_type = 'provision' THEN (
                SELECT v.law_id
                FROM provisions p
                JOIN versions v ON v.id = p.version_id
                WHERE p.id = e.entity_id
              )
            END AS derived_law_id
          FROM events e
          WHERE {" AND ".join(inner_where)}
        )
        SELECT
          r.id, r.actor_id, r.entity_type, r.entity_id, r.event_type,
          r.payload, r.created_at,
          j.code AS jurisdiction_code,
          l.title AS law_title,
          l.id AS law_id
        FROM resolved r
        LEFT JOIN laws l ON l.id = r.derived_law_id
        LEFT JOIN jurisdictions j ON j.id = l.jurisdiction_id
        WHERE {" AND ".join(outer_where)}
        ORDER BY r.created_at DESC, r.id DESC
        LIMIT :limit
    """  # noqa: S608
    stmt = text(sql)
    if event_types:
        stmt = stmt.bindparams(bindparam("event_types", expanding=True))
    if jurisdictions:
        stmt = stmt.bindparams(bindparam("jurisdictions", expanding=True))
    result = (await session.execute(stmt, params)).mappings().all()

    page = result[:limit]
    next_cursor = page[-1]["id"] if len(result) > limit else None
    events = [
        Event(
            id=r["id"],
            actor_id=r["actor_id"],
            entity_type=r["entity_type"],
            entity_id=r["entity_id"],
            event_type=r["event_type"],
            payload=r["payload"] or {},
            created_at=r["created_at"],
            jurisdiction_code=r["jurisdiction_code"],
            law_title=r["law_title"],
            law_id=r["law_id"],
        )
        for r in page
    ]
    return events, next_cursor


__all__ = [
    "Event",
    "list_events_for_actor",
    "list_events_for_entity",
    "list_events_recent",
    "save_event",
]
