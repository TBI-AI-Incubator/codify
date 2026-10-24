"""The lexical arm reads a small scope before ranking it, and walks the BM25 index
for a large one. Which it takes must not change what a search returns."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import codify.storage.retrieval as retrieval
from codify.akn import Article, Document, Paragraph
from codify.storage.repository import save_document
from codify.storage.retrieval import _reads_scope_first, hybrid_search
from codify.storage.versions import CurrentScope, current_scope
from codify.testing import postgres_url

pytestmark = pytest.mark.integration

_SCOPE_FIRST = "in_scope AS MATERIALIZED"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.fixture
def statements(session: AsyncSession) -> Iterator[list[str]]:
    """Every statement the session sends, so a test can say which arm ran."""
    seen: list[str] = []
    sync_engine = session.bind.sync_engine  # type: ignore[union-attr]

    def _record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        seen.append(statement)

    event.listen(sync_engine, "before_cursor_execute", _record)
    yield seen
    event.remove(sync_engine, "before_cursor_execute", _record)


async def _law(session: AsyncSession, code: str, texts: list[str]) -> uuid.UUID:
    """One law in `code` with a paragraph per text; returns its version id."""
    suffix = uuid.uuid4().hex[:8]
    doc = Document(
        frbr_work_uri=f"/akn/{code}/act/2024/{suffix}",
        frbr_expression_uri=f"/akn/{code}/act/2024/{suffix}/eng@2024-01-01",
        language="eng",
        expression_date=date(2024, 1, 1),
        body=[
            Article(
                akn_eid=f"art_{i}",
                akn_type="article",
                position=i,
                heading=f"Article {i}",
                children=[
                    Paragraph(
                        akn_eid=f"art_{i}__para_1", akn_type="paragraph", position=1, text=body
                    )
                ],
            )
            for i, body in enumerate(texts, start=1)
        ],
    )
    return await save_document(
        session, doc, jurisdiction_code=code, law_title=f"Act {suffix}", doctype="act", year=2024
    )


async def _rows(session: AsyncSession, version_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM provisions WHERE version_id = :v"), {"v": version_id}
            )
        ).scalar_one()
    )


async def test_a_scope_at_the_limit_is_read_first_and_one_past_it_is_walked(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = f"q{uuid.uuid4().hex[:5]}"
    version = await _law(session, code, ["one", "two", "three"])
    rows = await _rows(session, version)
    scope = await current_scope(session, jurisdictions=(code,))

    monkeypatch.setattr(retrieval, "SCOPE_FIRST_MAX_ROWS", rows)
    at_limit = (
        await _reads_scope_first(session, scope),
        await _reads_scope_first(session, [version]),
    )
    monkeypatch.setattr(retrieval, "SCOPE_FIRST_MAX_ROWS", rows - 1)
    past_it = (
        await _reads_scope_first(session, scope),
        await _reads_scope_first(session, [version]),
    )

    assert (rows, at_limit, past_it) == (rows, (True, True), (False, False))


async def test_a_scope_of_too_many_laws_is_walked_without_counting_its_rows(
    session: AsyncSession, statements: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code = f"q{uuid.uuid4().hex[:5]}"
    versions = [await _law(session, code, ["one"]), await _law(session, code, ["two"])]
    scope = await current_scope(session, jurisdictions=(code,))
    monkeypatch.setattr(retrieval, "SCOPE_FIRST_MAX_ROWS", 10**9)

    monkeypatch.setattr(retrieval, "SCOPE_FIRST_MAX_VERSIONS", 2)
    at_limit = (
        await _reads_scope_first(session, scope),
        await _reads_scope_first(session, versions),
    )
    statements.clear()
    monkeypatch.setattr(retrieval, "SCOPE_FIRST_MAX_VERSIONS", 1)
    past_it = (
        await _reads_scope_first(session, scope),
        await _reads_scope_first(session, versions),
    )
    row_counts = [s for s in statements if "LATERAL" in s]

    assert (at_limit, past_it, row_counts) == ((True, True), (False, False), [])


async def test_the_whole_corpus_is_always_walked(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is outside it, so the walk discards nothing; no limit makes reading it cheap."""
    monkeypatch.setattr(retrieval, "SCOPE_FIRST_MAX_ROWS", 10**12)
    monkeypatch.setattr(retrieval, "SCOPE_FIRST_MAX_VERSIONS", 10**12)

    assert await _reads_scope_first(session, CurrentScope()) is False


async def _search(session: AsyncSession, scope: Any, query: str) -> list[tuple[uuid.UUID, float]]:
    rows = await hybrid_search(
        session,
        query_vec=[1.0] + [0.0] * 767,
        query_text=query,
        version_ids=scope,
        k=10,
        candidate_pool=4,
        rrf_k=60,
        model_id="no-such-model",  # no vectors: the order is the lexical arm's alone
    )
    return [(r[0], r[3]) for r in rows]


@pytest.mark.parametrize("as_ids", [False, True], ids=["current-scope", "id-list"])
async def test_both_arms_return_the_same_ranking(
    session: AsyncSession,
    statements: list[str],
    monkeypatch: pytest.MonkeyPatch,
    as_ids: bool,
) -> None:
    """Scored the same way, so the same top `pool` in the same order. The other
    jurisdiction scores higher on every term, so the walk has to pass it over."""
    tag = uuid.uuid4().hex[:6]
    mine, theirs = f"q{tag}", f"r{tag}"
    # Term frequency decides each score, so no two provisions tie.
    version = await _law(
        session, mine, [f"tenancy {'deposit ' * n}held {tag}" for n in range(1, 7)]
    )
    await _law(session, theirs, [f"{'deposit ' * 9}{tag}" for _ in range(6)])
    scope: Any = [version] if as_ids else await current_scope(session, jurisdictions=(mine,))

    monkeypatch.setattr(retrieval, "SCOPE_FIRST_MAX_ROWS", 10**9)
    read_first = await _search(session, scope, f"deposit {tag}")
    read_first_sql = [s for s in statements if "WITH" in s and "fts AS" in s]
    statements.clear()
    monkeypatch.setattr(retrieval, "SCOPE_FIRST_MAX_ROWS", 0)
    walked = await _search(session, scope, f"deposit {tag}")
    walked_sql = [s for s in statements if "WITH" in s and "fts AS" in s]

    in_scope = set(
        (
            await session.execute(
                text("SELECT id FROM provisions WHERE version_id = :v"), {"v": version}
            )
        ).scalars()
    )
    assert [_SCOPE_FIRST in s for s in read_first_sql + walked_sql] == [True, False]
    # The pool's four of six, every one from this scope.
    assert (len(read_first), {pid for pid, _ in read_first} <= in_scope) == (4, True)
    assert read_first == walked
