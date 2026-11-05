"""Goods codes reach the database and answer a lookup."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.storage.goods_codes import (
    find_versions_by_goods_code,
    goods_codes_for_version,
    index_version_goods_codes,
)
from codify.storage.models import Jurisdiction, Law, Version

pytestmark = pytest.mark.integration

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_AKN = f"""<akomaNtoso xmlns="{AKN_NS}"><act><body>
  <p eId="rec_1">Measures on goods falling within CN code 8471 30 00 apply.</p>
  <table eId="t1">
    <tr eId="t1__tr_1"><th><p>CN code</p></th><th><p>Description</p></th></tr>
    <tr eId="t1__tr_2"><td eId="t1__tr_2__tc_1"><p>ex 8471 30 90</p></td>
      <td><p>Other portable machines</p></td></tr>
    <tr eId="t1__tr_3"><td eId="t1__tr_3__tc_1"><p>2710 19 43</p></td>
      <td><p>Gas oils</p></td></tr>
  </table>
</body></act></akomaNtoso>"""


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


async def test_codes_from_a_column_and_from_prose_are_both_stored(session: AsyncSession) -> None:
    version_id = await _a_version(session)
    assert await index_version_goods_codes(session, version_id, _AKN) == 3
    await session.flush()
    rows = await goods_codes_for_version(session, version_id)
    assert {r.code for r in rows} == {"84713000", "84713090", "27101943"}
    assert {r.source for r in rows} == {"column", "prose"}


async def test_the_ex_prefix_survives_the_round_trip(session: AsyncSession) -> None:
    version_id = await _a_version(session)
    await index_version_goods_codes(session, version_id, _AKN)
    await session.flush()
    partial = [r for r in await goods_codes_for_version(session, version_id) if r.partial]
    assert [r.surface for r in partial] == ["ex 8471 30 90"]


async def test_a_heading_lookup_reaches_the_subheadings_under_it(session: AsyncSession) -> None:
    """An HS6 question has to find the CN8 codes that carry the measure."""
    version_id = await _a_version(session)
    await index_version_goods_codes(session, version_id, _AKN)
    await session.flush()
    found = await find_versions_by_goods_code(session, "847130")
    assert {row[3] for row in found} == {"84713000", "84713090"}
    # An address travels with every hit, so it can be cited: the row for a
    # listing, the paragraph for a mention in prose.
    assert all(row[2] for row in found)
    exact = await find_versions_by_goods_code(session, "847130", include_narrower=False)
    assert exact == []


async def test_reindexing_replaces_rather_than_duplicates(session: AsyncSession) -> None:
    version_id = await _a_version(session)
    await index_version_goods_codes(session, version_id, _AKN)
    await session.flush()
    await index_version_goods_codes(session, version_id, _AKN)
    await session.flush()
    assert len(await goods_codes_for_version(session, version_id)) == 3


_CHAPTER_AKN = f"""<akomaNtoso xmlns="{AKN_NS}"><act><body>
  <table eId="t1">
    <tr eId="t1__tr_1"><th><p>CN code</p></th></tr>
    <tr eId="t1__tr_2"><td><p>99 – Widgets</p><p>Except:</p><p>9901 – Spare widgets</p></td></tr>
  </table>
</body></act></akomaNtoso>"""


async def test_a_measure_naming_a_chapter_answers_for_a_code_under_it(
    session: AsyncSession,
) -> None:
    """A measure listing chapter 99 covers 99021000; one carving out 9901 says so."""
    version_id = await _a_version(session)
    await index_version_goods_codes(session, version_id, _CHAPTER_AKN)
    await session.flush()
    covered = await find_versions_by_goods_code(session, "99021000", current_only=False)
    assert [(m.code, m.excluded) for m in covered] == [("99", False)]
    carved = await find_versions_by_goods_code(session, "99011000", current_only=False)
    assert sorted((m.code, m.excluded) for m in carved) == [("99", False), ("9901", True)]
    assert {m.version_id for m in carved} == {version_id}
    assert all(m.law_title and m.frbr_work_uri and m.jurisdiction_code for m in carved)
    narrow_only = await find_versions_by_goods_code(
        session, "99021000", include_broader=False, current_only=False
    )
    assert narrow_only == []


async def test_a_lookup_keeps_to_the_jurisdictions_it_is_given(session: AsyncSession) -> None:
    version_id = await _a_version(session)
    await index_version_goods_codes(session, version_id, _CHAPTER_AKN)
    await session.flush()
    elsewhere = await find_versions_by_goods_code(
        session, "99021000", jurisdiction_codes=["none-such"], current_only=False
    )
    assert elsewhere == []
