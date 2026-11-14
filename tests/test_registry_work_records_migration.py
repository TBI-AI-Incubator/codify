"""0030 follows 0029 on the single core chain and adds one nullable column."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_ROOT = Path(__file__).resolve().parents[1]
_REVISION = "0030_registry_work_records"


def test_registry_work_records_follows_official_sources_on_one_chain() -> None:
    scripts = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    rev = scripts.get_revision(_REVISION)
    assert rev is not None and rev.down_revision == "0029_official_sources"
    assert scripts.get_heads() == [_REVISION]


def test_the_column_is_added_and_dropped() -> None:
    source = (_ROOT / "codify" / "migrations" / "versions" / f"{_REVISION}.py").read_text()
    assert "ADD COLUMN IF NOT EXISTS record jsonb" in source
    assert "DROP COLUMN IF EXISTS record" in source
