"""Migration 0024 is the head and touches `versions` without a long lock."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_ROOT = Path(__file__).resolve().parents[1]
_REVISION = "0024_current_versions"


def test_current_versions_is_the_head_after_source_spans() -> None:
    scripts = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    assert scripts.get_heads() == [_REVISION]
    rev = scripts.get_revision(_REVISION)
    assert rev is not None and rev.down_revision == "0023_source_spans"


def test_versions_is_altered_under_a_short_lock_and_indexed_concurrently() -> None:
    source = (_ROOT / "codify" / "migrations" / "versions" / f"{_REVISION}.py").read_text()
    assert "SET lock_timeout = '5s'" in source
    assert "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX}" in source
    assert "pg_advisory" not in source  # one lock per law exhausts the lock table
    # The backfill runs after the DDL commits, so it holds no table lock.
    ddl, _, backfill = source.partition("autocommit_block()")
    assert "ADD COLUMN" in ddl and "refresh_current_versions(CAST" in backfill
    assert "DISABLE TRIGGER" not in source
    assert "indisvalid" in source and "DROP INDEX CONCURRENTLY {_INDEX}" in source
