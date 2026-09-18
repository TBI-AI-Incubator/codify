"""Shared helpers for test fixtures across the workspace.

Production code uses `api.db.resolve_async_url`; this module mirrors its
normalisation contract so test modules don't each re-roll the helper.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import re
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    import pytest

_DEFAULT_OLLAMA_HOST = "http://localhost:11434"


def integration_gate_action(reachable: bool, require_db: bool) -> str:
    """The single offline policy for DB-backed `integration` tests, as a pure
    function so it can be unit-tested without a database (the guard's own test).

    - reachable → "run".
    - unreachable + REQUIRE_DB → "require-fail": a database was demanded and is
      absent, so fail loud rather than skip. A broken CI Postgres must not turn
      real assertions into silent green; abstain is not pass.
    - unreachable + no REQUIRE_DB (offline / fork) → "skip".
    """
    if reachable:
        return "run"
    return "require-fail" if require_db else "skip"


def apply_db_gate(
    items: "list[pytest.Item]",
    reachable: bool,
    require_db: bool,
    endpoint: str = "",
) -> int:
    """Act on `integration_gate_action` for a collection of pytest items, so the
    acting logic (raise / skip-mark / count) is testable without a real database.

    Returns the number of `integration` items skipped. Raises `pytest.UsageError`
    on "require-fail" so REQUIRE_DB + an absent database aborts loudly rather than
    skipping. Pure of environment and IO: the caller supplies reachable/require_db.
    """
    import pytest

    action = integration_gate_action(reachable, require_db)
    if action == "run":
        return 0
    where = f" at {endpoint}" if endpoint else ""
    if action == "require-fail":
        raise pytest.UsageError(
            f"REQUIRE_DB is set but no Postgres is reachable{where}. Bring the "
            "database up or unset REQUIRE_DB; the integration tests will not be "
            "silently skipped while a database is demanded."
        )
    skip = pytest.mark.skip(
        reason=f"no Postgres reachable{where}; set REQUIRE_DB=1 to force the run"
    )
    skipped = 0
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
            skipped += 1
    return skipped


def postgres_url() -> str:
    """`POSTGRES_URL` normalised to `postgresql+asyncpg://`; see `settings.database_url`."""
    from codify.settings import database_url

    return database_url()


def ollama_host() -> str:
    """Return `OLLAMA_HOST` or the local-dev default."""
    return os.environ.get("OLLAMA_HOST", _DEFAULT_OLLAMA_HOST)


def safe_postgres_url() -> str:
    """Same as `postgres_url()` but with the password masked, safe to log."""
    return make_url(postgres_url()).render_as_string(hide_password=True)


async def postgres_ready(url: str | None = None) -> bool:
    """Probe whether Postgres at `url` accepts a connection."""
    engine = create_async_engine(url or postgres_url(), pool_pre_ping=True)
    try:
        async with engine.connect():
            return True
    except Exception:
        return False
    finally:
        await engine.dispose()


async def ollama_ready(*, model: str | None = None) -> bool:
    """Probe whether Ollama is reachable; if `model` set, also require it pulled."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            resp = await c.get(f"{ollama_host()}/api/tags")
        resp.raise_for_status()
    except (httpx.HTTPError, OSError):
        return False
    if model is None:
        return True
    pulled = {m.get("name", "").split(":")[0] for m in resp.json().get("models", [])}
    return model in pulled


_CLONE_ADVISORY_LOCK = 0x0C0D1F  # serialises concurrent template copies across xdist workers
# Only `<template>_<run>_gw<n>_lk` is reapable: the trailing marker says the run
# claims the liveness lock, and the older grammar stops at the worker suffix so
# cannot produce one. Bare and unmarked names are left alone, since an absent
# lock on those means an older run rather than a dead one.
_CLONE_NAME_RE = r"_[A-Za-z0-9]{1,12}_gw[0-9]+_lk$"
_SAFE_DB_NAME = re.compile(r"^[A-Za-z0-9_]+$")  # DB names are interpolated into DDL


def _run_id() -> str:
    """Short, stable identifier for this test run, shared by all of its workers.

    xdist's ``PYTEST_XDIST_TESTRUNUID`` is per-run and identical across a run's
    workers, which is exactly the scope needed; a PID is not, because each worker
    is its own process. Outside xdist no such variable exists, so a per-process id
    is generated and exported, which is the right scope there.
    """
    pinned = os.environ.get("CODIFY_TEST_RUN_ID")
    if pinned:
        return re.sub(r"[^A-Za-z0-9]", "", pinned)[:12] or "run"
    run_uid = os.environ.get("PYTEST_XDIST_TESTRUNUID")
    if not run_uid:
        run_uid = uuid.uuid4().hex
        os.environ["CODIFY_TEST_RUN_ID"] = run_uid
    return re.sub(r"[^A-Za-z0-9]", "", run_uid)[:12]


# Every migration chain a test database carries, as (versions dir, version
# table) from the repo root. Checked together: a suite touching one chain still
# clones a base that must satisfy both.
_CHAINS = (
    (Path("apps/api/migrations/versions"), "alembic_version"),
    (Path("packages/codify/codify/migrations/versions"), "alembic_version_core"),
)


def assert_base_at_head() -> None:
    """Fail loudly when the template database is behind a migration chain.

    A base missing a recent migration fails in whatever feature that migration
    added, reading as a code regression. Called before cloning, since every
    worker inherits the gap.
    """
    import psycopg

    url = os.environ.get("POSTGRES_URL")
    root = Path(__file__).resolve().parents[3]
    if not url or not (root / "apps").is_dir():
        return  # nothing to connect to, or not a workspace checkout
    dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    for versions_dir, table in _CHAINS:
        revisions = {
            m.group(1)
            for f in (root / versions_dir).glob("*.py")
            if (m := re.match(r"^(\d+)_", f.name))
        }
        if not revisions:
            continue
        head = max(revisions)
        try:
            with psycopg.connect(dsn, connect_timeout=5) as conn:
                row = conn.execute(f"SELECT version_num FROM {table}").fetchone()  # noqa: S608
        except Exception:  # noqa: S112 - unreachable or unmigrated is the
            continue  # fixtures' own error to report, not this guard's
        current = str(row[0]) if row else ""
        if current and current < head:
            raise RuntimeError(
                f"template database is at migration {current} but this tree ships "
                f"{head} ({table}). Run: uv run alembic upgrade head. Cloning it "
                f"gives every xdist worker the same gap, and the failures surface "
                f"in whatever feature the missing migration added rather than as "
                f"an unmigrated database."
            )


# Clones average 23 MB and the disk died at ~890 of them, so 5 GB leaves room to
# act and 12 GB is where the next full run plausibly finishes the job.
_CLONE_WARN_BYTES = 5 * 1024**3
_CLONE_LOUD_BYTES = 12 * 1024**3
_clone_warning_emitted = False


# `$ADMIN` because FORCE disconnects clients of the database it is issued from.
def _base_template() -> str:
    raw = os.environ.get("POSTGRES_URL")
    return re.sub(_CLONE_NAME_RE, "", make_url(raw).database or "") if raw else "codify"


# Text for a person to paste, not a query this builds.
_DROP_SQL = (  # noqa: S608
    "SELECT datname FROM pg_database WHERE datname LIKE '{t}\\_%' AND datname ~ '{p}'"
)


def _clone_drop_command(template: str) -> str:
    """The manual fallback, from the same matcher and scope the reaper uses, so
    the two cannot drift. `$ADMIN` because FORCE disconnects clients of the
    database the command is issued from."""
    return (
        '    ADMIN="${POSTGRES_URL%/*}/postgres"\n'
        '    SQL="' + _DROP_SQL.format(t=template, p=_CLONE_NAME_RE) + '"\n'
        '    psql "$ADMIN" -At -c "$SQL" \\\n'
        '      | xargs -I% psql "$ADMIN" -q -c \'DROP DATABASE IF EXISTS "%" WITH (FORCE)\''
    )


def warn_on_clone_accumulation(template: str = "") -> None:
    """Report on disk taken by leftover test databases, before it runs out.

    Reads `pg_database`, so it still works when whatever should reclaim them does
    not. Warns and never fails: a hard stop blocks the person who can clear the disk.
    """
    import psycopg

    global _clone_warning_emitted

    url = os.environ.get("POSTGRES_URL")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not url or _clone_warning_emitted or (worker and worker != "gw0"):
        return  # nothing to connect to, already said, or a sibling worker reported
    dsn = make_url(url).set(drivername="postgresql", database="postgres")
    try:
        with psycopg.connect(dsn.render_as_string(hide_password=False), connect_timeout=5) as c:
            # Looser than the sweep drops: a bare `codify_gwN` is real waste
            # but is not safe to remove unasked.
            row = c.execute(
                "SELECT count(*), coalesce(sum(pg_database_size(datname)), 0) "
                "FROM pg_database WHERE datname ~ '_gw[0-9]+(_lk)?$' "
                "OR datname LIKE 'coremig\\_%'"
            ).fetchone()
    except Exception:  # noqa: S110 - an unreachable database is the fixtures' error
        return
    count, total = row or (0, 0)
    if total < _CLONE_WARN_BYTES:
        return
    gb = total / 1024**3
    _clone_warning_emitted = True
    urgency = "CRITICAL" if total >= _CLONE_LOUD_BYTES else "WARNING"
    print(
        f"\n{urgency}: {count} leftover test databases hold {gb:.1f} GB. Each run "
        f"reaps its own and sweeps dead ones at start-up, so this many means the "
        f"reaper is not keeping up, or is not running.\n"
        f"  To clear them by hand, with no runs in flight since an idle connection "
        f"does not prove a run dead:\n\n{_clone_drop_command(template)}\n",
        file=sys.stderr,
    )


# Held for a run's life, keyed on its id. Acquiring it proves the run is gone;
# an empty `pg_stat_activity` does not, since a live run sits between connections.
_RUN_LOCK_NAMESPACE = 0x0C0D1E
_run_lock_conn: object | None = None


def _run_lock_key(run_id: str) -> int:
    # One bigint: the two-int form takes int4 and psycopg sends bigint.
    raw = int(hashlib.blake2s(run_id.encode(), digest_size=8).hexdigest(), 16)
    return (raw ^ _RUN_LOCK_NAMESPACE) - (1 << 63)


def claim_run(url: URL, run_id: str) -> None:
    """Hold this run's liveness lock until the process exits."""
    global _run_lock_conn
    if _run_lock_conn is not None:
        return
    import psycopg

    dsn = url.set(drivername="postgresql", database="postgres")
    conn = psycopg.connect(dsn.render_as_string(hide_password=False), connect_timeout=5)
    # Shared, so every worker of the run holds it at once; the sweeper's
    # exclusive try-lock then succeeds only after the last one exits.
    conn.execute("SELECT pg_advisory_lock_shared(%s)", (_run_lock_key(run_id),))
    conn.commit()
    _run_lock_conn = conn


def reap_dead_run_databases(url: URL, keep_run: str, template: str) -> list[str]:
    """Drop clones of `template` whose run no longer holds its lock.

    Scoped to the template: another application's `<name>_<run>_gw0` on the same
    server holds no lock of ours, so shape alone would make it a candidate."""
    import psycopg

    dsn = url.set(drivername="postgresql", database="postgres")
    dropped: list[str] = []
    with psycopg.connect(dsn.render_as_string(hide_password=False), connect_timeout=5) as c:
        c.autocommit = True
        rows = c.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s AND datname ~ %s",
            (f"{template}\\_%", _CLONE_NAME_RE),
        ).fetchall()
        for (name,) in rows:
            run = name.rsplit("_gw", 1)[0].rsplit("_", 1)[-1]
            if run == keep_run or not _SAFE_DB_NAME.match(name):
                continue
            got = c.execute("SELECT pg_try_advisory_lock(%s)", (_run_lock_key(run),)).fetchone()
            if not got or not got[0]:
                continue  # still running
            try:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # noqa: S608
                dropped.append(name)
            finally:
                c.execute("SELECT pg_advisory_unlock(%s)", (_run_lock_key(run),))
    return dropped


def drop_own_database(url: URL, name: str) -> list[str]:
    """Drop the clone this worker used. Its own only: workers finish at
    different times, and FORCE on a sibling still in use kills its connection."""
    import psycopg

    dsn = url.set(drivername="postgresql", database="postgres")
    dropped: list[str] = []
    with psycopg.connect(dsn.render_as_string(hide_password=False), connect_timeout=5) as c:
        c.autocommit = True
        if re.search(_CLONE_NAME_RE, name) and _SAFE_DB_NAME.match(name):
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # noqa: S608
            dropped.append(name)
    return dropped


def own_clone_and_base() -> tuple[str, URL | None]:
    """This worker's clone name and the base URL it hangs off, or None outside xdist."""
    raw = os.environ.get("POSTGRES_URL")
    if not raw or not os.environ.get("PYTEST_XDIST_WORKER"):
        return "", None
    url = make_url(raw)
    db = url.database or ""
    return db, url.set(database=re.sub(_CLONE_NAME_RE, "", db))


def configure_worker_db() -> None:
    """Give this test process its own database, cloned from the migrated base DB,
    and rewrite ``POSTGRES_URL`` in-process to point at it.

    Scoped by run **and** worker. The worker alone is not enough: two concurrent
    runs both compute ``<db>_gw0``, and the clone below drops and recreates its
    target after terminating its backends, so one run's setup destroys the other's
    live database mid-suite. The advisory lock in ``_clone_database`` cannot help,
    because both runs legitimately want the same name and the lock faithfully
    serialises the destruction.

    Outside xdist this is still a no-op: a single-process run keeps the base DB.
    Two concurrent bare runs therefore still share it, which is a separate
    mechanism and a separate fix.

    Run identity comes from ``PYTEST_XDIST_TESTRUNUID`` (xdist sets one per run,
    identical across that run's workers), or ``CODIFY_TEST_RUN_ID`` to pin it.
    Set ``CODIFY_TEST_SHARE_DB=1`` to opt back into the shared base DB.

    MUST be called at conftest import time, before anything (e.g.
    ``api.core.config.settings``) reads ``POSTGRES_URL``.
    """
    # Before the share-DB return, not after: opting into the base database is
    # the case that runs against it directly, so it needs the check most.
    assert_base_at_head()
    warn_on_clone_accumulation(_base_template())
    if os.environ.get("CODIFY_TEST_SHARE_DB") == "1":
        return
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return  # not under xdist: single-process run keeps the base DB
    # Fail loud rather than silently sharing the base DB (which would reintroduce
    # the exact cross-worker collision this exists to prevent).
    raw = os.environ.get("POSTGRES_URL")
    if not raw:
        raise RuntimeError(
            "PYTEST_XDIST_WORKER is set but POSTGRES_URL is unset; xdist workers "
            "would share one database and collide. Set POSTGRES_URL."
        )
    url = make_url(raw)
    template = url.database
    if not template:
        raise RuntimeError(f"POSTGRES_URL has no database name; cannot isolate a worker: {raw!r}")
    # Idempotent: when both test roots are collected in one process (`make test`),
    # each root's conftest calls this; the second call must not re-suffix an
    # already-worker-scoped DB into `<db>_gw0_gw0`.
    suffix = f"{_run_id()}_{worker}_lk"
    if template.endswith(f"_{suffix}"):
        return
    worker_db = f"{template}_{suffix}"
    claim_run(url, _run_id())
    if worker == "gw0":
        reap_dead_run_databases(url, keep_run=_run_id(), template=template)
    atexit.register(drop_own_database, url, worker_db)
    _clone_database(url, template=template, target=worker_db)
    _reindex_bm25(url.set(database=worker_db))
    os.environ["POSTGRES_URL"] = url.set(database=worker_db).render_as_string(hide_password=False)


# Every BM25 index, whatever it is called. `pg_indexes` alone cannot filter by
# access method, hence the join through `pg_class`.
_BM25_INDEXES_SQL = """
SELECT format('%I.%I', i.schemaname, i.indexname) AS qualified, i.indexdef
FROM pg_indexes i
JOIN pg_class c  ON c.relname = i.indexname AND c.relnamespace = i.schemaname::regnamespace
JOIN pg_am    am ON am.oid = c.relam
WHERE am.amname = 'bm25'
ORDER BY i.schemaname, i.indexname
"""


def _reindex_bm25(url: URL) -> None:
    """Give the clone its own OID for every BM25 index.

    pg_textsearch keys its shared-memory index state on index OID alone
    (`src/index/registry.h`: `Oid index_oid; /* Hash key - must be first */`;
    `MyDatabaseId` appears nowhere in its source). OIDs are per-database, and
    `CREATE DATABASE ... TEMPLATE` copies catalogs verbatim, so every worker
    clone inherits the template's OID and they collide in one cluster-wide
    cache entry. Symptom is `DataCorruptedError: pg_textsearch memtable record
    ... extends past free_offset` from whichever worker reads a cursor another
    advanced. Recreating the index here gives each clone a fresh OID; measured,
    it takes the failure from 6 per run to 0 across three runs.

    Enumerated by access method, never by name: the first version of this named
    `provisions_bm25_idx` alone, and the title index added later inherited the
    template's OID unreindexed. A no-op where no BM25 index exists.
    """
    import asyncio

    import asyncpg  # type: ignore[import-untyped]

    dsn = url.render_as_string(hide_password=False).replace("postgresql+asyncpg", "postgresql")

    async def _recreate() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            # Qualified by the server: an unqualified DROP resolves through
            # search_path and would miss, or hit the wrong index entirely.
            for row in await conn.fetch(_BM25_INDEXES_SQL):
                await conn.execute(f"DROP INDEX {row['qualified']}")
                await conn.execute(row["indexdef"])
        finally:
            await conn.close()

    asyncio.run(_recreate())


def _clone_database(url: URL, *, template: str, target: str) -> None:
    """``CREATE DATABASE target TEMPLATE template`` via asyncpg (a codify dep, so
    codify.testing carries no undeclared driver) against the maintenance DB.
    Serialised by an advisory lock; retried because ``pg_terminate_backend`` only
    signals, so a straggler on the template may not have closed yet."""
    import asyncio

    import asyncpg

    for name in (template, target):
        if not _SAFE_DB_NAME.match(name):
            raise RuntimeError(f"unsafe database identifier: {name!r}")

    async def _run() -> None:
        conn = await asyncpg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            database="postgres",
        )
        try:
            await conn.execute("SELECT pg_advisory_lock($1)", _CLONE_ADVISORY_LOCK)
            for attempt in range(10):
                await conn.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = ANY($1::text[]) AND pid <> pg_backend_pid()",
                    [template, target],
                )
                await conn.execute(f'DROP DATABASE IF EXISTS "{target}"')
                try:
                    await conn.execute(f'CREATE DATABASE "{target}" TEMPLATE "{template}"')
                    return
                except asyncpg.exceptions.ObjectInUseError:
                    if attempt == 9:
                        raise
                    await asyncio.sleep(0.5)  # template still has a closing backend; retry
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _CLONE_ADVISORY_LOCK)
            await conn.close()

    asyncio.run(_run())


__all__ = [
    "configure_worker_db",
    "ollama_host",
    "ollama_ready",
    "postgres_ready",
    "postgres_url",
    "safe_postgres_url",
]
