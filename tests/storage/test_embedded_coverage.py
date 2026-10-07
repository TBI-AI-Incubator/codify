"""Coverage counts the versions whose embedding finished, each once."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.akn.io import parse_akn
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.storage.embeddings import embed_and_stamp
from codify.storage.repository import save_document
from codify.storage.versions import count_embedded_versions
from codify.testing import postgres_url


class _FixedVectors:
    model = "test-model"

    async def embed_documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
        return [[1.0] + [0.0] * 767 for _ in items]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _save(session: AsyncSession, code: str) -> uuid.UUID:
    number = uuid.uuid4().hex[:8]
    xml = parse_to_akn(
        "BODY\n  ARTICLE 1\n    Body text.\n",
        country=code,
        doctype="act",
        number=number,
        date="2020-01-01",
        language="eng",
    )
    return await save_document(
        session, parse_akn(xml), jurisdiction_code=code, law_title=number, akn_xml=xml
    )


@pytest.mark.integration
async def test_counts_stamped_versions_in_scope_once_each(session: AsyncSession) -> None:
    a, b = "xa", "xy"
    embedded_a, unembedded_a, embedded_b, outside = (
        await _save(session, a),
        await _save(session, a),
        await _save(session, b),
        await _save(session, b),
    )
    for vid in (embedded_a, embedded_b, outside):
        await embed_and_stamp(session, vid, client=_FixedVectors(), path_context=False)  # type: ignore[arg-type]

    scope = [embedded_a, unembedded_a, embedded_b, embedded_b]
    stamped = (
        await session.execute(
            text("SELECT count(*) FROM versions WHERE id = ANY(:ids) AND embedded_at IS NOT NULL"),
            {"ids": scope},
        )
    ).scalar_one()
    counted = await count_embedded_versions(session, scope, "test-model", None)
    assert (counted, stamped) == (2, 2)
    assert await count_embedded_versions(session, [unembedded_a], "test-model") == 0


async def test_empty_scope_does_not_query() -> None:
    session = AsyncMock()
    assert await count_embedded_versions(session, [], "new", "old") == 0
    session.execute.assert_not_awaited()


async def test_count_binds_the_scope_as_one_array() -> None:
    session = AsyncMock()
    session.execute.return_value = Mock(scalar_one=Mock(return_value=2))
    ids = [uuid.uuid4(), uuid.uuid4()]
    assert await count_embedded_versions(session, ids, "new", "old") == 2
    assert session.execute.await_args.args[1] == {"version_ids": ids}
