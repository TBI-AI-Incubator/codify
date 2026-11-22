"""0029 sits on the single core chain and creates the three official-source tables."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_ROOT = Path(__file__).resolve().parents[1]
_REVISION = "0029_official_sources"


def test_official_sources_follows_goods_code_scope_on_one_chain() -> None:
    scripts = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    rev = scripts.get_revision(_REVISION)
    assert rev is not None and rev.down_revision == "0028_goods_code_scope"
    assert len(scripts.get_heads()) == 1


def test_the_tables_are_owned_by_the_core_chain_and_ids_carry_no_law_key() -> None:
    source = (_ROOT / "codify" / "migrations" / "versions" / f"{_REVISION}.py").read_text()
    for table in ("official_sources", "official_anchors", "official_ids"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in source
        assert f"DROP TABLE IF EXISTS {table}" in source
        assert f'"{table}"' in (_ROOT / "codify" / "migrations" / "env.py").read_text()
    ids = source.split("CREATE TABLE IF NOT EXISTS official_ids")[1].split('"""')[0]
    assert "REFERENCES" not in ids
