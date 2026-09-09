"""Integration tests for the `finding_notes` storage helper."""

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

from codify.storage.finding_notes import (
    delete_notes_for_version,
    get_note,
    get_notes_for_findings,
    upsert_note,
)
from codify.storage.models import FindingRow, Jurisdiction, Law, Version

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
    if not await _table_exists(engine, "finding_notes"):
        await engine.dispose()
        pytest.skip("finding_notes table not migrated; run alembic upgrade head")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _seed_version(session: AsyncSession) -> uuid.UUID:
    """Real jurisdiction/law/version rows so findings.version_id FK resolves."""
    from datetime import date

    suffix = uuid.uuid4().hex[:8]
    j = Jurisdiction(code=f"zz-{suffix}", name="Test", languages=["en"])
    session.add(j)
    await session.flush()
    law = Law(
        jurisdiction_id=j.id,
        title=f"Notes Act {suffix}",
        doctype="act",
        frbr_work_uri=f"/akn/zz/{suffix}/2026/1",
    )
    session.add(law)
    await session.flush()
    v = Version(
        law_id=law.id,
        expression_uri=f"/akn/zz/{suffix}/2026/1/eng@2026-01-01",
        language="en",
        expression_date=date(2026, 1, 1),
        akn_xml="<akomaNtoso/>",
    )
    session.add(v)
    await session.flush()
    return v.id


async def _insert_finding(session: AsyncSession, *, version_id: uuid.UUID) -> uuid.UUID:
    """Minimal FindingRow so the FK from `finding_notes` resolves."""
    fid = uuid.uuid4()
    row = FindingRow(
        id=fid,
        lens_run_id=uuid.uuid4(),
        lens_name="eu_acquis",
        version_id=version_id,
        provision_id=None,
        provision_eid="art_1__para_1",
        severity="medium",
        confidence=0.8,
        rationale="test rationale",
        recommendation=None,
        payload={"verdict": "partial"},
    )
    session.add(row)
    await session.flush()
    return fid


async def test_upsert_then_read(session: AsyncSession) -> None:
    version_id = await _seed_version(session)
    fid = await _insert_finding(session, version_id=version_id)

    await upsert_note(session, fid, "1.0", "First version of the note.")
    await session.flush()

    body = await get_note(session, fid, "1.0")
    assert body == "First version of the note."


async def test_upsert_is_idempotent_for_same_version(session: AsyncSession) -> None:
    """Retry of same (finding, version) overwrites, doesn't duplicate."""
    version_id = await _seed_version(session)
    fid = await _insert_finding(session, version_id=version_id)

    await upsert_note(session, fid, "1.0", "draft")
    await upsert_note(session, fid, "1.0", "final")
    await session.flush()

    body = await get_note(session, fid, "1.0")
    assert body == "final"


async def test_prompt_version_separates_rows(session: AsyncSession) -> None:
    version_id = await _seed_version(session)
    fid = await _insert_finding(session, version_id=version_id)

    await upsert_note(session, fid, "1.0", "v1 text")
    await upsert_note(session, fid, "1.1", "v1.1 text")
    await session.flush()

    assert await get_note(session, fid, "1.0") == "v1 text"
    assert await get_note(session, fid, "1.1") == "v1.1 text"


async def test_batch_read_returns_dict_keyed_on_finding_id(session: AsyncSession) -> None:
    version_id = await _seed_version(session)
    f1 = await _insert_finding(session, version_id=version_id)
    f2 = await _insert_finding(session, version_id=version_id)
    f3 = await _insert_finding(session, version_id=version_id)  # left unwarmed

    await upsert_note(session, f1, "1.0", "note 1")
    await upsert_note(session, f2, "1.0", "note 2")
    await session.flush()

    out = await get_notes_for_findings(session, [f1, f2, f3], "1.0")
    assert out == {f1: "note 1", f2: "note 2"}


async def test_delete_notes_for_version_cascades_at_application_layer(
    session: AsyncSession,
) -> None:
    v_keep = await _seed_version(session)
    v_drop = await _seed_version(session)
    f_keep = await _insert_finding(session, version_id=v_keep)
    f_drop = await _insert_finding(session, version_id=v_drop)
    await upsert_note(session, f_keep, "1.0", "stays")
    await upsert_note(session, f_drop, "1.0", "purged")
    await session.flush()

    purged = await delete_notes_for_version(session, v_drop)
    await session.flush()

    assert purged == 1
    assert await get_note(session, f_keep, "1.0") == "stays"
    assert await get_note(session, f_drop, "1.0") is None


async def test_finding_delete_cascades_to_notes(session: AsyncSession) -> None:
    """ON DELETE CASCADE."""
    version_id = await _seed_version(session)
    fid = await _insert_finding(session, version_id=version_id)
    await upsert_note(session, fid, "1.0", "doomed")
    await session.flush()

    await session.execute(text("DELETE FROM findings WHERE id = :id"), {"id": str(fid)})
    await session.flush()

    assert await get_note(session, fid, "1.0") is None
