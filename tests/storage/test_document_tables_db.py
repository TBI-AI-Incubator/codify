"""Annex tables are projected on the CPU pool, and only when asked for."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.storage import documents
from codify.storage.documents import get_version_document
from codify.storage.models import Jurisdiction, Law, Version
from codify.testing import postgres_url
from tests.storage.test_document_tables import _XML

pytestmark = pytest.mark.integration


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _version(session: AsyncSession) -> uuid.UUID:
    tag = uuid.uuid4().hex[:8]
    juris = Jurisdiction(code=f"zz-{tag}", name="Test", languages=["en"])
    session.add(juris)
    await session.flush()
    law = Law(
        jurisdiction_id=juris.id, title="Widgets", doctype="act", frbr_work_uri=f"/akn/zz/{tag}"
    )
    session.add(law)
    await session.flush()
    version = Version(
        law_id=law.id,
        expression_uri=f"/akn/zz/{tag}/eng@2024-01-01",
        language="eng",
        expression_date=date(2024, 1, 1),
        akn_xml=_XML,
    )
    session.add(version)
    await session.flush()
    return version.id


async def test_tables_are_projected_on_the_pool_only_when_asked_for(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    pooled: list[str] = []
    real = documents.on_the_cpu_pool

    async def spy(fn: Callable[..., Any], *args: Any) -> Any:
        pooled.append(fn.__name__)
        return await real(fn, *args)

    monkeypatch.setattr(documents, "on_the_cpu_pool", spy)
    version_id = await _version(session)
    plain = await get_version_document(session, version_id)
    assert plain is not None and plain.tables == [] and pooled == []
    full = await get_version_document(session, version_id, include_tables=True)
    assert full is not None
    assert [t.table.akn_eid for t in full.tables] == ["art_1__tbl", "att_1__table_1__tbl"]
    assert pooled == ["_unrowed_tables"]
