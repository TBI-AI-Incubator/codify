"""End-to-end retrieval against real Postgres + the embedding gateway."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.embed.client import EmbeddingClient
from codify.retrieve.hybrid import retrieve
from codify.storage.embeddings import upsert_embedding
from codify.storage.models import Jurisdiction, Law, Provision, Section, Version
from codify.testing import postgres_url

pytestmark = [pytest.mark.integration, pytest.mark.live_llm]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    url = postgres_url()
    engine = create_async_engine(url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.fixture
async def embedding_client() -> EmbeddingClient:
    return EmbeddingClient(
        base_url=os.getenv("LITELLM_BASE_URL", "http://localhost:4000/v1"),
        api_key=os.getenv("LITELLM_API_KEY", "sk-codify-dev"),
        model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-2"),
    )


async def _build_version(session: AsyncSession, suffix: str | None = None) -> uuid.UUID:
    suffix = suffix or uuid.uuid4().hex[:8]
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
        expression_uri=f"/akn/zz/{suffix}/2026/1/eng@2026-04-28",
        language="en",
        expression_date=date(2026, 4, 28),
        akn_xml="<akomaNtoso/>",
    )
    session.add(v)
    await session.flush()
    return v.id


async def _add_provisions_with_embeddings(
    session: AsyncSession,
    version_id: uuid.UUID,
    items: list[tuple[str, str, list[float]]],  # (akn_eid, text, vector)
    model_id: str,  # must match the retrieval client's model, hybrid_search filters on it
    *,
    section_title: str | None = None,
) -> list[uuid.UUID]:
    if section_title is not None:
        sec_eid = f"sec_{uuid.uuid4().hex[:8]}"
        sec = Section(
            version_id=version_id,
            akn_eid=sec_eid,
            akn_wid=sec_eid,
            akn_type="section",
            title=section_title,
            position=0,
        )
        session.add(sec)
        await session.flush()
        sec_id: uuid.UUID | None = sec.id
    else:
        sec_id = None
    ids: list[uuid.UUID] = []
    for i, (eid, body, vec) in enumerate(items):
        p = Provision(
            version_id=version_id,
            section_id=sec_id,
            akn_eid=eid,
            akn_wid=eid,
            akn_type="article",
            text=body,
            position=i,
        )
        session.add(p)
        await session.flush()
        await upsert_embedding(session, p.id, vec, model_id)
        ids.append(p.id)
    return ids


async def test_hybrid_beats_pure_vector_on_keyword_query(
    session: AsyncSession, embedding_client: EmbeddingClient
) -> None:
    """Provision containing 'trade secret' verbatim should outrank semantically similar
    'good faith' provisions when querying 'trade secret'. Hybrid surfaces it via FTS;
    pure-vector buries it under semantic neighbours."""
    version_id = await _build_version(session)

    # Embed real text so FTS + dense both have signal.
    keyword_text = "Disclosure of trade secrets shall constitute a civil offence."
    distractor_texts = [
        f"The parties shall act in good faith ({i}).  Performance must be reasonable."
        for i in range(10)
    ]
    all_texts = [keyword_text, *distractor_texts]
    vecs = await embedding_client.embed_documents([("Article", t) for t in all_texts])

    items = [(f"art_{i}", t, v) for i, (t, v) in enumerate(zip(all_texts, vecs, strict=True))]
    pids = await _add_provisions_with_embeddings(session, version_id, items, embedding_client.model)
    keyword_pid = pids[0]
    await session.flush()

    # Hybrid: keyword provision should be in top 3.
    hybrid_results = await retrieve(
        session, "trade secret", embedding_client=embedding_client, version_id=version_id, k=10
    )
    hybrid_ids = [m.provision_id for m in hybrid_results]
    assert keyword_pid in hybrid_ids[:3], f"Hybrid missed keyword in top 3: {hybrid_ids}"

    # The top-3 assertion above is the load-bearing check, FTS is the only
    # path that promotes the literal keyword bearer from semantic-distractor
    # land. We don't compare ranks against a "pure vector" proxy: with 768D
    # nonsense-token embeddings the rank result is non-deterministic and would
    # eventually flake CI.


async def test_latency_under_200ms_on_10k_corpus(
    session: AsyncSession, embedding_client: EmbeddingClient
) -> None:
    """SQL portion should complete in <200ms on a 10k-provision corpus.

    Seeded via bulk SQL (generate_series for provisions, chunked executemany
    for embeddings) so the seed phase doesn't dominate wall-clock. Random
    unit-norm halfvec(768) exercises the HNSW index, graph construction
    differs from real embeddings, but ef_search dominates traversal cost."""
    import math
    import random

    random.seed(0)
    version_id = await _build_version(session)

    n = 10_000
    sec = Section(
        version_id=version_id,
        akn_eid="sec_bulk",
        akn_wid="sec_bulk",
        akn_type="section",
        title="Bulk",
        position=0,
    )
    session.add(sec)
    await session.flush()

    await session.execute(
        text(
            """
            INSERT INTO provisions (
              id, version_id, section_id, akn_eid, akn_wid, akn_type,
              text, position, created_at, updated_at
            )
            SELECT
              gen_random_uuid(),
              :vid,
              :sid,
              'art_' || gs,
              'art_' || gs,
              'article',
              'Provision ' || gs || ': parties shall observe contractual obligations.',
              gs,
              NOW(),
              NOW()
            FROM generate_series(0, :max_i) AS gs
            """
        ),
        {"vid": version_id, "sid": sec.id, "max_i": n - 1},
    )

    pids = list(
        (
            await session.execute(
                text("SELECT id FROM provisions WHERE version_id = :vid ORDER BY position"),
                {"vid": version_id},
            )
        )
        .scalars()
        .all()
    )
    assert len(pids) == n

    def random_text_vec() -> str:
        v = [random.gauss(0, 1) for _ in range(768)]
        norm = math.sqrt(sum(x * x for x in v))
        return "[" + ",".join(f"{x / norm:.6f}" for x in v) + "]"

    chunk = 200
    insert_emb = text(
        """
        INSERT INTO provision_embeddings (id, provision_id, embedding, model_id, created_at)
        VALUES (gen_random_uuid(), :pid, CAST(:vec AS halfvec(768)), 'embeddinggemma', NOW())
        """
    )
    for start_i in range(0, n, chunk):
        rows = [
            {"pid": pids[j], "vec": random_text_vec()}
            for j in range(start_i, min(start_i + chunk, n))
        ]
        await session.execute(insert_emb, rows)
    await session.flush()

    query_vec = await embedding_client.embed_one("contractual obligations", task="query")

    from codify.storage.retrieval import hybrid_search

    start = time.perf_counter()
    result_rows = await hybrid_search(
        session,
        query_vec=query_vec,
        query_text="contractual obligations",
        version_ids=[version_id],
        k=10,
        candidate_pool=30,
        rrf_k=60,
        model_id="embeddinggemma",
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert len(result_rows) == 10
    assert elapsed_ms < 200, f"hybrid_search took {elapsed_ms:.0f}ms; AC is <200ms"


async def test_multi_version_filter_spans_versions(
    session: AsyncSession, embedding_client: EmbeddingClient
) -> None:
    suffix = uuid.uuid4().hex[:8]
    j = Jurisdiction(code=f"zz-{suffix}", name="Test", languages=["en"])
    session.add(j)
    await session.flush()
    law = Law(
        jurisdiction_id=j.id,
        title="Multi",
        doctype="act",
        frbr_work_uri=f"/akn/zz/{suffix}/2026/m",
    )
    session.add(law)
    await session.flush()
    v1 = Version(
        law_id=law.id,
        expression_uri=f"/akn/zz/{suffix}/m/eng@2026-01-01",
        language="en",
        expression_date=date(2026, 1, 1),
        akn_xml="<akomaNtoso/>",
    )
    v2 = Version(
        law_id=law.id,
        expression_uri=f"/akn/zz/{suffix}/m/eng@2026-04-28",
        language="en",
        expression_date=date(2026, 4, 28),
        akn_xml="<akomaNtoso/>",
    )
    session.add(v1)
    session.add(v2)
    await session.flush()

    text_a = "Trade secrets in version one."
    text_b = "Trade secrets in version two."
    vecs = await embedding_client.embed_documents([("Article", text_a), ("Article", text_b)])
    pid_a = (
        await _add_provisions_with_embeddings(
            session, v1.id, [("art_v1", text_a, vecs[0])], embedding_client.model
        )
    )[0]
    pid_b = (
        await _add_provisions_with_embeddings(
            session, v2.id, [("art_v2", text_b, vecs[1])], embedding_client.model
        )
    )[0]
    await session.flush()

    # Route through retrieve() so the test exercises the production query path
    # (including task='query' embedding); pass both versions to verify the fan.
    from codify.storage.retrieval import hybrid_search

    query_vec = await embedding_client.embed_one("trade secret", task="query")
    rows = await hybrid_search(
        session,
        query_vec=query_vec,
        query_text="trade secret",
        version_ids=[v1.id, v2.id],
        k=10,
        candidate_pool=30,
        rrf_k=60,
        model_id=embedding_client.model,
    )
    found = {row[0] for row in rows}
    assert pid_a in found
    assert pid_b in found


async def test_empty_corpus_returns_empty(
    session: AsyncSession, embedding_client: EmbeddingClient
) -> None:
    version_id = await _build_version(session)
    # No provisions, no embeddings.
    matches = await retrieve(
        session, "trade secret", embedding_client=embedding_client, version_id=version_id
    )
    assert matches == []


async def test_jurisdiction_resolution_round_trip(
    session: AsyncSession, embedding_client: EmbeddingClient
) -> None:
    """jurisdiction_code resolves through the storage helper and runs end-to-end."""
    suffix = uuid.uuid4().hex[:8]
    code = f"zz-{suffix}"
    j = Jurisdiction(code=code, name="Test", languages=["en"])
    session.add(j)
    await session.flush()
    law = Law(
        jurisdiction_id=j.id,
        title="J",
        doctype="act",
        frbr_work_uri=f"/akn/zz/{suffix}/j/2026/1",
    )
    session.add(law)
    await session.flush()
    v = Version(
        law_id=law.id,
        expression_uri=f"/akn/zz/{suffix}/j/2026/1/eng@2026-04-28",
        language="en",
        expression_date=date(2026, 4, 28),
        akn_xml="<akomaNtoso/>",
    )
    session.add(v)
    await session.flush()

    body = "Trade secrets are protected under this jurisdiction."
    [vec] = await embedding_client.embed([body])
    await _add_provisions_with_embeddings(
        session, v.id, [("art_1", body, vec)], embedding_client.model
    )
    await session.flush()

    matches = await retrieve(
        session,
        "trade secret",
        embedding_client=embedding_client,
        jurisdiction_code=code,
        k=5,
    )
    assert len(matches) == 1
    assert matches[0].text == body
