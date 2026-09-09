"""The guard that refuses to clone a template database behind the chain.

A base missing a migration fails in whatever feature that migration added, so
the failures read as a code regression. Two sessions diagnosed one such run
differently before this existed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codify import testing


class _Row:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def fetchone(self) -> tuple[str] | None:
        return None if self._value is None else (self._value,)


class _Conn:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, _sql: str) -> _Row:
        return _Row(self._value)


def _patch(monkeypatch: pytest.MonkeyPatch, *, version: str | None, chains: Any = None) -> None:
    """Point the guard at this repo's real migration files but a fake database."""
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/base")
    monkeypatch.setattr(testing, "psycopg", None, raising=False)
    if chains is not None:
        monkeypatch.setattr(testing, "_CHAINS", chains)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _Conn(version))


@pytest.mark.requires_repo("apps/api/migrations/versions")
def test_a_base_behind_the_chain_names_both_revisions(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, version="0001")
    with pytest.raises(RuntimeError) as exc:
        testing.assert_base_at_head()
    assert "0001" in str(exc.value)
    assert "alembic upgrade head" in str(exc.value), "the message must carry the fix"


@pytest.mark.requires_repo("apps/api/migrations/versions")
def test_a_current_base_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(testing.__file__).resolve().parents[3]
    head = max(
        f.name.split("_", 1)[0]
        for f in (root / "apps/api/migrations/versions").glob("*.py")
        if f.name[0].isdigit()
    )
    _patch(monkeypatch, version=head, chains=((Path("apps/api/migrations/versions"), "t"),))
    testing.assert_base_at_head()


def test_no_database_url_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suites that never touch Postgres must not be blocked by this."""
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    testing.assert_base_at_head()


def test_an_unreachable_database_is_left_to_the_fixtures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection failure is the fixtures' error to report, with their own
    message; raising here would replace it with a migration complaint."""
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/base")

    import psycopg

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(psycopg, "connect", _boom)
    testing.assert_base_at_head()


def test_an_unmigrated_base_is_left_to_the_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    """No row in the version table is a base nothing has migrated, which the
    fixtures fail on directly."""
    _patch(monkeypatch, version=None)
    testing.assert_base_at_head()


def test_the_share_db_opt_out_still_checks_the_base() -> None:
    """Opting into the base database runs against it directly, so it needs the
    check most. The guard sat after this return and was skipped by it."""
    import inspect

    body = inspect.getsource(testing.configure_worker_db)
    guard = body.index("assert_base_at_head()")
    share_return = body.index('CODIFY_TEST_SHARE_DB") == "1"')
    assert guard < share_return, "the share-DB return would skip the head check"
