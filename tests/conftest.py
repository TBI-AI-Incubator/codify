"""Package-wide test setup for the codify core suite.

Under pytest-xdist, give each worker its own database so the committing/DDL
storage tests don't collide on a shared Postgres. No-op outside xdist.

Two offline gates run at collection so a bare `pytest` with no gateway and no
database passes honestly rather than erroring: `live_llm` tests skip without a
model, `integration` tests skip without a reachable Postgres. Both are guarded
so CI cannot take the skip by accident (see each gate).
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest

from codify.jurisdictions import JURISDICTIONS_DIR, JurisdictionDataMissing
from codify.open_wheel import ships_in_open_wheel
from codify.testing import apply_db_gate, configure_worker_db

configure_worker_db()

_DB_SKIP_ATTR = "_codify_db_skipped_count"


def _db_endpoint() -> tuple[str, int]:
    raw = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
    parsed = urlparse(raw)
    return parsed.hostname or "localhost", parsed.port or 5432


def _db_reachable() -> bool:
    """A TCP connect to the Postgres endpoint: reachable enough to run the
    integration tests, cheap enough to probe once at collection."""
    host, port = _db_endpoint()
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _gate_live_llm(config, items, markexpr: str) -> None:
    """A bare offline `pytest` would run the live_llm tests and fail with no
    gateway. Skip them unless they were explicitly selected (`-m live_llm`, how the
    live-llm CI job runs, which the env vars can't neuter) or a gateway is
    configured (GEMINI_API_KEY / RUN_LIVE_LLM), so a plain run passes offline while
    the live job and a local dev with a key still run everything."""
    if "live_llm" in markexpr or os.environ.get("GEMINI_API_KEY") or os.environ.get("RUN_LIVE_LLM"):
        return
    skip = pytest.mark.skip(
        reason="needs an LLM gateway; select with -m live_llm or set RUN_LIVE_LLM"
    )
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip)


def _gate_integration_db(config, items, markexpr: str) -> None:
    """`integration` tests need Postgres/MinIO. Apply the DB gate once at
    collection. REQUIRE_DB is set by the CI workflow alongside its Postgres
    service, so an unreachable DB there aborts the run instead of skipping. The
    acting logic lives in `apply_db_gate` so it is unit-tested (test_offline_gate)."""
    if "integration" in markexpr:
        return
    host, port = _db_endpoint()
    skipped = apply_db_gate(
        items, _db_reachable(), bool(os.environ.get("REQUIRE_DB")), f"{host}:{port}"
    )
    setattr(config, _DB_SKIP_ATTR, skipped)


def pytest_collection_modifyitems(config, items):
    markexpr = config.getoption("markexpr") or ""
    _gate_live_llm(config, items, markexpr)
    _gate_integration_db(config, items, markexpr)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Make the DB skip loud: name the count and how to run them, so a green
    offline run cannot be mistaken for one that exercised the DB tests."""
    count = getattr(config, _DB_SKIP_ATTR, 0)
    if count:
        host, port = _db_endpoint()
        terminalreporter.write_line(
            f"OFFLINE: skipped {count} integration test(s) needing Postgres at "
            f"{host}:{port}. Set REQUIRE_DB=1 (with a database up) to run them.",
            yellow=True,
        )


# A config that does not ship can only be here in the monorepo, where absence is
# a real defect and must still fail. Asked of the shipping rule rather than of a
# count, which no rule sets and which a large enough open set would cross.
_TREE_ROOT = JURISDICTIONS_DIR.parent.parent
_FULL_CORPUS = any(not ships_in_open_wheel(cfg) for cfg in JURISDICTIONS_DIR.glob("*/config.json"))


def _skip_when_not_shipped(exc: JurisdictionDataMissing) -> None:
    if _FULL_CORPUS:
        raise exc
    pytest.skip(f"config not shipped: {exc}")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Iterator[None]:
    """Fixtures raise too, and an error there is not a skip."""
    try:
        return (yield)
    except JurisdictionDataMissing as exc:
        _skip_when_not_shipped(exc)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    """Skip, not fail, where a test names a config this tree does not ship."""
    try:
        return (yield)
    except JurisdictionDataMissing as exc:
        _skip_when_not_shipped(exc)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "jurisdiction(code): skips when that config is not shipped here"
    )
    config.addinivalue_line(
        "markers", "requires_repo(*paths): skips when the repository files it names are absent"
    )


@pytest.fixture(autouse=True)
def _declared_requirements(request: pytest.FixtureRequest) -> None:
    """Skip where a declared requirement is absent, for the cases the exception
    hooks cannot reach: a caller that takes `None` and answers with a default."""
    for mark in request.node.iter_markers("jurisdiction"):
        for code in mark.args:
            if not (JURISDICTIONS_DIR / code / "config.json").is_file():
                _skip_when_not_shipped(JurisdictionDataMissing(f"no config for {code!r}"))
    for mark in request.node.iter_markers("requires_repo"):
        for rel in mark.args:
            if (_TREE_ROOT / rel).exists():
                continue
            if _FULL_CORPUS:
                raise AssertionError(f"the monorepo is missing {rel}")
            pytest.skip(f"not in this tree: {rel}")


def _crashed_on_absent_config(longrepr: object) -> bool:
    """The terminal exception type, not the rendered traceback: that text also
    carries source lines, so a module merely naming the class would match."""
    crash = getattr(longrepr, "reprcrash", None)
    if crash is None:
        return False
    raised = crash.message.partition(":")[0].rpartition(".")[2]
    return raised == JurisdictionDataMissing.__name__


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> Iterator[None]:
    """A module that names an unshipped config raises at import, which ends
    collection for the whole suite rather than skipping one file."""
    report = yield
    if _FULL_CORPUS or report.outcome != "failed":
        return report
    if _crashed_on_absent_config(report.longrepr):
        report.outcome = "skipped"
        report.longrepr = (str(collector.path), 0, "config not shipped")
    return report
