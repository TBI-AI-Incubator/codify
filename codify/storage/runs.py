"""Durable-run lifecycle CRUD; RunRow.id is also the DBOS workflow id."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, cast, false, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from codify.storage.models import RunArtifactRow, RunRow

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


async def get_run(session: AsyncSession, id_: uuid.UUID) -> RunRow | None:
    result = await session.execute(select(RunRow).where(RunRow.id == id_))
    return result.scalar_one_or_none()


async def create_run(
    session: AsyncSession,
    *,
    id_: uuid.UUID,
    kind: str,
    actor_id: str | None = None,
    org_id: str | None = None,
    jurisdiction_code: str | None = None,
    params: dict[str, Any] | None = None,
    parent_run_id: uuid.UUID | None = None,
    object_key: str | None = None,
) -> RunRow:
    """Create a queued run. `org_id` is the workspace the run belongs to.

    A child inherits its parent's workspace, so a fan-out cannot end up more
    visible than the batch that started it. Omitting `org_id` fails closed: the
    run is visible only to its own actor and to platform admins.
    """
    if org_id is None and parent_run_id is not None:
        parent = await get_run(session, parent_run_id)
        org_id = parent.org_id if parent is not None else None
    row = RunRow(
        id=id_,
        kind=kind,
        status="queued",
        actor_id=actor_id,
        org_id=org_id,
        jurisdiction_code=jurisdiction_code,
        params=params or {},
        parent_run_id=parent_run_id,
        object_key=object_key,
    )
    session.add(row)
    await session.flush()
    return row


async def mark_running(
    session: AsyncSession, id_: uuid.UUID, *, image_ref: str | None = None
) -> bool:
    """Transition to running, recording the build that picked the run up.
    False when the row is already terminal (cancelled before the workflow started).

    Provisional: the terminal transition overwrites it, a run interrupted by a deploy
    being resumed and finished by the replacement build. None, from a deployment with
    no image reference to report, leaves any existing value alone rather than erasing
    it.
    """
    # One statement: a cancel committed between a read and a write would be
    # overwritten with running, and the workflow would then proceed.
    values: dict[str, Any] = {
        "status": "running",
        "started_at": func.coalesce(RunRow.started_at, datetime.now(UTC)),
    }
    if image_ref:
        values["image_ref"] = image_ref
    result = await session.execute(
        update(RunRow)
        .where(RunRow.id == id_, RunRow.status.notin_(TERMINAL_STATUSES))
        .values(**values)
    )
    # A mapped copy already in this session reads the write; nothing else is touched.
    for obj in list(session.identity_map.values()):
        if isinstance(obj, RunRow) and obj.id == id_:
            await session.refresh(obj)
    return bool(result.rowcount)


async def _mark_terminal(
    session: AsyncSession,
    id_: uuid.UUID,
    values: dict[str, Any],
    *,
    image_ref: str | None,
) -> bool:
    """Check the status under the UPDATE's row lock, including after a competing commit."""
    values["completed_at"] = datetime.now(UTC)
    if image_ref:
        values["image_ref"] = image_ref
    result = await session.execute(
        update(RunRow)
        .where(RunRow.id == id_, RunRow.status.not_in(TERMINAL_STATUSES))
        .values(**values)
        .returning(RunRow.id)
        .execution_options(synchronize_session="fetch")
    )
    return result.scalar_one_or_none() is not None


async def mark_succeeded(
    session: AsyncSession,
    id_: uuid.UUID,
    *,
    result: dict[str, Any] | None = None,
    tallies: dict[str, Any] | None = None,
    image_ref: str | None = None,
) -> bool:
    """Write success once; image_ref identifies the build that produced the output."""
    values: dict[str, Any] = {"status": "succeeded"}
    if result is not None:
        values["result"] = result
    if tallies is not None:
        values["tallies"] = tallies
    return await _mark_terminal(session, id_, values, image_ref=image_ref)


async def mark_failed(
    session: AsyncSession, id_: uuid.UUID, *, error: str, image_ref: str | None = None
) -> bool:
    """Write failure once; image_ref identifies the build the run failed on."""
    return await _mark_terminal(
        session, id_, {"status": "failed", "error": error[:2000]}, image_ref=image_ref
    )


async def mark_cancelled(
    session: AsyncSession, id_: uuid.UUID, *, image_ref: str | None = None
) -> bool:
    """Return whether this call changed the run to cancelled."""
    return await _mark_terminal(session, id_, {"status": "cancelled"}, image_ref=image_ref)


async def update_tallies(session: AsyncSession, id_: uuid.UUID, tallies: dict[str, Any]) -> None:
    row = await get_run(session, id_)
    if row is None:
        return
    row.tallies = tallies
    await session.flush()


# Postgres rejects 0x00 in text outright, and the rest of this range is no more
# meaningful in a stored artefact. Generated text carries them occasionally, and
# a run that dies here has already paid for the model work.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _scrub(value: Any) -> Any:
    """Strip control characters from every string in a nested payload.

    jsonb rejects 0x00 exactly as text does, and these payloads nest, so a
    top-level pass would leave a NUL in a nested note to kill the write.
    """
    if isinstance(value, str):
        return _CONTROL_CHARS_RE.sub("", value)
    if isinstance(value, dict):
        return {_scrub(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


async def save_artifact(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    stage: str,
    kind: str,
    content_text: str | None = None,
    content_json: dict[str, Any] | None = None,
) -> uuid.UUID:
    if content_text is not None:
        content_text = _CONTROL_CHARS_RE.sub("", content_text)
    if content_json is not None:
        content_json = _scrub(content_json)
    row = RunArtifactRow(
        run_id=run_id,
        stage=stage,
        kind=kind,
        content_text=content_text,
        content_json=content_json,
    )
    session.add(row)
    await session.flush()
    return row.id


async def get_artifact_json_slice(
    session: AsyncSession, *, id_: uuid.UUID, key: str, start: int, size: int
) -> list[dict[str, Any]]:
    """A window of one JSON array inside an artifact, sliced in the database.

    The admin fan-out reads its child specs a chunk at a time, and a batch of tens
    of thousands makes that array megabytes. Slicing server-side keeps each chunk
    proportional to the chunk rather than to the whole batch.
    """
    result = await session.execute(
        text(
            """
            SELECT e
            FROM run_artifacts a,
                 LATERAL jsonb_array_elements(a.content_json -> :key)
                     WITH ORDINALITY AS t(e, i)
            WHERE a.id = :id AND t.i > :start AND t.i <= :stop
            ORDER BY t.i
            """
        ),
        {"id": id_, "key": key, "start": start, "stop": start + size},
    )
    return [row[0] for row in result]


async def get_artifact(session: AsyncSession, id_: uuid.UUID) -> RunArtifactRow | None:
    result = await session.execute(select(RunArtifactRow).where(RunArtifactRow.id == id_))
    return result.scalar_one_or_none()


async def get_latest_artifact_by_kind(
    session: AsyncSession, *, run_id: uuid.UUID, kind: str
) -> RunArtifactRow | None:
    """Latest artifact row for `(run_id, kind)`, newest by `created_at`. Backs
    the discovery read endpoint that needs to find the persisted ranked list."""
    result = await session.execute(
        select(RunArtifactRow)
        .where(RunArtifactRow.run_id == run_id)
        .where(RunArtifactRow.kind == kind)
        .order_by(RunArtifactRow.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_runs(
    session: AsyncSession,
    *,
    kind: str | None = None,
    status: str | None = None,
    jurisdiction_code: str | None = None,
    jurisdiction_codes: list[str] | None = None,
    unattributed_actor_ids: tuple[str, ...] = (),
    unattributed_visible: bool = False,
    org_scope: str | None = None,
    org_unrestricted: bool = True,
    actor_id: str | None = None,
    parent_run_id: uuid.UUID | None = None,
    top_level: bool = False,
    params_contains: dict[str, Any] | None = None,
    cursor: datetime | None = None,
    limit: int = 50,
) -> tuple[list[RunRow], datetime | None]:
    """Newest-first, limit+1 cursor; params_contains = JSONB containment."""
    stmt = select(RunRow).order_by(RunRow.created_at.desc())
    if actor_id is not None:
        stmt = stmt.where(func.lower(RunRow.actor_id) == actor_id.strip().lower())
    if kind is not None:
        stmt = stmt.where(RunRow.kind == kind)
    if status is not None:
        stmt = stmt.where(RunRow.status == status)
    if jurisdiction_code is not None:
        stmt = stmt.where(RunRow.jurisdiction_code == jurisdiction_code)
    if jurisdiction_codes is not None:
        # An unattributed run (an admin batch naming its targets outright) is
        # its actor's to see, not everyone's. Mirrors the detail check, which
        # used to admit every caller because the empty code short-circuited it.
        if unattributed_visible:
            unattributed = RunRow.jurisdiction_code.is_(None)
        elif unattributed_actor_ids:
            # Every identity `actor_id` could hold for this actor: a token with
            # no email is stamped with its subject.
            unattributed = and_(
                RunRow.jurisdiction_code.is_(None), RunRow.actor_id.in_(unattributed_actor_ids)
            )
        else:
            unattributed = false()
        stmt = stmt.where(or_(unattributed, RunRow.jurisdiction_code.in_(jurisdiction_codes)))
    if not org_unrestricted:
        # Jurisdiction alone cannot scope work product: two workspaces can hold
        # the same one. A run with no workspace predates migration 0124 and stays
        # with the actor who started it rather than becoming visible to a
        # workspace that merely shares its jurisdiction, so `org_scope=None` must
        # not read as "no restriction".
        own_legacy = (
            and_(RunRow.org_id.is_(None), func.lower(RunRow.actor_id).in_(unattributed_actor_ids))
            if unattributed_actor_ids
            else false()
        )
        mine = RunRow.org_id == org_scope if org_scope is not None else false()
        stmt = stmt.where(or_(mine, own_legacy))
    if parent_run_id is not None:
        stmt = stmt.where(RunRow.parent_run_id == parent_run_id)
    if top_level:
        stmt = stmt.where(RunRow.parent_run_id.is_(None))
    if params_contains:
        stmt = stmt.where(RunRow.params.op("@>")(params_contains))
    if cursor is not None:
        stmt = stmt.where(RunRow.created_at < cursor)
    rows = list((await session.execute(stmt.limit(limit + 1))).scalars().all())
    next_cursor = rows[limit - 1].created_at if len(rows) > limit else None
    return rows[:limit], next_cursor


async def claim_batch_index(session: AsyncSession, batch_id: uuid.UUID) -> int:
    """Atomically increment params.item_count; returns the claimed 0-based index."""
    from sqlalchemy import update as sa_update

    result = await session.execute(
        sa_update(RunRow)
        .where(RunRow.id == batch_id)
        .values(
            params=RunRow.params.op("||")(
                func.jsonb_build_object(
                    "item_count",
                    func.coalesce(RunRow.params["item_count"].as_integer(), 0) + 1,
                )
            )
        )
        .returning(RunRow.params["item_count"].as_integer())
    )
    return int(result.scalar_one()) - 1


async def seal_batch(session: AsyncSession, batch_id: uuid.UUID) -> None:
    from sqlalchemy import update as sa_update

    await session.execute(
        sa_update(RunRow)
        .where(RunRow.id == batch_id)
        .values(params=RunRow.params.op("||")({"sealed": True}))
    )
    await session.flush()


async def merge_run_params(
    session: AsyncSession, id_: uuid.UUID, patch: dict[str, Any]
) -> dict[str, Any]:
    """Merge `patch` into a run's params in the database and return the result.
    The merge is one JSONB update, so a concurrent writer's keys survive."""
    result = await session.execute(
        update(RunRow)
        .where(RunRow.id == id_)
        .values(params=RunRow.params.op("||")(cast(patch, JSONB)))
        .returning(RunRow.params)
    )
    merged = result.scalar_one_or_none()
    if merged is None:
        raise RuntimeError(f"run {id_} row missing")
    return dict(merged)


async def reopen_unstarted_run(session: AsyncSession, id_: uuid.UUID) -> bool:
    """Put a failed run that never started back to queued, so a retried fetch
    can hand off to it; False for a cancelled run, which stays cancelled. A run
    that ran keeps its record: retry that run instead."""
    # One conditional write: a cancel committed between a read and a write would
    # otherwise be resurrected to queued and enqueued.
    result = await session.execute(
        update(RunRow)
        .where(RunRow.id == id_, RunRow.status == "failed", RunRow.started_at.is_(None))
        .values(status="queued", error=None, completed_at=None, image_ref=None)
    )
    for obj in list(session.identity_map.values()):
        if isinstance(obj, RunRow) and obj.id == id_:
            await session.refresh(obj)
    if result.rowcount:
        return True
    row = await get_run(session, id_)
    if row is None:
        raise RuntimeError(f"run {id_} row missing")
    if row.status == "queued":
        return True
    if row.status == "cancelled":
        return False
    raise RuntimeError(
        f"run {id_} is {row.status} and already ran; retry it rather than re-fetching for it"
    )


async def set_batch_item_count(session: AsyncSession, batch_id: uuid.UUID, count: int) -> None:
    """Overwrite params.item_count; used to reconcile after partial fan-out."""
    from sqlalchemy import update as sa_update

    await session.execute(
        sa_update(RunRow)
        .where(RunRow.id == batch_id)
        .values(params=RunRow.params.op("||")({"item_count": count}))
    )
    await session.flush()


async def list_children_of_parent(session: AsyncSession, parent_run_id: uuid.UUID) -> list[RunRow]:
    """Every child of one parent, oldest first. Uncapped.

    `list_runs` is newest-first with a limit, so a capped read of a large
    fan-out lets a parent seal while unseen children are still queued.
    """
    stmt = (
        select(RunRow)
        .where(RunRow.parent_run_id == parent_run_id)
        # id breaks the created_at tie; an unstable order reshuffles the stream.
        .order_by(RunRow.created_at.asc(), RunRow.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_running_children_of_parent_kind(session: AsyncSession, parent_kind: str) -> int:
    """Children currently running under any parent of `parent_kind`.

    Queue membership follows the parent, not the child: every admin child is
    enqueued on ADMIN_BATCH_QUEUE whatever its own kind, so counting by child
    kind measures the wrong thing."""
    parent = aliased(RunRow)
    stmt = (
        select(func.count())
        .select_from(RunRow)
        .join(parent, RunRow.parent_run_id == parent.id)
        .where(RunRow.status == "running")
        .where(parent.kind == parent_kind)
    )
    return int((await session.execute(stmt)).scalar_one())


async def count_runs(
    session: AsyncSession,
    *,
    kinds: list[str] | None = None,
    status: str | None = None,
    parent_run_id: uuid.UUID | None = None,
    jurisdiction_codes: list[str] | None = None,
    unattributed_actor_ids: tuple[str, ...] = (),
    unattributed_visible: bool = False,
) -> int:
    """Unbounded count matching the same filter surface as `list_runs`."""
    stmt = select(func.count()).select_from(RunRow)
    if kinds is not None:
        stmt = stmt.where(RunRow.kind.in_(kinds))
    if status is not None:
        stmt = stmt.where(RunRow.status == status)
    if parent_run_id is not None:
        stmt = stmt.where(RunRow.parent_run_id == parent_run_id)
    if jurisdiction_codes is not None:
        if unattributed_visible:
            unattributed = RunRow.jurisdiction_code.is_(None)
        elif unattributed_actor_ids:
            # Every identity `actor_id` could hold for this actor: a token with
            # no email is stamped with its subject.
            unattributed = and_(
                RunRow.jurisdiction_code.is_(None), RunRow.actor_id.in_(unattributed_actor_ids)
            )
        else:
            unattributed = false()
        stmt = stmt.where(or_(unattributed, RunRow.jurisdiction_code.in_(jurisdiction_codes)))
    result = await session.execute(stmt)
    return int(result.scalar_one())


__all__ = [
    "TERMINAL_STATUSES",
    "claim_batch_index",
    "seal_batch",
    "set_batch_item_count",
    "count_running_children_of_parent_kind",
    "count_runs",
    "create_run",
    "get_artifact",
    "get_latest_artifact_by_kind",
    "get_run",
    "list_children_of_parent",
    "list_runs",
    "mark_cancelled",
    "mark_failed",
    "mark_running",
    "mark_succeeded",
    "save_artifact",
    "update_tallies",
]
