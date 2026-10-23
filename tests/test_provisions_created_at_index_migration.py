"""Migration 0026 serves the per-version `max(created_at)` from an index."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration

_CORE_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_BASE_URL = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
_NEW = "provisions_version_id_created_at_idx"
_OLD = "provisions_version_id_idx"
_MAX_SQL = "SELECT max(created_at) FROM provisions WHERE version_id = :v"


def _sync(url: str) -> str:
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _reachable(url: str) -> bool:
    engine = create_engine(_sync(url))
    try:
        engine.connect().close()
    except Exception:
        return False
    finally:
        engine.dispose()
    return True


@pytest.fixture
def temp_db() -> Iterator[str]:
    if not _reachable(_BASE_URL):
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail("REQUIRE_DB=1 but POSTGRES_URL is not reachable")
        pytest.skip("POSTGRES_URL not reachable")
    name = f"coreidx_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_sync(_BASE_URL), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(_BASE_URL).set(database=name).render_as_string(hide_password=False)
    finally:
        os.environ["POSTGRES_URL"] = _BASE_URL
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _run(url: str, fn, *args: str) -> None:
    os.environ["POSTGRES_URL"] = url
    fn(Config(str(_CORE_INI)), *args)


def _indexes(url: str) -> set[str]:
    engine = create_engine(_sync(url))
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'provisions'")
            )
            return {r[0] for r in rows}
    finally:
        engine.dispose()


def test_upgrade_swaps_the_index_and_downgrade_restores_it(temp_db: str) -> None:
    _run(temp_db, command.upgrade, "head")
    after = _indexes(temp_db)
    assert _NEW in after and _OLD not in after

    _run(temp_db, command.downgrade, "-1")
    before = _indexes(temp_db)
    assert _OLD in before and _NEW not in before

    _run(temp_db, command.upgrade, "head")
    assert _NEW in _indexes(temp_db)


def test_max_created_at_for_a_version_is_an_index_descent(temp_db: str) -> None:
    _run(temp_db, command.upgrade, "head")
    engine = create_engine(_sync(temp_db))
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                r[0]
                for r in conn.execute(text("EXPLAIN " + _MAX_SQL), {"v": uuid.uuid4()}).fetchall()
            )
    finally:
        engine.dispose()
    # With seqscan off the planner picks the cheapest index; only the new one holds created_at.
    assert _NEW in plan, plan
