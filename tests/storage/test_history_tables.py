"""The history tables against Postgres: models match, builder rows fit, the guard holds."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import fields
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from codify.history.build import SourceText, Unit, build_work
from codify.storage.models import (
    DatedExpression,
    HistoryWithdrawal,
    HistoryWork,
    Jurisdiction,
    ProvisionInterval,
    ProvisionLink,
    PublisherText,
)
from codify.testing import postgres_url

pytestmark = pytest.mark.integration

_MODELS: tuple[type[SQLModel], ...] = (
    HistoryWork,
    PublisherText,
    DatedExpression,
    ProvisionInterval,
    ProvisionLink,
    HistoryWithdrawal,
)
_DERIVED = ("dated_expressions", "provision_intervals", "provision_links", "history_withdrawals")
_WORDING = "A vessel entering the harbour shall pay the berthing fee set out in the schedule."


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _work(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    juris = Jurisdiction(code=f"zz-{uuid.uuid4().hex[:8]}", name="Test", languages=["en"])
    session.add(juris)
    await session.flush()
    work = HistoryWork(jurisdiction_id=juris.id)
    session.add(work)
    await session.flush()
    return juris.id, work.id


def _text(ref: str, start: date, units: tuple[Unit, ...], **kw: Any) -> SourceText:
    args: dict[str, Any] = {
        "language": "eng",
        "valid_from": start,
        "source_ref": ref,
        "source_sha256": ref * 4,
        "akn_xml": "<akomaNtoso/>",
        "units": units,
        "converter_rev": "c1",
    }
    return SourceText(**{**args, **kw})


def _unit(eid: str, body: str) -> Unit:
    return Unit(eid=eid, akn_type="section", position=1, text=body)


def _row(model: type[SQLModel], source: Any) -> SQLModel:
    return model(**{f.name: getattr(source, f.name) for f in fields(source)})


async def test_each_model_names_the_columns_of_its_table(session: AsyncSession) -> None:
    for model in _MODELS:
        table = model.__table__  # type: ignore[attr-defined]
        got = await session.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table.name},
        )
        in_db = {name: nullable == "YES" for name, nullable in got}
        assert in_db, table.name
        assert {c.name: c.nullable for c in table.columns} == in_db, table.name


async def test_what_the_builder_derives_is_stored_as_it_stands(session: AsyncSession) -> None:
    jurisdiction_id, work_id = await _work(session)
    texts = [
        _text("aa", date(2024, 1, 1), (_unit("chp_1__sec_1", _WORDING),)),
        _text("bb", date(2024, 3, 1), (_unit("chp_2__sec_1", _WORDING),)),
        _text("cc", date(2024, 6, 1), (), akn_xml=None),
        SourceText(
            language="eng",
            valid_from=date(2024, 9, 1),
            akn_xml="<akomaNtoso/>",
            units=(_unit("chp_1__sec_9", _WORDING),),
            derivation="applied_effects",
            official=False,
            confidence=0.7,
            derived_from={"base": "2024-03-01"},
            converter_rev="c1",
        ),
    ]
    now = datetime(2026, 11, 23, tzinfo=UTC)
    first = build_work(work_id, jurisdiction_id, texts, now=now)
    rebuilt = build_work(work_id, jurisdiction_id, texts[:2], now=now, previous=first)
    for t in texts[:3]:
        session.add(
            PublisherText(
                jurisdiction_id=jurisdiction_id,
                source_ref=str(t.source_ref),
                source_sha256=str(t.source_sha256),
                language=t.language,
                publisher_valid_from=t.valid_from,
                work_id=work_id,
            )
        )
    await session.flush()
    for rows in (first.expressions, first.intervals, first.links, rebuilt.withdrawals):
        for r in rows:
            session.add(_row(_model_for(r), r))
    await session.flush()
    for table, expected in (
        ("dated_expressions", len(first.expressions)),
        ("provision_intervals", len(first.intervals)),
        ("provision_links", len(first.links)),
        ("history_withdrawals", len(rebuilt.withdrawals)),
    ):
        assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == expected > 0


def _model_for(row: Any) -> type[SQLModel]:
    models: dict[str, type[SQLModel]] = {
        "Expression": DatedExpression,
        "Interval": ProvisionInterval,
        "Link": ProvisionLink,
        "Withdrawal": HistoryWithdrawal,
    }
    return models[type(row).__name__]


async def _insert_text(session: AsyncSession, jurisdiction_id: uuid.UUID, ref: str) -> None:
    await session.execute(
        text(
            "INSERT INTO publisher_texts (jurisdiction_id, source_ref, source_sha256, language, "
            "publisher_valid_from, source_meta) "
            "VALUES (:j, :r, 'h', 'eng', '2024-01-01', '{\"a\": 1}')"
        ),
        {"j": jurisdiction_id, "r": ref},
    )


async def test_a_publisher_text_changes_only_its_work_assignment(session: AsyncSession) -> None:
    jurisdiction_id, work_id = await _work(session)
    await _insert_text(session, jurisdiction_id, "r1")
    allowed = "UPDATE publisher_texts SET work_id = :w, work_rule = '2' WHERE source_ref = 'r1'"
    await session.execute(text(allowed), {"w": work_id})
    await session.execute(text(allowed.replace(":w", "NULL")))
    for column, value in (
        ("language", "'deu'"),
        ("source_sha256", "'x'"),
        ("publisher_valid_from", "'2025-01-01'"),
        ("source_meta", "'{}'"),
        ("recorded_at", "now() + interval '1 day'"),
        ("publisher_group", "'g'"),
    ):
        with pytest.raises(DBAPIError, match="immutable apart from work_id and work_rule"):
            async with session.begin_nested():
                await session.execute(text(f"UPDATE publisher_texts SET {column} = {value}"))
    await session.execute(text("DELETE FROM publisher_texts WHERE source_ref = 'r1'"))


async def test_deleting_a_work_clears_the_assignment_without_touching_the_fact(
    session: AsyncSession,
) -> None:
    jurisdiction_id, work_id = await _work(session)
    await _insert_text(session, jurisdiction_id, "r2")
    await session.execute(
        text("UPDATE publisher_texts SET work_id = :w WHERE source_ref = 'r2'"), {"w": work_id}
    )
    await session.execute(text("DELETE FROM history_works WHERE id = :w"), {"w": work_id})
    row = (
        await session.execute(
            text("SELECT work_id, language FROM publisher_texts WHERE source_ref = 'r2'")
        )
    ).one()
    assert tuple(row) == (None, "eng")


async def test_the_derived_tables_carry_no_trigger(session: AsyncSession) -> None:
    found = await session.execute(
        text(
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relname = ANY(:names)"
        ),
        {"names": [*_DERIVED, "history_works"]},
    )
    assert found.all() == []


@pytest.mark.parametrize(
    "assignment",
    [
        "valid_to = valid_from",
        "confidence = 1.5",
        "akn_xml = NULL",
        "gap_reason = 'unreadable'",
    ],
)
async def test_a_span_row_that_breaks_a_check_is_refused(
    session: AsyncSession, assignment: str
) -> None:
    jurisdiction_id, work_id = await _work(session)
    params = {"w": work_id, "j": jurisdiction_id}
    await session.execute(
        text(
            "INSERT INTO dated_expressions (work_id, language, valid_from, jurisdiction_id, "
            "akn_xml, derivation, official, converter_rev, builder_rev, recorded_at) "
            "VALUES (:w, 'eng', '2024-01-01', :j, '<a/>', 'publisher_text', true, 'c', 'b', now())"
        ),
        params,
    )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(f"UPDATE dated_expressions SET {assignment} WHERE work_id = :w"), params
            )
