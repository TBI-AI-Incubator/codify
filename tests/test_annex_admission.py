"""Canonical annex admission preserves existing vocabularies and evidence."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from sqlalchemy.engine import Connection

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = _ROOT / "codify/migrations"


def _migration() -> ModuleType:
    path = _MIGRATIONS / "versions/0018_annex_admission.py"
    spec = importlib.util.spec_from_file_location("annex_admission", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "expression",
    [
        "(kind = ANY (ARRAY['retire_law'::text, 'future_kind2'::text]))",
        "((kind)::text = ANY ((ARRAY['retire_law'::character varying, "
        "'future_kind2'::character varying])::text[]))",
    ],
)
def test_admission_preserves_complete_literal_vocabulary(expression: str) -> None:
    assert _migration()._parse_kinds(expression) == ["retire_law", "future_kind2"]


@pytest.mark.parametrize(
    "expression",
    [
        "kind IN ('retire_law') OR true",
        "(kind = ANY (ARRAY['retire_law'::text])) AND id > 0",
        "other = ANY (ARRAY['retire_law'::text])",
        "kind = ANY (ARRAY[]::text[])",
        "kind IS NOT NULL",
        "kind = ANY (ARRAY['future kind'::text])",
        "kind = ANY (ARRAY['future(kind)'::text])",
        "kind = ANY (ARRAY['future::textkind'::text])",
        "kind = ANY (ARRAY['invalid quote''value'::text])",
    ],
)
def test_admission_refuses_unknown_constraint_semantics(expression: str) -> None:
    with pytest.raises(RuntimeError, match="Incompatible"):
        _migration()._parse_kinds(expression)


def test_canonical_graph_has_new_identity_and_rejects_legacy_stamps() -> None:
    scripts = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    assert scripts.get_heads() == ["0019_search_term_lexicon"]
    head = scripts.get_revision("0019_search_term_lexicon")
    assert head is not None and head.down_revision == "0018_annex_admission"
    current = scripts.get_revision("0018_annex_admission")
    assert current is not None and current.down_revision == "0014_effect_publisher_id"
    for legacy in (
        "0015_xref_ledger",
        "0016_delegated_powers",
        "0017_annex_transcription",
    ):
        with pytest.raises(ResolutionError):
            scripts.revision_map.get_revision(legacy)


def test_historical_migration_is_preserved_exactly_outside_discovery() -> None:
    bundle = _MIGRATIONS / "recovery/annex_0017"
    manifest = json.loads((bundle / "manifest.json").read_text())
    payload = (bundle / manifest["file"]).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == manifest["sha256"]
    assert b'"0017_annex_transcription"' in payload
    assert b'"0016_delegated_powers"' in payload
    assert not (_MIGRATIONS / "versions" / manifest["file"]).exists()


@pytest.fixture
def migration_connection() -> Iterator[Connection]:
    import uuid

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from codify.testing import postgres_url

    engine = create_engine(make_url(postgres_url()).set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection, connection.begin():
            schema = "annexmig_" + uuid.uuid4().hex
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", public')
            connection.exec_driver_sql(
                "CREATE TABLE runs (id integer PRIMARY KEY, kind text NOT NULL, "
                "CONSTRAINT runs_kind_check CHECK (kind IN ('retire_law', 'future_kind2')))"
            )
            connection.exec_driver_sql(
                "CREATE TABLE run_artifacts (id integer PRIMARY KEY, kind text NOT NULL, "
                "CONSTRAINT run_artifacts_kind_check "
                "CHECK (kind IN ('publisher_effects', 'future_artifact2')))"
            )
            connection.exec_driver_sql("INSERT INTO runs VALUES (1, 'future_kind2')")
            connection.exec_driver_sql("INSERT INTO run_artifacts VALUES (1, 'future_artifact2')")
            yield connection
            # DDL and fixture rows live entirely inside this transaction.
            connection.rollback()
    finally:
        engine.dispose()


def _invoke(connection: Connection, function: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with Operations.context(MigrationContext.configure(connection)):
        getattr(_migration(), function)()


def _definitions(connection: Connection) -> list[str]:
    return list(
        connection.exec_driver_sql(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid IN ('runs'::regclass, 'run_artifacts'::regclass) "
            "AND contype = 'c' ORDER BY conname"
        ).scalars()
    )


@pytest.mark.integration
def test_db_admission_preserves_rows_kinds_and_accepts_already_admitted(
    migration_connection: Connection,
) -> None:
    conn = migration_connection
    before = _definitions(conn)
    _invoke(conn, "upgrade")
    after = _definitions(conn)
    assert "transcribe_annex_images" in after[1]
    assert "annex_transcription" in after[0]
    assert "future_kind2" in after[1] and "future_artifact2" in after[0]
    assert conn.exec_driver_sql("SELECT kind FROM runs").scalar_one() == "future_kind2"
    assert conn.exec_driver_sql("SELECT kind FROM run_artifacts").scalar_one() == "future_artifact2"
    oids = list(
        conn.exec_driver_sql(
            "SELECT oid FROM pg_constraint WHERE conrelid IN "
            "('runs'::regclass, 'run_artifacts'::regclass) ORDER BY oid"
        ).scalars()
    )
    _invoke(conn, "upgrade")
    assert _definitions(conn) == after
    assert (
        list(
            conn.exec_driver_sql(
                "SELECT oid FROM pg_constraint WHERE conrelid IN "
                "('runs'::regclass, 'run_artifacts'::regclass) ORDER BY oid"
            ).scalars()
        )
        == oids
    )
    _invoke(conn, "downgrade")
    assert _definitions(conn) == before
    _invoke(conn, "upgrade")
    assert _definitions(conn) == after


@pytest.mark.integration
def test_db_retry_restores_both_constraints_after_interruption(
    migration_connection: Connection,
) -> None:
    from sqlalchemy import event

    conn = migration_connection
    before = _definitions(conn)

    def interrupt(
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.startswith("ALTER TABLE run_artifacts ADD CONSTRAINT"):
            raise RuntimeError("injected second-constraint interruption")

    event.listen(conn, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="injected"), conn.begin_nested():
            _invoke(conn, "upgrade")
    finally:
        event.remove(conn, "before_cursor_execute", interrupt)
    assert _definitions(conn) == before
    _invoke(conn, "upgrade")
    assert "transcribe_annex_images" in _definitions(conn)[1]
    assert "annex_transcription" in _definitions(conn)[0]


@pytest.mark.integration
@pytest.mark.parametrize(
    "table,kind",
    [("runs", "transcribe_annex_images"), ("run_artifacts", "annex_transcription")],
)
def test_db_populated_downgrade_refuses_without_deleting(
    migration_connection: Connection,
    table: str,
    kind: str,
) -> None:
    conn = migration_connection
    _invoke(conn, "upgrade")
    conn.exec_driver_sql(f"INSERT INTO {table} VALUES (2, '{kind}')")
    before = _definitions(conn)
    with pytest.raises(RuntimeError, match="retains .* evidence"):
        _invoke(conn, "downgrade")
    assert _definitions(conn) == before
    assert conn.exec_driver_sql(f"SELECT kind FROM {table} WHERE id = 2").scalar_one() == kind


@pytest.mark.integration
@pytest.mark.parametrize(
    "replacement",
    [
        None,
        "CHECK (kind IS NOT NULL)",
        "CHECK (kind IN ('publisher_effects', 'future_artifact2')) NOT VALID",
        "CHECK (kind IN ('publisher_effects', 'future_artifact2')) NO INHERIT",
    ],
)
def test_db_incompatible_second_constraint_refuses_before_any_change(
    migration_connection: Connection,
    replacement: str | None,
) -> None:
    conn = migration_connection
    conn.exec_driver_sql("ALTER TABLE run_artifacts DROP CONSTRAINT run_artifacts_kind_check")
    if replacement is not None:
        conn.exec_driver_sql(
            f"ALTER TABLE run_artifacts ADD CONSTRAINT run_artifacts_kind_check {replacement}"
        )
    before = _definitions(conn)
    with pytest.raises(RuntimeError, match="[Ii]ncompatible"):
        _invoke(conn, "upgrade")
    assert _definitions(conn) == before
