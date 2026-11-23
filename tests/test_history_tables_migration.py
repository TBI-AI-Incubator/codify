"""The history migration creates only new objects, undoes them, and sits on the one chain."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

_ROOT = Path(__file__).resolve().parents[1]
_TABLES = (
    "history_works",
    "publisher_texts",
    "dated_expressions",
    "provision_intervals",
    "provision_links",
    "history_withdrawals",
)


def _migration() -> Path:
    versions = _ROOT / "codify" / "migrations" / "versions"
    (found,) = (
        p
        for p in versions.glob("*.py")
        if "CREATE TABLE IF NOT EXISTS history_works" in p.read_text()
    )
    return found


def test_the_migration_is_the_one_head_of_the_chain() -> None:
    scripts = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    assert [r.path for r in scripts.get_revisions(scripts.get_heads())] == [str(_migration())]


def test_every_table_is_created_dropped_and_owned_by_the_core_chain() -> None:
    source = _migration().read_text()
    env = (_ROOT / "codify" / "migrations" / "env.py").read_text()
    for table in _TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} " in source
        assert f'"{table}"' in source
        assert f'"{table}"' in env


@pytest.mark.parametrize("statement", ["ALTER TABLE", "DROP TABLE", "DROP COLUMN", "DELETE FROM"])
def test_upgrade_touches_no_existing_object(statement: str) -> None:
    upgrade = _migration().read_text().split("def downgrade")[0]
    assert statement not in upgrade


def test_the_facts_table_alone_has_a_guard_trigger_and_it_is_dropped_on_downgrade() -> None:
    source = _migration().read_text()
    assert source.count("CREATE OR REPLACE TRIGGER") == 1
    assert "BEFORE UPDATE ON publisher_texts" in source
    assert "DROP FUNCTION IF EXISTS public.enforce_publisher_texts_immutable()" in source
