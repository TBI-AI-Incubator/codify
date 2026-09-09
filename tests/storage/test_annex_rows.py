"""Resolved table rows reach the database and read back as a table."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.search import TOKENISER_VERSION, tokenise_to_text
from codify.storage.annex_rows import (
    count_annex_rows,
    count_stale_annex_row_tokens,
    index_version_annex_rows,
    retokenise_version_annex_rows,
    rows_for_table,
    search_annex_rows,
)
from codify.storage.models import Jurisdiction, Law, Version

pytestmark = pytest.mark.integration

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_AKN = f"""<akomaNtoso xmlns="{AKN_NS}"><act>
<preface><longTitle><p>Trade measures</p></longTitle></preface>
<body><section eId="sec_1"><content>
  <table eId="t1"><caption>Schedule of duties</caption>
    <tr eId="t1__tr_1"><th><p>Code</p></th><th><p>Description</p></th></tr>
    <tr eId="t1__tr_2"><td><p>7408</p></td><td><p>Copper wire:</p></td></tr>
    <tr eId="t1__tr_3"><td><p>740811</p></td><td><p>– – Exceeding 6 mm</p></td></tr>
    <tr eId="t1__tr_4"><td><p>740821</p></td><td><p>– – Of brass</p></td></tr>
    <tr eId="t1__tr_5"><td><p>74082900</p></td><td><p>– – Other</p></td></tr>
  </table>
</content></section></body></act></akomaNtoso>"""


def _url() -> str:
    raw = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _a_version(session: AsyncSession) -> uuid.UUID:
    tag = uuid.uuid4().hex[:8]
    juris = Jurisdiction(code=f"zz-{tag}", name="Test", languages=["en"])
    session.add(juris)
    await session.flush()
    law = Law(
        jurisdiction_id=juris.id,
        title=f"Act {tag}",
        doctype="act",
        frbr_work_uri=f"/akn/zz/{tag}/2024/1",
    )
    session.add(law)
    await session.flush()
    version = Version(
        law_id=law.id,
        expression_uri=f"/akn/zz/{tag}/2024/1/eng@2024-01-01",
        language="eng",
        expression_date=date(2024, 1, 1),
        akn_xml=_AKN,
    )
    session.add(version)
    await session.flush()
    return version.id


async def test_every_body_row_is_stored_and_the_header_is_not(session: AsyncSession) -> None:
    version_id = await _a_version(session)
    assert await index_version_annex_rows(session, version_id) == (4, None)
    await session.flush()
    assert await count_annex_rows(session, version_id, "t1") == 4


async def test_a_row_keeps_the_lineage_that_makes_it_readable(session: AsyncSession) -> None:
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()
    rows = await rows_for_table(session, version_id, "t1")
    other = next(r for r in rows if r.akn_eid == "t1__tr_5")
    assert other.lineage == ["Copper wire"]
    assert "Copper wire > Other" in other.resolved_text
    assert other.mechanisms, "a resolved row records how its context was found"


async def test_rows_read_back_in_document_order(session: AsyncSession) -> None:
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()
    rows = await rows_for_table(session, version_id, "t1")
    assert [r.row_index for r in rows] == sorted(r.row_index for r in rows)


async def test_a_table_reads_back_a_page_at_a_time(session: AsyncSession) -> None:
    """The largest table in the corpus runs to six figures."""
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()
    page = await rows_for_table(session, version_id, "t1", offset=1, limit=2)
    assert [r.akn_eid for r in page] == ["t1__tr_3", "t1__tr_4"]


async def test_a_row_is_found_by_a_word_only_its_lineage_holds(session: AsyncSession) -> None:
    """The point of resolution: "Other" alone is unsearchable.

    Ranked rather than filtered, and the terms are ORed, matching the provisions
    arm: every row under Copper matches that term, and the one whose lineage also
    holds "Other" is the one BM25 puts first.
    """
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()
    hits = await search_annex_rows(
        session, tokenise_to_text("copper other", "eng"), version_ids=[version_id]
    )
    assert hits[0].akn_eid == "t1__tr_5"


async def test_a_match_carries_the_law_it_belongs_to(session: AsyncSession) -> None:
    """A row on its own says "Article 12". The card needs to say which act."""
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()
    hit = (
        await search_annex_rows(
            session, tokenise_to_text("copper other", "eng"), version_ids=[version_id]
        )
    )[0]
    assert hit.frbr_work_uri
    assert hit.jurisdiction_code
    assert hit.table_eid == "t1"
    assert hit.row_index >= 0
    # The cells keep their column labels, which is why the row reads on its own.
    assert hit.cells


async def test_an_unscoped_search_reaches_every_version(session: AsyncSession) -> None:
    """`version_ids=None` is the corpus, an empty list is a caller with nothing
    in scope. Conflating them makes an unscoped search silently empty."""
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()
    tokens = tokenise_to_text("copper other", "eng")
    assert await search_annex_rows(session, tokens, version_ids=None)
    assert await search_annex_rows(session, tokens, version_ids=[]) == []


async def test_reindexing_replaces_rather_than_duplicates(session: AsyncSession) -> None:
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()
    await index_version_annex_rows(session, version_id)
    await session.flush()
    assert await count_annex_rows(session, version_id) == 4


async def test_reindexing_one_version_leaves_another_alone(session: AsyncSession) -> None:
    """The delete is scoped to a version. Unscoped, a re-derive of one law
    would silently empty its neighbours."""
    first = await _a_version(session)
    second = await _a_version(session)
    await index_version_annex_rows(session, first)
    await index_version_annex_rows(session, second)
    await session.flush()
    await index_version_annex_rows(session, first)
    await session.flush()
    assert await count_annex_rows(session, second) == 4


async def test_a_table_removed_from_the_akn_leaves_no_rows_behind(
    session: AsyncSession,
) -> None:
    """A repair edit or a re-OCR can drop a table. Its rows must go with it."""
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()
    emptied = _AKN.replace('<table eId="t1">', '<table eId="t2">')
    await index_version_annex_rows(session, version_id, emptied)
    await session.flush()
    assert await count_annex_rows(session, version_id, "t1") == 0
    assert await count_annex_rows(session, version_id, "t2") == 4


async def test_a_resolve_failure_keeps_the_rows_it_could_not_replace(
    session: AsyncSession,
) -> None:
    """The caller commits regardless, so deleting before resolving would empty
    a version whose rows worked yesterday."""
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()
    count, skipped = await index_version_annex_rows(session, version_id, "<not xml")
    assert count == 0
    # Named, so a run can say it refused rather than reporting no tables.
    assert skipped and "Error" in skipped
    await session.flush()
    assert await count_annex_rows(session, version_id) == 4


async def test_a_stale_row_is_retokenised_and_a_current_one_is_left_alone(
    session: AsyncSession,
) -> None:
    """The stamp exists so a tokeniser bump can find the rows it staled."""
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()

    # Nothing to do: every row was written by this tokeniser.
    assert await retokenise_version_annex_rows(session, version_id) == 0

    rows = await rows_for_table(session, version_id, "t1")
    rows[0].search_pipeline_version = TOKENISER_VERSION - 1
    rows[0].search_tokens = "stale"
    session.add(rows[0])
    await session.flush()

    assert await retokenise_version_annex_rows(session, version_id) == 1
    await session.flush()
    # The pass updates the Core table, so the object already in the identity map
    # still holds the old value; asserting on it would pass on a no-op.
    session.expire_all()
    refreshed = await rows_for_table(session, version_id, "t1")
    assert refreshed[0].search_tokens != "stale"
    assert refreshed[0].search_pipeline_version == TOKENISER_VERSION


async def test_the_writer_stamps_the_tokeniser_that_wrote_the_row(
    session: AsyncSession,
) -> None:
    """Unstamped rows are the state the stamp exists to make selectable."""
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()

    rows = await rows_for_table(session, version_id, "t1")
    assert rows
    assert all(r.search_pipeline_version == TOKENISER_VERSION for r in rows)


async def test_the_stale_count_sees_rows_the_current_tokeniser_has_not_written(
    session: AsyncSession,
) -> None:
    """The readiness probe reads this. Counting only provisions would report a
    corpus current while every table row waited for a backfill."""
    version_id = await _a_version(session)
    await index_version_annex_rows(session, version_id)
    await session.flush()

    stale, total = await count_stale_annex_row_tokens(session, version_id)
    assert total == 4
    assert stale == 0

    rows = await rows_for_table(session, version_id, "t1")
    rows[0].search_pipeline_version = None
    session.add(rows[0])
    await session.flush()

    assert await count_stale_annex_row_tokens(session, version_id) == (1, 4)
