"""The alert that fires before the disk carrying test databases runs out.

Its whole value is being independent of whatever should reclaim them, so these
drive it with the database stubbed rather than through a real accumulation.
"""

from __future__ import annotations

from typing import Any

import pytest

from codify import testing


class _Cursor:
    def __init__(self, row: tuple[int, int]) -> None:
        self._row = row

    def fetchone(self) -> tuple[int, int]:
        return self._row


class _Conn:
    def __init__(self, row: tuple[int, int]) -> None:
        self._row = row
        self.dsn: str | None = None

    def execute(self, _sql: str) -> _Cursor:
        return _Cursor(self._row)

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    # Module state, so one test's warning would silence the next.
    monkeypatch.setattr(testing, "_clone_warning_emitted", False)


def _stub_psycopg(monkeypatch: pytest.MonkeyPatch, row: tuple[int, int]) -> list[str]:
    seen: list[str] = []

    def connect(dsn: str, **_: Any) -> _Conn:
        seen.append(dsn)
        return _Conn(row)

    monkeypatch.setattr("psycopg.connect", connect)
    return seen


GB = 1024**3


def test_below_the_threshold_says_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/codify")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    _stub_psycopg(monkeypatch, (40, 1 * GB))
    testing.warn_on_clone_accumulation()
    assert capsys.readouterr().err == ""


def test_above_the_threshold_names_the_count_and_the_size(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/codify")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    _stub_psycopg(monkeypatch, (400, 9 * GB))
    testing.warn_on_clone_accumulation()
    err = capsys.readouterr().err
    assert "WARNING" in err and "400" in err and "9.0 GB" in err
    # The remedy has to be runnable, and it must not connect to the database
    # FORCE would disconnect it from.
    assert "/postgres" in err and "DROP DATABASE" in err
    # It must also spare the pre-run-scoping `codify_gwN` names, which an older
    # checkout still uses: only a name carrying a run id is abandoned by its name.
    assert testing._CLONE_NAME_RE in err


def test_past_the_loud_threshold_it_escalates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/codify")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    _stub_psycopg(monkeypatch, (900, 20 * GB))
    testing.warn_on_clone_accumulation()
    assert "CRITICAL" in capsys.readouterr().err


def test_it_queries_the_maintenance_database_not_the_one_under_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/codify_run_gw3")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    seen = _stub_psycopg(monkeypatch, (400, 9 * GB))
    testing.warn_on_clone_accumulation()
    # Counting from inside a clone would miss it once that clone is itself dropped.
    assert seen and seen[0].endswith("/postgres")


def test_only_one_xdist_worker_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/codify")
    _stub_psycopg(monkeypatch, (400, 9 * GB))
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw5")
    testing.warn_on_clone_accumulation()
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    testing.warn_on_clone_accumulation()
    assert "WARNING" in capsys.readouterr().err


def test_an_unreachable_database_is_silent_not_fatal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A warning that can break the suite is worse than the accumulation."""
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/codify")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    def boom(*_: object, **__: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("psycopg.connect", boom)
    testing.warn_on_clone_accumulation()
    assert capsys.readouterr().err == ""


def test_no_postgres_url_is_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    testing.warn_on_clone_accumulation()
    assert capsys.readouterr().err == ""


def test_it_warns_once_even_when_several_conftests_call_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two test roots in one process each call `configure_worker_db`."""
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/codify")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    _stub_psycopg(monkeypatch, (400, 9 * GB))
    testing.warn_on_clone_accumulation()
    assert "WARNING" in capsys.readouterr().err
    testing.warn_on_clone_accumulation()
    assert capsys.readouterr().err == ""


def test_the_count_sees_lock_aware_clones(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reaper renamed them; a counter blind to the new shape reports zero
    while the disk fills."""
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost:5432/codify")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    seen: list[str] = []

    class _C:
        def execute(self, sql: str) -> object:
            seen.append(sql)
            return type("R", (), {"fetchone": lambda self: (0, 0)})()

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("psycopg.connect", lambda *a, **k: _C())
    testing.warn_on_clone_accumulation()
    assert any("_gw[0-9]+(_lk)?$" in q for q in seen), seen
