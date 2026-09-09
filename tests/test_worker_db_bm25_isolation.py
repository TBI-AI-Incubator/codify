"""The BM25 re-OID that makes xdist worker clones safe.

pg_textsearch keys per-index state on index OID alone, cluster-wide, and a
template clone copies catalogs verbatim, so a clone that keeps the template's
OIDs corrupts both databases. `_reindex_bm25` breaks that tie. It once named
one index; a second arrived two days later and inherited the collision
until this test existed.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from codify.testing import _reindex_bm25

pytestmark = pytest.mark.integration

_BASE_URL = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")


def _sync(url: str) -> str:
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _bm25_oids(url: str) -> dict[str, int]:
    eng = create_engine(_sync(url), isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as conn:
            return {
                r[0]: r[1]
                for r in conn.execute(
                    text(
                        "SELECT c.relname, c.oid FROM pg_class c "
                        "JOIN pg_am am ON am.oid = c.relam WHERE am.amname = 'bm25'"
                    )
                )
            }
    finally:
        eng.dispose()


@pytest.fixture
def bm25_db() -> Iterator[str]:
    """Two BM25 indexes under names the product does not use, one of them in a
    schema outside search_path so an unqualified DROP cannot reach it."""
    try:
        create_engine(_sync(_BASE_URL)).connect().close()
    except Exception:
        pytest.skip("POSTGRES_URL not reachable")
    name = f"bm25iso_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_sync(_BASE_URL), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(_BASE_URL).set(database=name).render_as_string(hide_password=False)
    eng = create_engine(_sync(url), isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_textsearch"))
            conn.execute(text("CREATE SCHEMA elsewhere"))
            for schema, table in (("public", "alpha"), ("elsewhere", "beta")):
                conn.execute(text(f"CREATE TABLE {schema}.{table} (id int, tokens text)"))
                conn.execute(
                    text(
                        f"CREATE INDEX {table}_tokens_bm25 ON {schema}.{table} "
                        "USING bm25 (tokens) WITH (text_config='simple', k1=1.2, b=0.75)"
                    )
                )
        eng.dispose()
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_every_bm25_index_gets_a_fresh_oid(bm25_db: str) -> None:
    """Neither index is named like the product's, so an implementation that
    enumerates by name touches nothing, and one sits outside search_path, so an
    unqualified DROP cannot reach it either."""
    before = _bm25_oids(bm25_db)
    assert len(before) == 2, f"fixture did not build both indexes: {sorted(before)}"

    _reindex_bm25(make_url(bm25_db))

    after = _bm25_oids(bm25_db)
    assert set(after) == set(before), "an index was dropped and not rebuilt"
    stale = sorted(n for n in before if after[n] == before[n])
    assert not stale, f"kept the template's OID, so a clone still collides: {stale}"
