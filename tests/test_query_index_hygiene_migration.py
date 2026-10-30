"""Migration 0027 drops indexes a wider one already serves and adds the ones
foreign-key actions and retry lookups lack; the downgrade restores the chain."""

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
_REVISION = "0027_query_index_hygiene"
_PREVIOUS = "0026_provisions_created_at_idx"
_NIL = "'00000000-0000-0000-0000-000000000000'"

# name -> (table, access method, key columns, valid), for every index in public.
_INDEXES = """
SELECT c.relname, t.relname, am.amname,
       array(SELECT pg_get_indexdef(i.indexrelid, k, true)
             FROM generate_series(1, i.indnkeyatts) k),
       i.indisvalid
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_class t ON t.oid = i.indrelid
JOIN pg_am am ON am.oid = c.relam
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
"""


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
    name = f"coreidx_{uuid.uuid4().hex[:12]}"
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


def _sql(url: str, statement: str) -> list[tuple]:
    engine = create_engine(_sync(url), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            result = conn.execute(text(statement))
            return [tuple(r) for r in result] if result.returns_rows else []
    finally:
        engine.dispose()


def _indexes(url: str) -> dict[str, tuple]:
    return {r[0]: r[1:] for r in _sql(url, _INDEXES)}


def _reloptions(url: str) -> list[str] | None:
    return _sql(url, "SELECT reloptions FROM pg_class WHERE oid = 'provisions'::regclass")[0][0]


def test_every_dropped_btree_leaves_a_successor_on_its_prefix(temp_db: str) -> None:
    _run(temp_db, command.upgrade, _PREVIOUS)
    before = _indexes(temp_db)
    _run(temp_db, command.upgrade, _REVISION)
    after = _indexes(temp_db)

    dropped = {n: d for n, d in before.items() if n not in after}
    btrees = {n: d for n, d in dropped.items() if d[1] == "btree"}
    assert btrees, dropped
    for name, (table, _am, keys, _valid) in btrees.items():
        successors = [
            other
            for other, (t, am, k, valid) in after.items()
            if t == table and am == "btree" and valid and list(k[: len(keys)]) == list(keys)
        ]
        assert successors, f"{name} on {table}{keys} has no index leading with its columns"


# Each reader the migration serves: a foreign key's ON DELETE action, the retry
# chain lookup, and the readers of the dropped prefixes.
@pytest.mark.parametrize(
    "reader",
    [
        f"UPDATE registry_works SET law_id = NULL WHERE law_id = {_NIL}",
        f"UPDATE repair_proposals SET run_id = NULL WHERE run_id = {_NIL}",
        "SELECT id FROM runs WHERE params->>'retried_from' = 'x' ORDER BY created_at DESC LIMIT 1",
        f"SELECT id FROM sections WHERE version_id = {_NIL}",
        f"SELECT id FROM annex_rows WHERE version_id = {_NIL}",
        f"SELECT id FROM law_links WHERE jurisdiction_id = {_NIL} AND source_ref = 'x'",
    ],
)
def test_each_reader_plans_through_an_index(temp_db: str, reader: str) -> None:
    _run(temp_db, command.upgrade, "head")
    engine = create_engine(_sync(temp_db))
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(r[0] for r in conn.execute(text("EXPLAIN " + reader)))
    finally:
        engine.dispose()
    # A key condition, not a full index scan the disabled seq scan pushed it to.
    assert "Index Cond" in plan, plan


def test_downgrade_restores_the_previous_indexes_and_settings(temp_db: str) -> None:
    _run(temp_db, command.upgrade, _PREVIOUS)
    before, options = _indexes(temp_db), _reloptions(temp_db)
    _run(temp_db, command.upgrade, _REVISION)
    assert _reloptions(temp_db) != options

    _run(temp_db, command.downgrade, _PREVIOUS)
    assert _indexes(temp_db) == before
    assert _reloptions(temp_db) == options


def test_upgrade_drops_an_untracked_vector_index_and_rebuilds_an_invalid_one(
    temp_db: str,
) -> None:
    _run(temp_db, command.upgrade, _PREVIOUS)
    before = set(_indexes(temp_db))
    _run(temp_db, command.upgrade, _REVISION)
    added = {n: d for n, d in _indexes(temp_db).items() if n not in before}
    assert added
    broken = sorted(added)[0]
    table = added[broken][0]
    _run(temp_db, command.downgrade, _PREVIOUS)

    # An HNSW made outside the chain, and a concurrent build cancelled part way.
    _sql(
        temp_db,
        "CREATE INDEX version_unit_embeddings_hnsw_idx ON version_unit_embeddings "
        "USING hnsw (embedding halfvec_cosine_ops)",
    )
    _sql(temp_db, f"CREATE INDEX {broken} ON {table} (id)")
    _sql(temp_db, f"UPDATE pg_index SET indisvalid = false WHERE indexrelid = '{broken}'::regclass")

    _run(temp_db, command.upgrade, _REVISION)
    after = _indexes(temp_db)
    assert "version_unit_embeddings_hnsw_idx" not in after
    assert after[broken] == added[broken]
