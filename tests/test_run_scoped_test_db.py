"""Test databases are scoped by run, not just by worker.

Worker-only scoping meant two concurrent runs both computed `<db>_gw0`. Because
`configure_worker_db` clones by dropping and recreating its target, one run's
setup destroyed the other's live database mid-suite, terminating the running
suite's backends. What that produced downstream was cross-run database
destruction and unreliable results; an earlier guess at the crash mechanism was
refuted and is deliberately not restated here. The advisory lock inside
`_clone_database` could not prevent it, since both runs legitimately wanted the
same name and the lock serialised the destruction.

These exercise the naming decision only. Cloning needs a server; the defect was
in what the name was, so that is what is pinned here.
"""

from __future__ import annotations

import re

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # CODIFY_TEST_SHARE_DB included: inherited from the invoking shell it would
    # return before any suffix is applied, and these tests are about the suffix.
    for var in (
        "CODIFY_TEST_RUN_ID",
        "PYTEST_XDIST_TESTRUNUID",
        "PYTEST_XDIST_WORKER",
        "CODIFY_TEST_SHARE_DB",
    ):
        monkeypatch.delenv(var, raising=False)


def test_workers_of_one_run_share_its_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: gw0 and gw1 of one run must agree, or they stop sharing
    the corpus their tests were written against."""
    from codify.testing import _run_id

    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "a6fef8631aeb43e0b30b5ffe819d27f0")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    first = _run_id()
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    assert _run_id() == first


def test_two_runs_do_not_collide(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defect. Both runs used to reach `<db>_gw0` and destroy each other."""
    from codify.testing import _run_id

    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "a" * 32)
    mine = _run_id()
    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "b" * 32)
    assert _run_id() != mine


def test_a_bare_run_still_gets_an_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside xdist there is no run uid, and sharing the base DB is how a single
    targeted file could still trample a concurrent suite."""
    from codify.testing import _run_id

    generated = _run_id()
    assert generated
    # Exported, so anything else in this process agrees with it.
    assert _run_id() == generated


def test_a_pinned_id_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from codify.testing import _run_id

    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "c" * 32)
    monkeypatch.setenv("CODIFY_TEST_RUN_ID", "pinned-1")
    assert _run_id() == "pinned1"


def test_the_id_is_a_safe_database_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is interpolated into DDL, so anything outside [A-Za-z0-9_] is a hole."""
    from codify.testing import _SAFE_DB_NAME, _run_id

    monkeypatch.setenv("CODIFY_TEST_RUN_ID", 'x"; DROP DATABASE codify; --')
    run_id = _run_id()
    assert _SAFE_DB_NAME.match(f"codify_{run_id}_gw0")
    assert len(run_id) <= 12


def test_two_concurrent_runs_get_different_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defect end to end, and the one test here that fails against the old
    naming rather than merely failing to import it.

    The clone is stubbed: what is under test is which database name each run
    resolves to, and on the old code both resolved to `<db>_gw0`.
    """
    from codify import testing

    monkeypatch.setattr(testing, "_clone_database", lambda *a, **k: None)
    monkeypatch.setattr(testing, "_reindex_bm25", lambda *a, **k: None)
    # Both reach Postgres; this test is about naming and runs with none.
    monkeypatch.setattr(testing, "claim_run", lambda *a, **k: None)
    monkeypatch.setattr(testing, "reap_dead_run_databases", lambda *a, **k: [])
    monkeypatch.setattr(testing.atexit, "register", lambda *a, **k: None)
    base = "postgresql://codify:codify@localhost:5432/codify"

    def _resolve(run_uid: str) -> str:
        monkeypatch.setenv("POSTGRES_URL", base)
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", run_uid)
        monkeypatch.delenv("CODIFY_TEST_RUN_ID", raising=False)
        testing.configure_worker_db()
        import os

        return os.environ["POSTGRES_URL"]

    first = _resolve("a" * 32)
    second = _resolve("b" * 32)
    assert first != second, "two concurrent runs resolved to the same database"
    # The producer, not just the matcher: a clone without the marker is one the
    # reaper will never look at, and every consumer test would stay green.
    for url in (first, second):
        assert re.search(testing._CLONE_NAME_RE, url), url
