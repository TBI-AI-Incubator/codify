"""`versions.is_current` names what `latest_versions_global` chose, and search over a
`CurrentScope` returns what search over the equivalent id list returns."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.akn.io import parse_akn
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.storage.embeddings import embed_and_stamp
from codify.storage.repository import save_document
from codify.storage.retrieval import hybrid_search
from codify.storage.versions import (
    CurrentScope,
    count_embedded_versions,
    count_in_scope,
    current_scope,
    latest_versions_global,
    scope_filter,
)
from codify.testing import postgres_url

pytestmark = pytest.mark.integration


class _FixedVectors:
    model = "test-model"

    async def embed_documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
        return [[1.0] + [0.0] * 767 for _ in items]


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as s:
        yield s
        await s.rollback()


async def _save(
    session: AsyncSession,
    code: str,
    number: str,
    on: str,
    *,
    body: str = "Body text.",
    language: str = "eng",
    parent: uuid.UUID | None = None,
    year: int | None = 2020,
) -> uuid.UUID:
    xml = parse_to_akn(
        f"BODY\n  ARTICLE 1\n    {body}\n",
        country=code,
        doctype="act",
        number=number,
        date="2020-01-01",
        language=language,
    )
    doc = parse_akn(xml)
    doc = doc.model_copy(
        update={
            "expression_date": date.fromisoformat(on),
            "frbr_expression_uri": f"{doc.frbr_work_uri}/{language}@{on}",
        }
    )
    return await save_document(
        session,
        doc,
        jurisdiction_code=code,
        law_title=number,
        year=year,
        akn_xml=xml,
        parent_version_id=parent,
    )


async def _current(session: AsyncSession, law_of: uuid.UUID) -> list[uuid.UUID]:
    rows = await session.execute(
        text(
            "SELECT v.id FROM versions v WHERE v.is_current AND v.law_id = "
            "(SELECT law_id FROM versions WHERE id = :vid)"
        ),
        {"vid": law_of},
    )
    return [r[0] for r in rows]


async def test_the_latest_original_is_current_and_a_delete_hands_it_back(
    session: AsyncSession,
) -> None:
    n = uuid.uuid4().hex[:8]
    first = await _save(session, "xa", n, "2020-01-01")
    assert await _current(session, first) == [first]

    newer = await _save(session, "xa", n, "2022-01-01")
    older = await _save(session, "xa", n, "2021-01-01")
    # A translation is never current, however recent.
    await _save(session, "xa", n, "2023-01-01", language="fra", parent=older)
    assert await _current(session, first) == [newer]
    flagged = set(await latest_versions_global(session, jurisdictions=("xa",)))
    assert newer in flagged and older not in flagged and first not in flagged

    # As a migration does: guard off, inside this test's rolled-back transaction.
    await session.execute(text("ALTER TABLE versions DISABLE TRIGGER versions_enforce_immutable"))
    await session.execute(text("DELETE FROM versions WHERE id = :vid"), {"vid": newer})
    assert await _current(session, first) == [older]
    await session.execute(
        text("UPDATE versions SET expression_date = '2030-01-01' WHERE id = :vid"),
        {"vid": first},
    )
    assert await _current(session, first) == [first]
    await session.execute(
        text("UPDATE versions SET parent_version_id = :p WHERE id = :vid"),
        {"vid": first, "p": older},
    )
    assert await _current(session, older) == [older]


async def test_concurrent_writers_leave_one_current_version(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    n = uuid.uuid4().hex[:8]
    async with factory() as s:
        base = await _save(s, "xa", n, "2020-01-01")
        await s.commit()
    try:
        a, b = factory(), factory()
        async with a, b:
            await _save(a, "xa", n, "2021-01-01")
            # Blocks on the law's lock until `a` commits, then sees its row.
            second = asyncio.create_task(_save(b, "xa", n, "2022-01-01"))
            await asyncio.sleep(0.5)
            assert not second.done()
            await a.commit()
            winner = await second
            await b.commit()
        async with factory() as s:
            assert await _current(s, base) == [winner]
    finally:
        async with factory() as s:
            await s.execute(
                text("DELETE FROM laws WHERE id = (SELECT law_id FROM versions WHERE id = :v)"),
                {"v": base},
            )
            await s.commit()


async def test_current_scope_searches_and_counts_what_the_id_list_does(
    session: AsyncSession,
) -> None:
    code = "xy"
    tag = uuid.uuid4().hex[:6]
    old = await _save(session, code, f"{tag}a", "2020-01-01", body=f"tenancy deposit {tag}")
    new = await _save(session, code, f"{tag}a", "2021-01-01", body=f"tenancy deposit {tag}")
    dated = await _save(session, code, f"{tag}b", "2020-01-01", body=f"deposit rules {tag}")
    undated = await _save(session, code, f"{tag}c", "2020-01-01", body=f"deposit {tag}", year=None)
    for vid in (new, dated):
        await embed_and_stamp(session, vid, client=_FixedVectors(), path_context=False)  # type: ignore[arg-type]
    jid = (
        await session.execute(text("SELECT id FROM jurisdictions WHERE code = :c"), {"c": code})
    ).scalar_one()

    async def search(scope: object) -> set[uuid.UUID]:
        rows = await hybrid_search(
            session,
            query_vec=[1.0] + [0.0] * 767,
            query_text=f"deposit {tag}",
            version_ids=scope,  # type: ignore[arg-type]
            k=10,
            candidate_pool=30,
            rrf_k=60,
            model_id="test-model",
        )
        # A set: every test vector is equal, so plans may order the ties apart.
        return {r[0] for r in rows}

    ids = await latest_versions_global(session, jurisdictions=(code,))
    scope = await current_scope(session, jurisdictions=(code,))
    assert scope == CurrentScope(jurisdiction_ids=(jid,))
    assert old not in ids and {new, dated, undated} <= set(ids)
    hits = await search(ids)
    assert hits and await search(scope) == hits
    assert await count_in_scope(session, scope) == len(ids)
    assert await count_embedded_versions(session, scope, "m") == await count_embedded_versions(
        session, ids, "m"
    )
    # A year range admits the undated law unless asked not to.
    assert await count_in_scope(
        session, CurrentScope(jurisdiction_ids=(jid,), from_year=2020)
    ) == len(ids)
    assert (
        await count_in_scope(
            session, CurrentScope(jurisdiction_ids=(jid,), from_year=2020, undated=False)
        )
        == len(ids) - 1
    )
    nowhere = await current_scope(session, jurisdictions=("no-such-code",))
    assert not nowhere and await search(nowhere) == set()
    assert await count_in_scope(session, nowhere) == 0


def test_an_id_list_binds_as_one_array() -> None:
    ids = [uuid.uuid4()]
    assert scope_filter(ids, "p.version_id") == (
        "",
        "p.version_id = ANY(:version_ids)",
        {"version_ids": ids},
    )
