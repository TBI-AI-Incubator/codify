"""A retried fetch hands off to the ingest child its first attempt failed."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.storage import (
    create_run,
    get_run,
    mark_cancelled,
    mark_failed,
    mark_running,
    merge_run_params,
    reopen_unstarted_run,
)

pytestmark = pytest.mark.integration


def _url() -> str:
    raw = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    # Nothing commits: the rollback at the end is the cleanup.
    engine = create_async_engine(_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _reread(session: AsyncSession, id_: uuid.UUID):  # type: ignore[no-untyped-def]
    # The reopen is a raw UPDATE; the mapped object is stale until expired.
    session.expire_all()
    row = await get_run(session, id_)
    assert row is not None
    return row


async def test_a_failed_child_that_never_ran_reopens_and_takes_the_fetch(
    session: AsyncSession,
) -> None:
    id_ = uuid.uuid4()
    await create_run(session, id_=id_, kind="ingest", params={"a": 1})
    await mark_failed(session, id_, error="acquire failed: portal down")
    await session.flush()

    assert await reopen_unstarted_run(session, id_) is True
    merged = await merge_run_params(session, id_, {"object_key": "k"})
    row = await _reread(session, id_)
    assert (row.status, row.error, row.completed_at) == ("queued", None, None)
    assert row.params == {"a": 1, "object_key": "k"} == merged


async def test_a_failed_child_that_ran_is_not_reopened(session: AsyncSession) -> None:
    id_ = uuid.uuid4()
    await create_run(session, id_=id_, kind="ingest", params={})
    await mark_running(session, id_)
    await mark_failed(session, id_, error="extract: boom")
    await session.flush()
    with pytest.raises(RuntimeError, match="already ran"):
        await reopen_unstarted_run(session, id_)


async def test_a_queued_child_is_left_alone(session: AsyncSession) -> None:
    id_ = uuid.uuid4()
    await create_run(session, id_=id_, kind="ingest", params={})
    await session.flush()
    assert await reopen_unstarted_run(session, id_) is True
    assert (await _reread(session, id_)).status == "queued"


async def test_a_cancelled_child_stays_cancelled_and_takes_no_fetch(
    session: AsyncSession,
) -> None:
    id_ = uuid.uuid4()
    await create_run(session, id_=id_, kind="ingest", params={})
    await mark_cancelled(session, id_)
    await session.flush()
    assert await reopen_unstarted_run(session, id_) is False
    assert (await _reread(session, id_)).status == "cancelled"


async def test_merge_keeps_keys_a_concurrent_writer_added(session: AsyncSession) -> None:
    """The merge is one JSONB update: a key written between read and write survives."""
    from sqlalchemy import text

    id_ = uuid.uuid4()
    await create_run(session, id_=id_, kind="ingest", params={"a": 1})
    await session.flush()
    await session.execute(
        text("UPDATE runs SET params = params || '{\"b\": 2}'::jsonb WHERE id = :id"), {"id": id_}
    )
    merged = await merge_run_params(session, id_, {"c": 3})
    assert merged == {"a": 1, "b": 2, "c": 3}


async def test_mark_running_is_one_conditional_write(session: AsyncSession) -> None:
    """A row cancelled before the workflow opens stays cancelled and reports so;
    a queued row opens and keeps its first start time on a second open."""
    id_ = uuid.uuid4()
    await create_run(session, id_=id_, kind="ingest", params={})
    await mark_cancelled(session, id_)
    await session.flush()
    assert await mark_running(session, id_) is False
    assert (await _reread(session, id_)).status == "cancelled"

    other = uuid.uuid4()
    await create_run(session, id_=other, kind="ingest", params={})
    await session.flush()
    assert await mark_running(session, other, image_ref="img:1") is True
    first = await _reread(session, other)
    assert first.status == "running" and first.started_at is not None and first.image_ref == "img:1"
    assert await mark_running(session, other) is True
    assert (await _reread(session, other)).started_at == first.started_at


@pytest.fixture
async def other() -> AsyncIterator[AsyncSession]:
    """A second connection, for a cancel that lands between another session's read and write."""
    engine = create_async_engine(_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _committed_row(session: AsyncSession, other: AsyncSession, **kw: object) -> uuid.UUID:
    id_ = uuid.uuid4()
    await create_run(session, id_=id_, kind="ingest", params={})
    if kw.get("failed"):
        await mark_failed(session, id_, error="acquire failed: portal down")
    await session.commit()
    return id_


async def test_a_cancel_landing_between_read_and_open_is_not_overwritten(
    session: AsyncSession, other: AsyncSession
) -> None:
    id_ = await _committed_row(session, other)
    try:
        loaded = await get_run(session, id_)  # this session now holds the row as queued
        assert loaded is not None and loaded.status == "queued"
        await mark_cancelled(other, id_)
        await other.commit()
        assert await mark_running(session, id_) is False
        await session.commit()
        other.expire_all()
        assert (await get_run(other, id_)).status == "cancelled"  # type: ignore[union-attr]
    finally:
        from sqlalchemy import text

        # This session's write attempt holds the row's lock until it ends.
        await session.rollback()
        await other.execute(text("DELETE FROM runs WHERE id = :id"), {"id": id_})
        await other.commit()


async def test_a_reopen_racing_another_writer_takes_the_row_as_it_is_committed(
    session: AsyncSession, other: AsyncSession
) -> None:
    """The reopen is one conditional write against the committed row, not this
    session's stale read. A cancel cannot land on a failed row (cancel is
    write-once too), so the other writer here is a retry marking it running."""
    from sqlalchemy import text

    id_ = await _committed_row(session, other, failed=True)
    try:
        loaded = await get_run(session, id_)  # this session sees the row as failed
        assert loaded is not None and loaded.status == "failed"
        await mark_cancelled(other, id_)
        await other.commit()
        other.expire_all()
        assert (await get_run(other, id_)).status == "failed"  # type: ignore[union-attr]
        # Another writer picks the row up between this session's read and its write.
        await other.execute(
            text("UPDATE runs SET status = 'running', started_at = now() WHERE id = :id"),
            {"id": id_},
        )
        await other.commit()
        with pytest.raises(RuntimeError, match="already ran"):
            await reopen_unstarted_run(session, id_)
        await session.rollback()
        other.expire_all()
        assert (await get_run(other, id_)).status == "running"  # type: ignore[union-attr]
    finally:
        from sqlalchemy import text

        # This session's write attempt holds the row's lock until it ends.
        await session.rollback()
        await other.execute(text("DELETE FROM runs WHERE id = :id"), {"id": id_})
        await other.commit()
