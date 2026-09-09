"""A law gets the short name its own AKN states, without a re-ingest."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from codify.akn.io import parse_akn
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.search import TOKENISER_VERSION
from codify.storage import repository
from codify.storage.models import Law
from codify.storage.repository import (
    fill_short_title_for_version,
    save_document,
    save_document_reporting,
)
from codify.storage.title_tokens import title_tokens_for

pytestmark = pytest.mark.integration


def _postgres_url() -> str:
    raw = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _reachable(url: str) -> bool:
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
        async with engine.connect():
            pass
        await engine.dispose()
        return True
    except Exception:
        return False


# Tests commit across sessions; teardown deletes the tracked laws.
_SEEDED: list[uuid.UUID] = []


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    url = _postgres_url()
    if not await _reachable(url):
        pytest.skip(f"postgres not reachable at {url}")
    engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
    async with engine.connect() as c:
        if (await c.execute(text("SELECT to_regclass('laws')"))).scalar() is None:
            await engine.dispose()
            pytest.skip("schema not migrated; run alembic upgrade head")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    _SEEDED.clear()
    try:
        async with factory() as s:
            yield s
    finally:
        if _SEEDED:
            async with factory() as cleanup:
                # Versions and their derived rows cascade from the law.
                await cleanup.execute(
                    text("DELETE FROM laws WHERE id = ANY(:ids)"), {"ids": list(_SEEDED)}
                )
                await cleanup.commit()
        _SEEDED.clear()
        await engine.dispose()


async def _seed(session: AsyncSession, *, doc_title: str | None) -> tuple[uuid.UUID, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    bb = "BODY\n  SECTION 1\n    ARTICLE 1\n      Body of article one.\n"
    akn = parse_to_akn(bb, country="xa", doctype="act", number=suffix, date="2020-01-01")
    if doc_title:
        akn = akn.replace("<body", f"<docTitle>{doc_title}</docTitle><body", 1)
    vid = await save_document(
        session,
        parse_akn(akn),
        jurisdiction_code="xa",
        law_title=f"An Act to do things {suffix}",
        akn_xml=akn,
    )
    await session.commit()
    law_id = (
        await session.execute(text("SELECT law_id FROM versions WHERE id = :v"), {"v": vid})
    ).scalar_one()
    _SEEDED.append(law_id)
    return vid, law_id


def _akn_with_title(doc_title: str) -> str:
    bb = "BODY\n  SECTION 1\n    ARTICLE 1\n      Body of article one.\n"
    akn = parse_to_akn(
        bb, country="xa", doctype="act", number=uuid.uuid4().hex[:8], date="2020-01-01"
    )
    return akn.replace("<body", f"<preface><docTitle>{doc_title}</docTitle></preface><body", 1)


async def _law(session: AsyncSession, law_id: uuid.UUID) -> Law:
    return (await session.execute(select(Law).where(Law.id == law_id))).scalar_one()


async def test_a_stated_name_is_filled_and_reindexed(session: AsyncSession) -> None:
    vid, law_id = await _seed(session, doc_title="Finance Act 1958")
    await session.execute(
        text("UPDATE laws SET short_title = NULL, title_search_pipeline_version = 1 WHERE id = :i"),
        {"i": law_id},
    )
    await session.commit()

    filled = await fill_short_title_for_version(session, vid)
    await session.commit()
    session.expire_all()

    law = await _law(session, law_id)
    assert filled == "Finance Act 1958"
    assert law.short_title == "Finance Act 1958"
    assert law.title_search_pipeline_version is None


async def test_a_source_stating_nothing_is_left_alone(session: AsyncSession) -> None:
    vid, law_id = await _seed(session, doc_title=None)
    await session.execute(text("UPDATE laws SET short_title = NULL WHERE id = :i"), {"i": law_id})
    await session.commit()

    assert await fill_short_title_for_version(session, vid) is None
    await session.commit()
    session.expire_all()
    assert (await _law(session, law_id)).short_title is None


async def test_only_the_source_expression_names_the_law(session: AsyncSession) -> None:
    vid, law_id = await _seed(session, doc_title="Source Act 1958")
    other = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO versions (
                id, law_id, language, akn_xml, parent_version_id, expression_uri,
                expression_date, ingested_at
            )
            SELECT :new, law_id, 'fra', :akn, :vid, expression_uri || '/fra',
                   expression_date, now()
            FROM versions WHERE id = :vid
            """
        ),
        {
            "new": other,
            "vid": vid,
            "akn": "<akn><preface><docTitle>Translated Act 1958</docTitle></preface></akn>",
        },
    )
    await session.execute(text("UPDATE laws SET short_title = NULL WHERE id = :i"), {"i": law_id})
    await session.commit()

    assert await fill_short_title_for_version(session, other) is None
    assert await fill_short_title_for_version(session, vid) == "Source Act 1958"
    await session.commit()
    session.expire_all()
    assert (await _law(session, law_id)).short_title == "Source Act 1958"


async def test_an_existing_name_is_never_overwritten(session: AsyncSession) -> None:
    vid, law_id = await _seed(session, doc_title="Finance Act 1958")
    await session.execute(
        text("UPDATE laws SET short_title = 'Kept Name' WHERE id = :i"), {"i": law_id}
    )
    await session.commit()

    assert await fill_short_title_for_version(session, vid) is None
    await session.commit()
    session.expire_all()
    assert (await _law(session, law_id)).short_title == "Kept Name"


async def test_a_translation_does_not_name_an_existing_law(session: AsyncSession) -> None:
    vid, law_id = await _seed(session, doc_title=None)
    await session.execute(text("UPDATE laws SET short_title = NULL WHERE id = :i"), {"i": law_id})
    await session.commit()

    frbr = (
        await session.execute(text("SELECT frbr_work_uri FROM laws WHERE id = :i"), {"i": law_id})
    ).scalar_one()
    bb = "BODY\n  SECTION 1\n    ARTICLE 1\n      Corps de l'article un.\n"
    akn = parse_to_akn(
        bb, country="xa", doctype="act", number=frbr.rsplit("/", 1)[-1], date="2020-01-01"
    )
    akn = akn.replace(
        "<body", "<preface><docTitle>Loi de finances 1958</docTitle></preface><body", 1
    )
    doc = parse_akn(akn)
    doc.frbr_work_uri = frbr
    doc.language = "fra"
    doc.frbr_expression_uri = f"{frbr}/fra@2020-01-01"
    await save_document(
        session,
        doc,
        jurisdiction_code="xa",
        law_title="Loi",
        akn_xml=akn,
        parent_version_id=vid,
    )
    await session.commit()
    session.expire_all()

    assert (await _law(session, law_id)).short_title is None


async def test_a_call_that_stores_nothing_does_not_name_the_law(session: AsyncSession) -> None:
    vid, law_id = await _seed(session, doc_title="Finance Act 1958")
    await session.execute(text("UPDATE laws SET short_title = NULL WHERE id = :i"), {"i": law_id})
    await session.commit()

    frbr, akn = (
        await session.execute(
            text(
                "SELECT l.frbr_work_uri, v.akn_xml FROM laws l"
                " JOIN versions v ON v.id = :v WHERE l.id = :i"
            ),
            {"v": vid, "i": law_id},
        )
    ).one()
    doc = parse_akn(akn)
    doc.frbr_work_uri = frbr
    outcome = await save_document_reporting(
        session, doc, jurisdiction_code="xa", law_title="An Act", akn_xml=akn
    )
    await session.commit()
    session.expire_all()

    assert outcome.stored is False, "the fixture must reuse, or it tests nothing"
    assert (await _law(session, law_id)).short_title is None


async def test_the_newest_source_expression_names_the_law(session: AsyncSession) -> None:
    vid, law_id = await _seed(session, doc_title="Current Act 2024")
    await session.execute(
        text(
            """
            INSERT INTO versions (
                id, law_id, language, akn_xml, parent_version_id, expression_uri,
                expression_date, ingested_at
            )
            SELECT :new, law_id, 'eng', :akn, NULL, expression_uri || '/old',
                   expression_date - INTERVAL '4 years', now()
            FROM versions WHERE id = :src
            """
        ),
        {"new": uuid.uuid4(), "src": vid, "akn": _akn_with_title("Superseded Act 1920")},
    )
    await session.execute(text("UPDATE laws SET short_title = NULL WHERE id = :i"), {"i": law_id})
    await session.commit()

    assert await fill_short_title_for_version(session, vid) == "Current Act 2024"
    await session.commit()
    session.expire_all()
    assert (await _law(session, law_id)).short_title == "Current Act 2024"


async def test_an_older_expression_ingested_later_does_not_name_the_law(
    session: AsyncSession,
) -> None:
    vid, law_id = await _seed(session, doc_title=None)
    await session.execute(text("UPDATE laws SET short_title = NULL WHERE id = :i"), {"i": law_id})
    await session.commit()

    frbr = (
        await session.execute(text("SELECT frbr_work_uri FROM laws WHERE id = :i"), {"i": law_id})
    ).scalar_one()
    akn = _akn_with_title("Superseded Act 1920")
    doc = parse_akn(akn)
    doc.frbr_work_uri = frbr
    doc.frbr_expression_uri = f"{frbr}/eng@2020-01-01"
    doc.expression_date = date(2020, 1, 1)
    await save_document(session, doc, jurisdiction_code="xa", law_title="An Act", akn_xml=akn)
    await session.commit()
    session.expire_all()

    assert (await _law(session, law_id)).short_title is None


async def test_a_new_source_expression_does_name_an_unnamed_law(
    session: AsyncSession,
) -> None:
    vid, law_id = await _seed(session, doc_title=None)
    await session.execute(text("UPDATE laws SET short_title = NULL WHERE id = :i"), {"i": law_id})
    await session.commit()

    frbr = (
        await session.execute(text("SELECT frbr_work_uri FROM laws WHERE id = :i"), {"i": law_id})
    ).scalar_one()
    akn = _akn_with_title("Finance Act 2030")
    doc = parse_akn(akn)
    doc.frbr_work_uri = frbr
    doc.frbr_expression_uri = f"{frbr}/eng@2030-01-01"
    doc.expression_date = date(2030, 1, 1)
    await save_document(session, doc, jurisdiction_code="xa", law_title="An Act", akn_xml=akn)
    async with AsyncSession(session.bind) as observer:
        assert (await _law(observer, law_id)).short_title is None
    await session.commit()

    async with AsyncSession(session.bind) as observer:
        law = await _law(observer, law_id)
        assert law.short_title == "Finance Act 2030"
        assert law.title_tokens == title_tokens_for(
            law.title, language="eng", short_title="Finance Act 2030"
        )
        assert law.title_search_pipeline_version == TOKENISER_VERSION


async def test_a_named_law_is_not_parsed_again(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    vid, law_id = await _seed(session, doc_title="Finance Act 1958")
    await session.execute(
        text("UPDATE laws SET short_title = 'Kept Name' WHERE id = :i"), {"i": law_id}
    )
    await session.commit()

    def _refuse(*_args: object, **_kwargs: object) -> str | None:
        raise AssertionError("the document was parsed for a law that already has a name")

    monkeypatch.setattr(repository, "short_title_from_akn", _refuse)
    assert await fill_short_title_for_version(session, vid) is None
