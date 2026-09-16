"""Coverage counts versions, including fallback-only versions, once each."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from codify.storage.versions import _COUNT_EMBEDDED_SQL, count_embedded_versions
from codify.testing import postgres_url

# CTEs shadow corpus tables: these controls need PostgreSQL syntax but neither
# migrations nor persisted fixtures. No customer data or providers are used.
FIXTURE_SQL = """
WITH provisions(id, version_id) AS (
  VALUES (1, '00000000-0000-0000-0000-000000000001'::uuid),
         (2, '00000000-0000-0000-0000-000000000001'::uuid),
         (3, '00000000-0000-0000-0000-000000000002'::uuid),
         (4, '00000000-0000-0000-0000-000000000003'::uuid),
         (5, '00000000-0000-0000-0000-000000000004'::uuid)
), provision_embeddings(provision_id, model_id) AS (
  VALUES (1, 'new'), (1, 'old'), (2, 'new'), (3, 'old'), (4, 'unrelated')
)
"""

CASES = [
    ([1, 2, 3, 4], None, 1),
    ([1, 2, 3, 4], "old", 2),
    ([1, 1, 2, 2], "old", 2),
    ([1, 2, 3, 4], "new", 1),
    ([2], None, 0),
    ([2], "old", 1),
    ([3, 4, 5], "old", 0),
    ([], "old", 0),
]


@pytest.mark.integration
@pytest.mark.parametrize("ids,fallback,expected", CASES)
async def test_scoped_coverage_sql(ids, fallback, expected):
    engine = create_async_engine(postgres_url())
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            stmt = text(FIXTURE_SQL + str(_COUNT_EMBEDDED_SQL)).bindparams(
                *_COUNT_EMBEDDED_SQL._bindparams.values()
            )
            result = await conn.execute(
                stmt,
                dict(
                    version_ids=[uuid.UUID(int=i) for i in ids],
                    model_id="new",
                    fallback_model_id=fallback,
                ),
            )
            assert result.scalar_one() == expected
            await conn.rollback()
    finally:
        await engine.dispose()


async def test_empty_scope_does_not_query():
    session = AsyncMock()
    assert await count_embedded_versions(session, [], "new", "old") == 0
    session.execute.assert_not_awaited()


async def test_count_passes_both_model_identities_and_scope():
    session = AsyncMock()
    session.execute.return_value = Mock(scalar_one=Mock(return_value=2))
    ids = [uuid.uuid4(), uuid.uuid4()]
    assert await count_embedded_versions(session, ids, "new", "old") == 2
    assert session.execute.await_args.args[1] == dict(
        version_ids=ids, model_id="new", fallback_model_id="old"
    )


def test_coverage_probe_stops_after_one_embedding_per_distinct_version():
    sql = str(_COUNT_EMBEDDED_SQL)
    assert "SELECT DISTINCT unnest(:version_ids)" in sql
    assert "JOIN LATERAL" in sql
    assert "LIMIT 1" in sql
    assert "p.version_id = scoped.id" in sql
