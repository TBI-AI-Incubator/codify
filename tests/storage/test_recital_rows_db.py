"""Recital rows against a database: backfill, embedding and lookup."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.akn.io import parse_akn
from codify.storage.documents import get_version_document
from codify.storage.embeddings import embed_and_stamp
from codify.storage.models import Provision, ProvisionEmbedding
from codify.storage.provisions import find_provision_id_by_eid
from codify.storage.repository import add_missing_recital_rows, save_document
from codify.testing import postgres_url
from tests.storage.test_recital_rows import AKN

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _Client:
    model = "fake-embedding"

    def __init__(self) -> None:
        self.embedded = 0

    async def embed_documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
        self.embedded += len(items)
        return [[0.001] * 768 for _ in items]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _saved(session: AsyncSession) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    akn = AKN.replace("/akn/xa/act/2003/13", f"/akn/xa/act/2003/{suffix}")
    return await save_document(
        session,
        parse_akn(akn),
        jurisdiction_code="xa",
        law_title=f"Recitals {suffix}",
        akn_xml=akn,
    )


async def _eids(session: AsyncSession, vid: uuid.UUID) -> list[str]:
    rows = await session.execute(
        text("SELECT akn_eid FROM provisions WHERE version_id = :v ORDER BY akn_eid"),
        {"v": vid},
    )
    return [r[0] for r in rows]


async def test_a_saved_version_carries_recitals_that_resolve_but_do_not_render_as_body(
    session: AsyncSession,
) -> None:
    vid = await _saved(session)
    assert "recitals__rec_1" in await _eids(session, vid)
    assert await find_provision_id_by_eid(session, vid, "recitals__rec_2") is not None
    document = await get_version_document(session, vid)
    assert document is not None
    assert [p.akn_eid for p in document.provisions] == []
    assert [s.akn_eid for s in document.sections] == ["art_1"]


async def test_backfill_adds_only_the_missing_recitals_and_embeds_only_them(
    session: AsyncSession,
) -> None:
    vid = await _saved(session)
    client = _Client()
    await embed_and_stamp(session, vid, client=client, path_context=False)
    assert client.embedded == 3

    await session.execute(
        text("DELETE FROM provisions WHERE version_id = :v AND akn_type = 'recital'"),
        {"v": vid},
    )
    await session.execute(
        text("UPDATE provisions SET created_at = '2020-01-01' WHERE version_id = :v"),
        {"v": vid},
    )
    assert await add_missing_recital_rows(session, vid) == 2
    assert await add_missing_recital_rows(session, vid) == 0
    generations = await session.scalars(
        select(Provision.created_at).where(Provision.version_id == vid).distinct()
    )
    assert [g.year for g in generations] == [2020]

    again = _Client()
    await embed_and_stamp(session, vid, client=again, path_context=False, missing_only=True)
    assert again.embedded == 2
    held = await session.scalar(
        select(func.count())
        .select_from(ProvisionEmbedding)
        .where(ProvisionEmbedding.version_id == vid)
    )
    assert held == 3

    await session.execute(
        text("DELETE FROM provisions WHERE version_id = :v AND akn_eid = 'art_1__content'"),
        {"v": vid},
    )
    assert await add_missing_recital_rows(session, vid) == 0
    assert await _eids(session, vid) == ["recitals__rec_1", "recitals__rec_2"]


async def test_a_version_fully_embedded_but_unstamped_is_stamped_by_a_missing_only_pass(
    session: AsyncSession,
) -> None:
    vid = await _saved(session)
    await embed_and_stamp(session, vid, client=_Client(), path_context=False)
    await session.execute(text("UPDATE versions SET embedded_at = NULL WHERE id = :v"), {"v": vid})
    again = _Client()
    await embed_and_stamp(session, vid, client=again, path_context=False, missing_only=True)
    assert again.embedded == 0
    stamped = await session.scalar(
        text("SELECT embedded_at FROM versions WHERE id = :v"), {"v": vid}
    )
    assert stamped is not None


async def test_backfilled_recital_words_reach_the_search_lexicon(session: AsyncSession) -> None:
    vid = await _saved(session)
    await session.execute(
        text("DELETE FROM provisions WHERE version_id = :v AND akn_type = 'recital'"), {"v": vid}
    )
    await session.execute(text("DELETE FROM search_terms WHERE term = 'legibl'"))
    await add_missing_recital_rows(session, vid)
    found = await session.scalar(text("SELECT count(*) FROM search_terms WHERE term = 'legibl'"))
    assert found == 1
