"""Offline selection controls; PostgreSQL index plans need separate validation."""

from __future__ import annotations

import sqlite3
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from codify.storage.retrieval import _DENSE_ONLY_SQL, _HYBRID_SQL, hybrid_search


@pytest.mark.parametrize("hybrid", [False, True])
@pytest.mark.parametrize("fallback", [None, "old", "new"])
def test_indexable_vector_selection_preserves_preferred_and_scope(hybrid, fallback):
    sql = str(_HYBRID_SQL if hybrid else _DENSE_ONLY_SQL)
    # Guard the performance regression: ORDER BY must see the indexed relation,
    # not a separately limited embedding lookup for each provision.
    assert "JOIN LATERAL" not in sql
    if hybrid:
        sql = "WITH " + sql[sql.index("    vec AS (") : sql.index("    fused AS (")]
        sql = sql.rstrip().rstrip(",") + " SELECT id FROM vec ORDER BY rn"
    # SQLite exercises the actual selection predicates with scalar distances;
    # it cannot establish pgvector recall, PostgreSQL planning or latency.
    sql = sql.replace("e.embedding <=> :query_vec", "abs(e.embedding - :query_vec)")
    sql = sql.replace("e.version_id = ANY(:version_ids)", "e.version_id = :version_id")
    sql = sql.replace("e.jurisdiction_id = ANY(:jurisdiction_ids)", "e.jurisdiction_id = 1")
    assert "e.jurisdiction_id = 1" in sql
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            CREATE TABLE provisions (
                id INTEGER PRIMARY KEY, akn_eid TEXT, text TEXT, version_id INTEGER,
                akn_type TEXT, normative BOOLEAN, excluded_from_pool BOOLEAN
            );
            CREATE TABLE provision_embeddings (
                provision_id INTEGER, model_id TEXT, embedding REAL,
                version_id INTEGER, jurisdiction_id INTEGER,
                UNIQUE(provision_id, model_id, jurisdiction_id)
            );
            INSERT INTO provisions VALUES
                (1,'a','both',1,'article',1,0),
                (2,'b','fallback only',1,'article',1,0),
                (3,'c','preferred only',1,'article',1,0),
                (4,'d','other version',2,'article',1,0),
                (5,'e','excluded',1,'article',1,1),
                (6,'f','non normative',1,'article',0,0),
                (7,'g','wrong type',1,'paragraph',1,0),
                (8,'h','unrelated model',1,'article',1,0),
                (9,'i','other jurisdiction',1,'article',1,0);
            INSERT INTO provision_embeddings VALUES
                (1,'new',0.9,1,1),(1,'old',0.01,1,1),(2,'old',0.2,1,1),(3,'new',0.3,1,1),
                (4,'new',0.01,2,1),(5,'new',0.01,1,1),(6,'new',0.01,1,1),(7,'new',0.01,1,1),
                (8,'unrelated',0.01,1,1),(9,'new',0.001,1,2);
        """)
        rows = db.execute(
            sql,
            dict(
                query_vec=0.0,
                version_id=1,
                model_id="new",
                fallback_model_id=fallback,
                akn_type="article",
                include_non_normative=False,
                pool=10,
                k=10,
            ),
        ).fetchall()
    assert [row[0] for row in rows] == ([2, 3, 1] if fallback == "old" else [3, 1])


@pytest.mark.parametrize("tokens", ["", "example"])
async def test_both_vector_paths_enable_transaction_local_iterative_scan(tokens):
    session = AsyncMock()
    session.execute.return_value.all = lambda: []
    with patch("codify.storage.retrieval.query_tokens_for", return_value=tokens):
        assert (
            await hybrid_search(
                session,
                query_vec=[0.0] * 768,
                query_text="example",
                version_ids=[uuid.uuid4()],
                k=10,
                candidate_pool=200,
                rrf_k=60,
                model_id="new",
            )
            == []
        )
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert statements[0] == "SET LOCAL hnsw.iterative_scan = 'relaxed_order'"
    # The setting, the partition lookup, the search.
    assert len(statements) == 3


async def test_empty_scope_does_not_execute_or_change_settings():
    session = AsyncMock()
    assert (
        await hybrid_search(
            session,
            query_vec=[0.0] * 768,
            query_text="example",
            version_ids=[],
            k=10,
            candidate_pool=200,
            rrf_k=60,
            model_id="new",
        )
        == []
    )
    session.execute.assert_not_awaited()
