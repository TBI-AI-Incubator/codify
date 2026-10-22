"""A law found by its number and year is found whatever plan Postgres picks.

The relevance order is BM25 through `laws_title_bm25_idx`. Scanned in that order,
the index yields only titles sharing a token with the query, so a law matched by
its citation alone was dropped whenever the planner chose the ordered scan.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.akn import Article, Document, Paragraph
from codify.storage.laws import count_laws, list_laws, list_laws_facets
from codify.storage.repository import save_document
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
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _seed(
    session: AsyncSession, doctype: str, title: str, number: str, year: int, suffix: str = ""
) -> None:
    suffix = suffix or uuid.uuid4().hex[:8]
    work = f"/akn/xz/act/{year}/{suffix}"
    doc = Document(
        frbr_work_uri=work,
        frbr_expression_uri=f"{work}/eng@{year}-01-01",
        language="eng",
        expression_date=date(year, 1, 1),
        body=[
            Article(
                akn_eid="art_1",
                akn_type="article",
                position=1,
                children=[
                    Paragraph(akn_eid="art_1__p1", akn_type="paragraph", position=1, text="Text.")
                ],
            )
        ],
    )
    await save_document(
        session, doc, jurisdiction_code="xz", law_title=title, doctype=doctype, number=number,
        year=year,
    )  # fmt: skip


def _citation() -> tuple[str, str]:
    # A number and year no other title carries, so the corpus-wide query sees only
    # the rows seeded here and no filter offers the planner another index.
    number = str(uuid.uuid4().int % 9_000_000 + 1_000_000)
    return number, f"{number}/1817"


async def _force_the_bm25_scan(session: AsyncSession) -> None:
    # With both off, the only way into `laws` for a relevance order is an index scan.
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    await session.execute(text("SET LOCAL enable_bitmapscan = off"))


@pytest.mark.parametrize("padding", ["", "0"])
async def test_a_citation_only_match_survives_the_ordered_bm25_scan(
    session: AsyncSession, padding: str
) -> None:
    number, query = _citation()
    kind = "act"
    # The cited law shares no title token with the query; a decoy shares one.
    await _seed(session, kind, "Commercial Companies Law", padding + number, 1817)
    await _seed(session, kind, f"Ordinance {number} on Harbours", "17", 1999)
    await session.flush()
    await _force_the_bm25_scan(session)

    rows, _ = await list_laws(session, q=query)
    titles = [r.title for r in rows]
    assert sorted(titles) == ["Commercial Companies Law", f"Ordinance {number} on Harbours"]
    # Title matches rank first; the citation-only law scores nothing on words.
    assert titles[0] == f"Ordinance {number} on Harbours"
    assert await count_laws(session, q=query) == 2
    facets = await list_laws_facets(session, q=query)
    assert sum(f["count"] for f in facets["doctype"]) == 2
    assert {f["value"] for f in facets["year"]} == {1817, 1999}


async def test_paging_a_relevance_listing_keeps_its_order(session: AsyncSession) -> None:
    number, _ = _citation()
    for title in (f"Harbour {number} Lights Law", f"Harbour {number} Law", f"{number} Fisheries"):
        await _seed(session, "act", title, "3", 2001)
    await session.flush()
    await _force_the_bm25_scan(session)
    whole, _ = await list_laws(session, q=f"harbour {number} lights")
    assert [r.title for r in whole][0] == f"Harbour {number} Lights Law"
    pages = [(await list_laws(session, q=f"harbour {number} lights", limit=1, offset=i))[0]
             for i in range(len(whole))]  # fmt: skip
    assert [p[0].title for p in pages] == [r.title for r in whole]


async def test_tied_scores_page_once_each_in_order(session: AsyncSession) -> None:
    number, _ = _citation()
    # Identical titles score alike; inserted against their work-URI order, so the
    # index's order among the ties is not the listing's.
    stems = [f"{c}{uuid.uuid4().hex[:6]}" for c in "fedcba"]
    for stem in stems:
        await _seed(session, "act", f"Harbour {number} Law", "3", 2001, suffix=stem)
    await session.flush()
    await _force_the_bm25_scan(session)
    query = f"harbour {number}"
    whole, _ = await list_laws(session, q=query)
    assert [r.frbr_work_uri for r in whole] == sorted(r.frbr_work_uri for r in whole)
    paged = [
        (await list_laws(session, q=query, limit=1, offset=i))[0][0].frbr_work_uri
        for i in range(len(stems))
    ]
    assert paged == [r.frbr_work_uri for r in whole]
    assert len(set(paged)) == len(stems)
