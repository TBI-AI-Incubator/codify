"""embed_provisions_cached, cache read/write against version_unit_embeddings."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.storage.embeddings import embed_provisions_cached
from codify.storage.models import (
    Jurisdiction,
    Law,
    Provision,
    Version,
    VersionUnitEmbedding,
)
from codify.testing import postgres_url

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _FakeEmbedClient:
    """Counts embed_documents calls; returns deterministic 768-dim vectors."""

    def __init__(self) -> None:
        self.calls = 0
        self.embedded = 0
        self.model = "fake-embedding"

    async def embed_documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
        self.calls += 1
        self.embedded += len(items)
        return [[float(len(text)) / 1000.0] * 768 for _title, text in items]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    url = postgres_url()
    engine = create_async_engine(url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _synthetic_version(session: AsyncSession, n: int) -> tuple[uuid.UUID, list[str]]:
    suffix = uuid.uuid4().hex[:8]
    j = Jurisdiction(code=f"zz-{suffix}", name="Test", languages=["en"])
    session.add(j)
    await session.flush()
    law = Law(
        jurisdiction_id=j.id,
        title=f"Synthetic Act {suffix}",
        doctype="act",
        frbr_work_uri=f"/akn/zz/{suffix}/2026/1",
    )
    session.add(law)
    await session.flush()
    v = Version(
        law_id=law.id,
        expression_uri=f"/akn/zz/{suffix}/2026/1/eng@2026-04-27",
        language="en",
        expression_date=date(2026, 4, 27),
        akn_xml="<akomaNtoso/>",
    )
    session.add(v)
    await session.flush()
    eids = [f"art_{i}_{suffix}" for i in range(n)]
    for i, eid in enumerate(eids):
        session.add(
            Provision(
                version_id=v.id,
                akn_eid=eid,
                akn_wid=eid,
                akn_type="article",
                text=f"Provision {i}.",
                position=i,
            )
        )
    await session.flush()
    return v.id, eids


async def _embedding_count(session: AsyncSession, version_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(VersionUnitEmbedding)
            .where(VersionUnitEmbedding.version_id == version_id)
        )
    ).scalar_one()


async def test_cold_call_embeds_then_warm_call_reads_cache(session: AsyncSession) -> None:
    version_id, eids = await _synthetic_version(session, 6)
    items = [(eid, "", f"Provision {i}.") for i, eid in enumerate(eids)]
    client = _FakeEmbedClient()

    # Cold: all 6 are misses → one embed call, 6 rows persisted.
    vecs = await embed_provisions_cached(session, version_id, items, client=client)
    assert len(vecs) == 6
    assert client.calls == 1
    assert client.embedded == 6
    await session.commit()
    assert await _embedding_count(session, version_id) == 6

    # Warm: every provision cached → embed client untouched. Vectors
    # round-trip through pgvector's half-precision storage, so compare
    # approximately rather than exact.
    client2 = _FakeEmbedClient()
    vecs2 = await embed_provisions_cached(session, version_id, items, client=client2)
    assert client2.calls == 0
    assert len(vecs2) == len(vecs)
    for got, want in zip(vecs2, vecs, strict=True):
        assert got[0] == pytest.approx(want[0], abs=1e-3)


async def test_partial_cache_embeds_only_misses(session: AsyncSession) -> None:
    version_id, eids = await _synthetic_version(session, 4)
    items = [(eid, "", f"Provision {i}.") for i, eid in enumerate(eids)]
    client = _FakeEmbedClient()
    await embed_provisions_cached(session, version_id, items[:2], client=client)
    await session.commit()
    assert client.embedded == 2

    # Now request all 4, only the 2 new ones should be embedded.
    client2 = _FakeEmbedClient()
    vecs = await embed_provisions_cached(session, version_id, items, client=client2)
    assert len(vecs) == 4
    assert client2.embedded == 2, "only the uncached provisions should be embedded"


async def test_duplicate_eids_embed_each_position_without_cache(session: AsyncSession) -> None:
    """Duplicate eIds within one items list must not collapse onto a single
    cache row, each position is embedded fresh and skipped from the cache."""
    version_id, eids = await _synthetic_version(session, 1)
    items = [
        (eids[0], "", "first text"),
        (eids[0], "", "second text"),  # duplicate eId, different text
    ]
    client = _FakeEmbedClient()
    vecs = await embed_provisions_cached(session, version_id, items, client=client)
    assert len(vecs) == 2
    # Both positions embedded fresh; lengths differ so vectors differ.
    assert vecs[0] != vecs[1]
    await session.commit()
    # Only one cache row exists (for the first occurrence); the duplicate
    # was deliberately skipped.
    assert await _embedding_count(session, version_id) == 1


async def test_empty_eid_is_embedded_but_never_cached(session: AsyncSession) -> None:
    """An empty `akn_eid` would collapse every eid-less unit onto the same
    cache row, so we deliberately don't cache it. Should still return a
    fresh vector per position."""
    version_id, _ = await _synthetic_version(session, 0)
    items = [("", "", "anonymous one"), ("", "", "anonymous two")]
    client = _FakeEmbedClient()
    vecs = await embed_provisions_cached(session, version_id, items, client=client)
    assert len(vecs) == 2
    await session.commit()
    assert await _embedding_count(session, version_id) == 0


async def test_caches_eids_without_a_provision_row(session: AsyncSession) -> None:
    """`iter_assessable` yields more units than `document_to_rows` persists.
    Those overflow eIds must still cache, that's the whole point of keying
    on `(version_id, akn_eid)` rather than `provision_id`."""
    version_id, eids = await _synthetic_version(session, 2)
    # Mix in two eIds the mapper never persisted as Provision rows.
    orphan_eids = ["art_orphan_1", "art_orphan_2"]
    all_items = [(eid, "", f"Persisted {i}.") for i, eid in enumerate(eids)] + [
        (eid, "", f"Orphan {i}.") for i, eid in enumerate(orphan_eids)
    ]
    client = _FakeEmbedClient()

    vecs = await embed_provisions_cached(session, version_id, all_items, client=client)
    assert len(vecs) == 4
    assert client.embedded == 4
    await session.commit()
    # All four, including the orphans, landed in the cache.
    assert await _embedding_count(session, version_id) == 4

    client2 = _FakeEmbedClient()
    vecs2 = await embed_provisions_cached(session, version_id, all_items, client=client2)
    assert client2.calls == 0, "orphans should hit the cache on the second call"
    assert len(vecs2) == 4
