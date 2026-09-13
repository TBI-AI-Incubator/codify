"""Ownership contracts for the Core search-term lexicon migration."""

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

_CORE_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_PRE_LEXICON = "0018_annex_admission"
_LEXICON = "0019_search_term_lexicon"
_BASE_URL = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")


def _sync(url: str) -> str:
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _reachable(url: str) -> bool:
    engine = create_engine(_sync(url))
    try:
        engine.connect().close()
    except Exception:
        return False
    finally:
        engine.dispose()
    return True


@pytest.fixture
def temp_db() -> Iterator[str]:
    if not _reachable(_BASE_URL):
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail("REQUIRE_DB=1 but POSTGRES_URL is not reachable")
        pytest.skip("POSTGRES_URL not reachable")

    name = f"corelex_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_sync(_BASE_URL), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield (make_url(_BASE_URL).set(database=name).render_as_string(hide_password=False))
    finally:
        os.environ["POSTGRES_URL"] = _BASE_URL
        # Every per-database engine below is disposed before this point. Do not
        # hide leaked sessions with FORCE: a leak should make cleanup fail.
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def _run(url: str, fn, *args: str) -> None:
    previous = os.environ.get("POSTGRES_URL")
    os.environ["POSTGRES_URL"] = url
    try:
        fn(Config(str(_CORE_INI)), *args)
    finally:
        if previous is None:
            os.environ.pop("POSTGRES_URL", None)
        else:
            os.environ["POSTGRES_URL"] = previous


def _prepare_pre_0019(url: str) -> None:
    _run(url, command.stamp, _PRE_LEXICON)
    engine = create_engine(_sync(url))
    try:
        with engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE public.provisions (id uuid PRIMARY KEY, search_tokens text)")
            )
    finally:
        engine.dispose()


def _create_platform_version(conn, version: str) -> None:
    conn.execute(text("CREATE TABLE public.alembic_version (version_num varchar PRIMARY KEY)"))
    conn.execute(
        text("INSERT INTO public.alembic_version VALUES (:version)"),
        {"version": version},
    )


def _create_compatible_lexicon(conn) -> None:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    conn.execute(
        text(
            "CREATE TABLE public.search_terms ("
            "term text PRIMARY KEY, ndoc integer NOT NULL DEFAULT 0)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX search_terms_trgm_idx ON public.search_terms "
            "USING gin (term gin_trgm_ops)"
        )
    )


def test_standalone_builds_distinct_document_frequency(temp_db: str) -> None:
    _prepare_pre_0019(temp_db)
    engine = create_engine(_sync(temp_db))
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO public.provisions VALUES (:id, :tokens)"),
                [
                    {"id": uuid.uuid4(), "tokens": "shared shared first"},
                    {"id": uuid.uuid4(), "tokens": "shared second"},
                ],
            )
        _run(temp_db, command.upgrade, _LEXICON)
        with engine.connect() as conn:
            rows = dict(
                conn.execute(text("SELECT term, ndoc FROM public.search_terms")).tuples().all()
            )
            assert rows == {"first": 1, "second": 1, "shared": 2}
            assert (
                conn.execute(text("SELECT to_regclass('public.search_terms_trgm_idx')")).scalar()
                is not None
            )
    finally:
        engine.dispose()


def test_product_before_0129_is_refused_without_advancing_core(temp_db: str) -> None:
    _prepare_pre_0019(temp_db)
    engine = create_engine(_sync(temp_db))
    try:
        with engine.begin() as conn:
            _create_platform_version(conn, "0128")
        with pytest.raises(RuntimeError, match="through 0129"):
            _run(temp_db, command.upgrade, _LEXICON)
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM public.alembic_version_core")
                ).scalar_one()
                == _PRE_LEXICON
            )
            assert conn.execute(text("SELECT to_regclass('public.search_terms')")).scalar() is None
    finally:
        engine.dispose()


def test_product_lexicon_is_adopted_and_retained_across_downgrade(temp_db: str) -> None:
    _prepare_pre_0019(temp_db)
    engine = create_engine(_sync(temp_db))
    try:
        with engine.begin() as conn:
            _create_platform_version(conn, "0132")
            _create_compatible_lexicon(conn)
            conn.execute(text("INSERT INTO public.search_terms VALUES ('sentinel', 7)"))
            index_oid = conn.execute(
                text("SELECT 'public.search_terms_trgm_idx'::regclass::oid")
            ).scalar_one()

        _run(temp_db, command.upgrade, _LEXICON)
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT ndoc FROM public.search_terms WHERE term = 'sentinel'")
                ).scalar_one()
                == 7
            )
            assert (
                conn.execute(
                    text("SELECT 'public.search_terms_trgm_idx'::regclass::oid")
                ).scalar_one()
                == index_oid
            )

        _run(temp_db, command.downgrade, _PRE_LEXICON)
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT ndoc FROM public.search_terms WHERE term = 'sentinel'")
                ).scalar_one()
                == 7
            )
            assert (
                conn.execute(
                    text("SELECT 'public.search_terms_trgm_idx'::regclass::oid")
                ).scalar_one()
                == index_oid
            )

        _run(temp_db, command.upgrade, _LEXICON)
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT ndoc FROM public.search_terms WHERE term = 'sentinel'")
                ).scalar_one()
                == 7
            )
            assert (
                conn.execute(
                    text("SELECT 'public.search_terms_trgm_idx'::regclass::oid")
                ).scalar_one()
                == index_oid
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("defect", ["wrong_type", "missing_index"])
def test_incompatible_product_lexicon_is_refused(temp_db: str, defect: str) -> None:
    _prepare_pre_0019(temp_db)
    engine = create_engine(_sync(temp_db))
    try:
        with engine.begin() as conn:
            _create_platform_version(conn, "0132")
            if defect == "wrong_type":
                conn.execute(
                    text(
                        "CREATE TABLE public.search_terms ("
                        "term text PRIMARY KEY, ndoc text NOT NULL DEFAULT '0')"
                    )
                )
            else:
                conn.execute(
                    text(
                        "CREATE TABLE public.search_terms ("
                        "term text PRIMARY KEY, ndoc integer NOT NULL DEFAULT 0)"
                    )
                )

        with pytest.raises(RuntimeError, match="Incompatible|Missing"):
            _run(temp_db, command.upgrade, _LEXICON)
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM public.alembic_version_core")
                ).scalar_one()
                == _PRE_LEXICON
            )
    finally:
        engine.dispose()
