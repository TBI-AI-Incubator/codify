"""Postgres rejects 0x00 in text outright, so an artefact carrying one kills the
run that produced it, after the model work is already paid for. The strip belongs
at the storage boundary rather than in each caller.

Integration: needs a migrated postgres (same skip rules as test_dossier_inputs)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from codify.storage.runs import create_run, get_artifact, save_artifact

pytestmark = pytest.mark.integration


def _postgres_url() -> str:
    raw = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _reachable(url: str) -> bool:
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
        async with engine.connect():
            pass
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    url = _postgres_url()
    if not await _reachable(url):
        pytest.skip(f"postgres not reachable at {url}")
    engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
    async with engine.connect() as c:
        if (await c.execute(text("SELECT to_regclass('run_artifacts')"))).scalar() is None:
            await engine.dispose()
            pytest.skip("schema not migrated; run alembic upgrade head")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _write(session: AsyncSession, body: str) -> str:
    run_id = uuid.uuid4()
    await create_run(session, id_=run_id, kind="ingest")
    artifact_id = await save_artifact(
        session, run_id=run_id, stage="structure", kind="bluebell", content_text=body
    )
    await session.commit()
    row = await get_artifact(session, artifact_id)
    assert row is not None
    return str(row.content_text)


async def test_a_nul_byte_does_not_kill_the_write(session: AsyncSession) -> None:
    """Without the strip this raises CharacterNotInRepertoireError and the run dies."""
    stored = await _write(session, "SEC 1\x00\nBody of section one.")
    assert "\x00" not in stored
    assert stored == "SEC 1\nBody of section one."


async def test_the_other_rejected_control_characters_go_too(session: AsyncSession) -> None:
    stored = await _write(session, "a\x01b\x08c\x0bd\x0ce\x1ff\x7fg")
    assert stored == "abcdefg"


async def test_provision_text_survives_intact(session: AsyncSession) -> None:
    """Newlines and tabs carry structure in Bluebell, so they must not be swept."""
    body = "SEC 1\n\tSubsection text.\r\nMore text."
    assert await _write(session, body) == body


async def _write_json(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.uuid4()
    await create_run(session, id_=run_id, kind="ingest")
    artifact_id = await save_artifact(
        session, run_id=run_id, stage="translate", kind="notes", content_json=payload
    )
    await session.commit()
    row = await get_artifact(session, artifact_id)
    assert row is not None
    return dict(row.content_json)


async def test_a_nul_in_json_does_not_kill_the_write(session: AsyncSession) -> None:
    """jsonb rejects 0x00 exactly as text does, and content_json carries model output."""
    stored = await _write_json(session, {"note": "clause\x00 one"})
    assert stored == {"note": "clause one"}


async def test_nested_values_and_keys_are_reached(session: AsyncSession) -> None:
    """The translation payload nests, so a top-level pass would leave the NUL in place."""
    stored = await _write_json(session, {"audit": {"fl\x00ags": ["a\x00b", {"why": "c\x00d"}]}})
    assert stored == {"audit": {"flags": ["ab", {"why": "cd"}]}}


async def test_non_string_json_values_survive(session: AsyncSession) -> None:
    stored = await _write_json(session, {"count": 3, "ok": True, "none": None})
    assert stored == {"count": 3, "ok": True, "none": None}
