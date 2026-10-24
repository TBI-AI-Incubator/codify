"""The lexicon prune scans unlocked, then re-checks its candidates under a lock.

A term must never be pruned while a provision or title carries it, including
one a writer adds between the scan and the re-check.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from codify.storage import lexicon
from codify.storage.jurisdictions import get_or_create_jurisdiction
from codify.storage.models import Law, Provision, Version
from codify.testing import postgres_url

pytestmark = pytest.mark.integration

_CORE_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
_LOCK_NOT_AVAILABLE = "55P03"


def _sync(url: str) -> str:
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def scratch_url() -> Iterator[str]:
    """A freshly migrated database of its own: the prune reads the whole corpus."""
    base = postgres_url()
    admin = create_engine(_sync(base), isolation_level="AUTOCOMMIT")
    try:
        admin.connect().close()
    except Exception:
        admin.dispose()
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail("REQUIRE_DB=1 but POSTGRES_URL is not reachable")
        pytest.skip("POSTGRES_URL not reachable")
    name = f"corelex_{uuid.uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    previous = os.environ.get("POSTGRES_URL")
    os.environ["POSTGRES_URL"] = _sync(url)
    try:
        command.upgrade(Config(str(_CORE_INI)), "head")
    finally:
        if previous is None:
            os.environ.pop("POSTGRES_URL", None)
        else:
            os.environ["POSTGRES_URL"] = previous
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
async def engine(scratch_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(scratch_url)
    yield eng
    await eng.dispose()


def _word() -> str:
    return "zq" + uuid.uuid4().hex[:10]


async def _seed(session: AsyncSession, tokens: str) -> tuple[uuid.UUID, uuid.UUID]:
    """One law with one provision carrying `tokens`; returns (law id, provision id)."""
    code = "x" + uuid.uuid4().hex[:6]
    jurisdiction = await get_or_create_jurisdiction(session, code, languages=["en"])
    law = Law(
        jurisdiction_id=jurisdiction.id,
        title="Act",
        doctype="act",
        frbr_work_uri=f"/akn/{code}/act/2026/1",
    )
    session.add(law)
    await session.flush()
    version = Version(
        law_id=law.id,
        expression_uri=f"/akn/{code}/act/2026/1/eng@2026-01-01",
        language="en",
        expression_date=date(2026, 1, 1),
        akn_xml="<akomaNtoso/>",
    )
    session.add(version)
    await session.flush()
    provision = Provision(
        version_id=version.id,
        akn_eid="sec_1",
        akn_wid="sec_1",
        akn_type="section",
        text="Invented text.",
        position=1,
        search_tokens=tokens,
    )
    session.add(provision)
    await session.flush()
    return law.id, provision.id


async def _add_term(session: AsyncSession, term: str) -> None:
    await session.execute(
        text("INSERT INTO search_terms (term, ndoc) VALUES (:t, 1) ON CONFLICT DO NOTHING"),
        {"t": term},
    )


async def _present(engine: AsyncEngine, term: str) -> bool:
    async with engine.connect() as conn:
        found = await conn.execute(text("SELECT 1 FROM search_terms WHERE term = :t"), {"t": term})
        return found.first() is not None


async def _prune(engine: AsyncEngine) -> int:
    async with async_sessionmaker(engine)() as s:
        removed = await lexicon.prune_dead_terms(s)
        await s.commit()
        return removed


async def test_a_dead_term_goes_and_carried_terms_stay(engine: AsyncEngine) -> None:
    carried, titled, dead = _word(), _word(), _word()
    async with async_sessionmaker(engine)() as s:
        law_id, _ = await _seed(s, f"alpha {carried} omega")
        await s.execute(
            text("UPDATE laws SET title_tokens = :t WHERE id = :id"), {"t": titled, "id": law_id}
        )
        for term in (carried, titled, dead):
            await _add_term(s, term)
        await s.commit()

    assert await _prune(engine) >= 1
    assert await _present(engine, carried)
    assert await _present(engine, titled)
    assert not await _present(engine, dead)


# A word, and one the 'simple' parser finds no lexeme in, which the GIN cannot see.
@pytest.mark.parametrize("term", ["word", "́"])
@pytest.mark.parametrize("carrier", ["provision", "title"])
async def test_a_term_written_between_scan_and_recheck_survives(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, term: str, carrier: str
) -> None:
    term = _word() if term == "word" else term
    async with async_sessionmaker(engine)() as s:
        law_id, provision_id = await _seed(s, "alpha")
        await s.execute(text("DELETE FROM search_terms WHERE term = :t"), {"t": term})
        await _add_term(s, term)
        await s.commit()

    scan: Callable[[AsyncSession], Awaitable[list[str]]] = lexicon._dead_term_candidates
    proposed: list[str] = []

    async def scan_then_write(session: AsyncSession) -> list[str]:
        candidates = await scan(session)
        proposed.extend(candidates)
        async with async_sessionmaker(engine)() as writer:
            if carrier == "provision":
                await writer.execute(
                    text("UPDATE provisions SET search_tokens = :t WHERE id = :id"),
                    {"t": f"alpha {term}", "id": provision_id},
                )
            else:
                await writer.execute(
                    text("UPDATE laws SET title_tokens = :t WHERE id = :id"),
                    {"t": term, "id": law_id},
                )
            await writer.commit()
        return candidates

    monkeypatch.setattr(lexicon, "_dead_term_candidates", scan_then_write)
    try:
        await _prune(engine)
        # The scan did propose it, so its survival is the re-check's doing.
        assert term in proposed
        assert await _present(engine, term)
    finally:
        async with async_sessionmaker(engine)() as s:
            await s.execute(text("DELETE FROM laws WHERE id = :id"), {"id": law_id})
            await s.commit()


async def test_an_uncommitted_writer_refuses_the_sweep(engine: AsyncEngine) -> None:
    term = _word()
    async with async_sessionmaker(engine)() as s:
        await _add_term(s, term)
        await s.commit()
    writer = async_sessionmaker(engine)()
    await writer.begin()
    await _seed(writer, term)
    try:
        with pytest.raises(DBAPIError) as refused:
            await _prune(engine)
        assert getattr(refused.value.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE
    finally:
        await writer.commit()
        await writer.close()
    assert await _present(engine, term)


async def test_ddl_refuses_the_sweep_rather_than_queueing_it(engine: AsyncEngine) -> None:
    holder = async_sessionmaker(engine)()
    await holder.begin()
    await holder.execute(text("LOCK TABLE laws IN ACCESS EXCLUSIVE MODE"))
    try:
        with pytest.raises(DBAPIError) as refused:
            await _prune(engine)
        assert getattr(refused.value.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE
    finally:
        await holder.rollback()
        await holder.close()


async def test_a_snapshot_isolation_refuses(engine: AsyncEngine) -> None:
    """Under REPEATABLE READ the re-check would read the scan's snapshot."""
    async with async_sessionmaker(engine)() as s:
        await s.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        with pytest.raises(RuntimeError, match="read committed"):
            await lexicon.prune_dead_terms(s)
