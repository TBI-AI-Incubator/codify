"""The law listing picks its page before reading version dates, so the order,
paging and dates must match what a whole-corpus aggregate would give."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.storage.jurisdictions import get_or_create_jurisdiction
from codify.storage.laws import list_laws
from codify.storage.models import Law, Version
from codify.testing import postgres_url

pytestmark = pytest.mark.integration


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    try:
        async with engine.connect() as c:
            if (
                await c.execute(text("SELECT to_regclass('laws_title_bm25_idx')"))
            ).scalar() is None:
                pytest.skip("schema not migrated; run alembic upgrade head")
    except OSError:
        await engine.dispose()
        pytest.skip("postgres not reachable")
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[str, dict[uuid.UUID, tuple[date, datetime]]]:
    """Laws whose year, URI and newest version disagree on order; returns the
    jurisdiction and each law's (latest expression, latest ingest)."""
    code = "x" + uuid.uuid4().hex[:6]
    jurisdiction = await get_or_create_jurisdiction(session, code, languages=["en"])
    base = datetime(2026, 1, 1, tzinfo=UTC)
    expected: dict[uuid.UUID, tuple[date, datetime]] = {}
    # (year, uri suffix, version offsets in days); None is an undated law.
    shapes = [(2001, "c", [3, 40]), (None, "a", [9]), (2001, "a", [1]), (1999, "b", [25, 2])]
    shapes += [(2010, "d", []), (None, "b", [17, 5, 30]), (2001, "b", [11])]
    for i, (year, suffix, offsets) in enumerate(shapes):
        law = Law(
            jurisdiction_id=jurisdiction.id,
            title=f"Act {i}",
            doctype="act",
            year=year,
            frbr_work_uri=f"/akn/{code}/act/{suffix}/{i}",
        )
        session.add(law)
        await session.flush()
        for j, days in enumerate(offsets):
            session.add(
                Version(
                    law_id=law.id,
                    expression_uri=f"{law.frbr_work_uri}/eng@2020-01-{j + 1:02d}",
                    language="en",
                    expression_date=date(2020, 1, 1) + timedelta(days=days),
                    akn_xml="<akomaNtoso/>",
                    ingested_at=base + timedelta(days=days),
                )
            )
        await session.flush()
        if offsets:
            newest = max(offsets)
            expected[law.id] = (
                date(2020, 1, 1) + timedelta(days=newest),
                base + timedelta(days=newest),
            )
    return code, expected


async def test_browse_order_cursor_pages_and_dates(session: AsyncSession) -> None:
    code, expected = await _seed(session)
    whole, cursor = await list_laws(session, jurisdictions=[code], limit=50)
    assert cursor is None
    keyed = sorted(whole, key=lambda r: (r.year is None, -(r.year or 0), r.frbr_work_uri, r.id))
    assert [r.id for r in whole] == [r.id for r in keyed]
    for row in whole:
        got = (row.latest_expression_date, row.latest_ingested_at)
        assert got == expected.get(row.id, (None, None)), row.title

    paged: list[uuid.UUID] = []
    cursor = None
    while True:
        page, cursor = await list_laws(session, jurisdictions=[code], limit=2, cursor=cursor)
        paged += [r.id for r in page]
        if cursor is None:
            break
    assert paged == [r.id for r in whole]


async def test_ingested_order_and_offset_pages(session: AsyncSession) -> None:
    code, expected = await _seed(session)
    whole, _ = await list_laws(session, jurisdictions=[code], sort="ingested", limit=50)
    keyed = sorted(
        whole,
        key=lambda r: (
            r.latest_ingested_at is None,
            -(r.latest_ingested_at.timestamp() if r.latest_ingested_at else 0),
            r.frbr_work_uri,
            r.id,
        ),
    )
    assert [r.id for r in whole] == [r.id for r in keyed]
    assert {r.id for r in whole if r.latest_ingested_at} == set(expected)
    pages = [
        (await list_laws(session, jurisdictions=[code], sort="ingested", limit=3, offset=o))[0]
        for o in range(0, len(whole), 3)
    ]
    assert [r.id for p in pages for r in p] == [r.id for r in whole]


async def test_relevance_order_survives_a_join_that_reorders(session: AsyncSession) -> None:
    """The page's order must be re-applied outside it: a hash join need not keep it."""
    code = "x" + uuid.uuid4().hex[:6]
    word = "q" + "".join(chr(ord("a") + int(c, 16) % 26) for c in uuid.uuid4().hex[:10])
    jurisdiction = await get_or_create_jurisdiction(session, code, languages=["en"])
    # Longer titles score lower; URIs run against that order.
    for i in range(12):
        session.add(
            Law(
                jurisdiction_id=jurisdiction.id,
                title=" ".join([word.title(), *["Harbour"] * i, "Act"]),
                title_tokens=" ".join([word, *["harbour"] * i, "act"]),
                doctype="act",
                frbr_work_uri=f"/akn/{code}/act/2001/{99 - i}",
            )
        )
    await session.flush()
    expected = (
        (
            await session.execute(
                text(
                    "SELECT id FROM laws WHERE title_tsv @@ to_tsquery('simple', :w) "
                    "ORDER BY title_tokens <@> to_bm25query(:w, 'laws_title_bm25_idx'), "
                    "frbr_work_uri, id"
                ),
                {"w": word},
            )
        )
        .scalars()
        .all()
    )
    assert len(expected) == 12
    await session.execute(text("SET LOCAL enable_nestloop = off"))
    await session.execute(text("SET LOCAL enable_mergejoin = off"))
    rows, _ = await list_laws(session, q=word, limit=50)
    assert [r.id for r in rows] == list(expected)
    # The re-rank reads the score as the last column; equal ranks keep BM25's order.
    reranked, _ = await list_laws(session, q=word, limit=50, doctype_ranks={(code, "act"): 1})
    assert [r.id for r in reranked] == list(expected)
