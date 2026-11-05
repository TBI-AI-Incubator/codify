"""0028 lets a goods code be a two-digit chapter and records a carve-out; the
downgrade restores the four-digit floor."""

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
_REVISION = "0028_goods_code_scope"
_PREVIOUS = "0027_query_index_hygiene"
_CHECK = (
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conname = 'goods_code_references_code_digits'"
)
_COLUMN = (
    "SELECT column_default, is_nullable FROM information_schema.columns "
    "WHERE table_name = 'goods_code_references' AND column_name = 'excluded'"
)


def _sync(url: str) -> str:
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


@pytest.fixture
def temp_db() -> Iterator[str]:
    admin = create_engine(_sync(_BASE_URL), isolation_level="AUTOCOMMIT")
    try:
        admin.connect().close()
    except Exception:
        admin.dispose()
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail("REQUIRE_DB=1 but POSTGRES_URL is not reachable")
        pytest.skip("POSTGRES_URL not reachable")
    name = f"coregcs_{uuid.uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(_BASE_URL).set(database=name).render_as_string(hide_password=False)
    finally:
        os.environ["POSTGRES_URL"] = _BASE_URL
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _run(url: str, fn, *args: str) -> None:  # type: ignore[no-untyped-def]
    os.environ["POSTGRES_URL"] = url
    fn(Config(str(_CORE_INI)), *args)


def _one(url: str, statement: str) -> tuple | None:
    engine = create_engine(_sync(url), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            row = conn.execute(text(statement)).first()
            return tuple(row) if row is not None else None
    finally:
        engine.dispose()


def test_upgrade_admits_a_chapter_and_records_a_carve_out(temp_db: str) -> None:
    _run(temp_db, command.upgrade, _REVISION)
    check = _one(temp_db, _CHECK)
    assert check is not None and "{2,10}" in check[0]
    assert _one(temp_db, _COLUMN) == ("false", "NO")


def test_downgrade_restores_the_four_digit_floor(temp_db: str) -> None:
    _run(temp_db, command.upgrade, _REVISION)
    _run(temp_db, command.downgrade, _PREVIOUS)
    check = _one(temp_db, _CHECK)
    assert check is not None and "{4,10}" in check[0]
    assert _one(temp_db, _COLUMN) is None
