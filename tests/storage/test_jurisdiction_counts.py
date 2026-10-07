"""A jurisdiction's law and version counts are its own, and provisions are not counted."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.akn.io import parse_akn
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.storage.jurisdictions import JurisdictionCounts, jurisdiction_counts
from codify.storage.repository import save_document
from codify.testing import postgres_url

pytestmark = pytest.mark.integration


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _save(session: AsyncSession, code: str, number: str, language: str) -> uuid.UUID:
    xml = parse_to_akn(
        "BODY\n  ARTICLE 1\n    Body text.\n  ARTICLE 2\n    More text.\n",
        country=code,
        doctype="act",
        number=number,
        date="2020-01-01",
        language=language,
    )
    return await save_document(
        session, parse_akn(xml), jurisdiction_code=code, law_title=number, akn_xml=xml
    )


async def test_counts_laws_and_versions_of_one_jurisdiction(session: AsyncSession) -> None:
    # Shared synthetic codes may hold committed rows, so the seed is measured as a delta.
    code, other = "xa", "xy"
    before = await jurisdiction_counts(session, code)
    # Three laws, five versions; the other jurisdiction's rows must not count.
    for languages in (("eng", "fra", "ara"), ("eng",), ("eng",)):
        number = uuid.uuid4().hex[:8]
        for language in languages:
            await _save(session, code, number, language)
    await _save(session, other, uuid.uuid4().hex[:8], "eng")

    after = await jurisdiction_counts(session, code)
    assert (after.laws - before.laws, after.versions - before.versions) == (3, 5)
    assert after.provisions is None


async def test_an_absent_jurisdiction_counts_zero(session: AsyncSession) -> None:
    absent = await jurisdiction_counts(session, f"z{uuid.uuid4().hex[:6]}")
    assert absent == JurisdictionCounts(laws=0, versions=0)
