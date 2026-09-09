"""The core migration chain's self-adopting guards.

The one that matters most: a core-chain ``downgrade`` on a database shared with
the product chain must NEVER drop the product-owned corpus tables. A regression
there is silent product data loss, so it is pinned here rather than left to the
CI shell gates (which only exercise ``upgrade``).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration

# Relative to this file, so it resolves in the package tree as well as the
# monorepo: the old form named the monorepo layout and landed above an sdist.
_CORE_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_BASELINE = "0001_core_baseline"
# Captured once: `_run` rewrites POSTGRES_URL to a per-test database, so the base
# URL must not be re-read from the environment afterwards.
_BASE_URL = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")


def _sync(url: str) -> str:
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _reachable(url: str) -> bool:
    try:
        create_engine(_sync(url)).connect().close()
    except Exception:
        return False
    return True


@pytest.fixture
def temp_db() -> Iterator[str]:
    if not _reachable(_BASE_URL):
        pytest.skip("POSTGRES_URL not reachable")
    name = f"coremig_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_sync(_BASE_URL), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(_BASE_URL).set(database=name).render_as_string(hide_password=False)
    finally:
        os.environ["POSTGRES_URL"] = _BASE_URL  # undo `_run`'s rewrite
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _run(url: str, fn, *args: str) -> None:
    os.environ["POSTGRES_URL"] = url
    fn(Config(str(_CORE_INI)), *args)


def test_downgrade_on_a_shared_database_preserves_product_tables(temp_db: str) -> None:
    eng = create_engine(_sync(temp_db), isolation_level="AUTOCOMMIT")
    sentinel = str(uuid.uuid4())
    with eng.connect() as conn:
        # Simulate a database the product chain owns: its version table plus a
        # corpus table carrying real data.
        conn.execute(text("CREATE TABLE public.alembic_version (version_num varchar PRIMARY KEY)"))
        conn.execute(text("INSERT INTO public.alembic_version VALUES ('0123')"))
        conn.execute(text("CREATE TABLE public.versions (id uuid PRIMARY KEY)"))
        conn.execute(text("INSERT INTO public.versions (id) VALUES (:i)"), {"i": sentinel})

    _run(temp_db, command.stamp, _BASELINE)  # adopt: records the core version, no DDL
    _run(temp_db, command.downgrade, "base")  # the dangerous path

    with eng.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('public.versions')")).scalar() is not None, (
            "downgrade dropped a product-owned table on a shared database"
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM public.versions WHERE id = :i"), {"i": sentinel}
            ).scalar()
            == 1
        ), "downgrade destroyed product data on a shared database"
        assert (
            conn.execute(text("SELECT count(*) FROM public.alembic_version_core")).scalar() == 0
        ), "downgrade left the core version stamped"


def test_standalone_upgrade_then_downgrade_round_trips(temp_db: str) -> None:
    # Needs pg_textsearch for the BM25 index; skip where the extension is absent
    # rather than fail (the CI postgres image ships it).
    with create_engine(_sync(temp_db)).connect() as conn:
        if (
            conn.execute(
                text("SELECT count(*) FROM pg_available_extensions WHERE name = 'pg_textsearch'")
            ).scalar()
            == 0
        ):
            pytest.skip("pg_textsearch not available")

    _run(temp_db, command.upgrade, "head")
    with create_engine(_sync(temp_db)).connect() as conn:
        assert conn.execute(text("SELECT to_regclass('public.versions')")).scalar() is not None
        assert (
            conn.execute(text("SELECT to_regclass('public.provisions_bm25_idx')")).scalar()
            is not None
        )

    _run(temp_db, command.downgrade, "base")
    with create_engine(_sync(temp_db)).connect() as conn:
        # No product chain here, so the baseline built these and the downgrade drops them.
        assert conn.execute(text("SELECT to_regclass('public.versions')")).scalar() is None


def test_upgrade_refuses_an_incomplete_product_schema(temp_db: str) -> None:
    # `versions` present but the baseline head is not (no BM25 index): the guard
    # must refuse to stamp rather than report head over an older schema.
    with create_engine(_sync(temp_db), isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(text("CREATE TABLE public.versions (id uuid PRIMARY KEY)"))
    with pytest.raises(Exception, match="before adopting the core chain"):
        _run(temp_db, command.upgrade, "head")
    with create_engine(_sync(temp_db)).connect() as conn:
        assert (
            conn.execute(text("SELECT to_regclass('public.alembic_version_core')")).scalar() is None
        )


def test_offline_sql_mode_is_rejected() -> None:
    os.environ.setdefault("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
    with pytest.raises(Exception, match="offline"):
        command.upgrade(Config(str(_CORE_INI)), "head", sql=True)
