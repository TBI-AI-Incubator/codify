from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from codify.lenses.types import SchemeMatch
from codify.storage.scheme_matches import (
    delete_scheme_matches_for_run,
    list_scheme_matches_for_run,
    list_scheme_matches_for_version,
    save_scheme_matches,
)

pytestmark = pytest.mark.integration


def _postgres_url() -> str:
    raw = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _table_exists(engine: AsyncEngine, name: str) -> bool:
    async with engine.connect() as c:
        result = await c.execute(text("SELECT to_regclass(:n)"), {"n": name})
        return result.scalar() is not None


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    url = _postgres_url()
    engine = create_async_engine(url, pool_pre_ping=True)
    if not await _table_exists(engine, "scheme_matches"):
        await engine.dispose()
        pytest.skip(
            "scheme_matches table not migrated; run alembic upgrade head against the test DB"
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _lens_run(session: AsyncSession) -> uuid.UUID:
    """A real parent row, with the version `lens_runs` requires.

    `scheme_matches.lens_run_id` is a foreign key, so a fabricated id no longer
    inserts."""
    run_id, law_id, version_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    code = f"z{uuid.uuid4().hex[:4]}"
    work = f"/akn/{code}/act/2099/{uuid.uuid4().hex[:8]}"
    await session.execute(
        text("INSERT INTO jurisdictions (id, code, name) VALUES (:id, :code, :code)"),
        {"id": uuid.uuid4(), "code": code},
    )
    await session.execute(
        text(
            "INSERT INTO laws (id, jurisdiction_id, frbr_work_uri, title, doctype) "
            "SELECT :lid, j.id, :work, 'T', 'act' FROM jurisdictions j WHERE j.code = :code"
        ),
        {"lid": law_id, "work": work, "code": code},
    )
    await session.execute(
        text(
            "INSERT INTO versions "
            "(id, law_id, language, expression_uri, expression_date, akn_xml) "
            "VALUES (:vid, :lid, 'ara', :expr, DATE '2099-01-01', '<akomaNtoso/>')"
        ),
        {"vid": version_id, "lid": law_id, "expr": f"{work}/ara@2099-01-01"},
    )
    await session.execute(
        text(
            "INSERT INTO lens_runs (id, version_id, lens_name, status) "
            "VALUES (:id, :vid, 'example', 'succeeded')"
        ),
        {"id": run_id, "vid": version_id},
    )
    return run_id


def _match(scheme_id: str, *, confidence: float = 0.6) -> SchemeMatch:
    return SchemeMatch(
        lens_name="example",
        scheme_id=scheme_id,
        confidence=confidence,
        findings=[uuid.uuid4(), uuid.uuid4()],
        rationale=f"rationale for {scheme_id}",
    )


async def test_save_and_list_round_trips(session: AsyncSession) -> None:
    run_id = await _lens_run(session)
    matches = [_match("scheme-a", confidence=0.7), _match("scheme-b", confidence=0.5)]
    await save_scheme_matches(session, matches, lens_run_id=run_id)
    await session.flush()

    out = await list_scheme_matches_for_run(session, run_id)
    assert [m.scheme_id for m in out] == [
        "scheme-a",
        "scheme-b",
    ]  # sorted by confidence desc
    assert all(m.lens_name == "example" for m in out)
    out_by_id = {m.scheme_id: m for m in out}
    for original in matches:
        recovered = out_by_id[original.scheme_id]
        assert recovered.findings == original.findings  # UUID list survives JSONB round-trip
        assert recovered.rationale == original.rationale
        assert recovered.confidence == pytest.approx(original.confidence)


async def test_delete_only_targets_named_run(session: AsyncSession) -> None:
    run_a = await _lens_run(session)
    run_b = await _lens_run(session)
    await save_scheme_matches(session, [_match("a")], lens_run_id=run_a)
    await save_scheme_matches(session, [_match("b")], lens_run_id=run_b)
    await session.flush()

    deleted = await delete_scheme_matches_for_run(session, run_a)
    assert deleted == 1

    remaining_a = await list_scheme_matches_for_run(session, run_a)
    remaining_b = await list_scheme_matches_for_run(session, run_b)
    assert remaining_a == []
    assert len(remaining_b) == 1


async def test_save_empty_list_is_no_op(session: AsyncSession) -> None:
    run_id = await _lens_run(session)
    await save_scheme_matches(session, [], lens_run_id=run_id)
    await session.flush()
    assert await list_scheme_matches_for_run(session, run_id) == []


async def test_list_scheme_matches_for_version_returns_empty_without_findings(
    session: AsyncSession,
) -> None:
    """No findings → no run resolvable → [] without error."""
    out = await list_scheme_matches_for_version(session, uuid.uuid4(), "example")
    assert out == []


async def test_matches_go_with_their_run(session: AsyncSession) -> None:
    """The foreign key exists so a deleted run cannot leave matches behind."""
    run_id = await _lens_run(session)
    await save_scheme_matches(session, [_match("orphan-check")], lens_run_id=run_id)
    await session.flush()
    assert len(await list_scheme_matches_for_run(session, run_id)) == 1

    await session.execute(text("DELETE FROM lens_runs WHERE id = :id"), {"id": run_id})
    await session.flush()
    # Read past the identity map: the cascade happened in the database.
    session.expire_all()
    left = await session.execute(
        text("SELECT count(*) FROM scheme_matches WHERE lens_run_id = :id"),
        {"id": run_id},
    )
    assert left.scalar_one() == 0
