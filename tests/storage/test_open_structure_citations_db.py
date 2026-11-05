"""What a stored guidance section or decision cites, resolved against a stored Act."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.akn.io import parse_akn
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.storage.graph import citation_chain
from codify.storage.models import CrossReference, Law, Provision, Version
from codify.storage.provisions import find_provision_id_by_eid
from codify.storage.refresh_refs import refresh_references_for_version
from codify.storage.repository import save_document
from codify.storage.resolve_refs import resolve_references_for_version
from codify.testing import postgres_url
from tests.ingestion.test_open_structure import SOURCES, ingest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_FOI = Path(__file__).parents[1] / "fixtures/synthetic/xa/freedom-of-information-2001.bluebell"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _law_of(session: AsyncSession, version_id: uuid.UUID) -> Law:
    return (
        await session.execute(
            select(Law).join(Version, Version.law_id == Law.id).where(Version.id == version_id)
        )
    ).scalar_one()


async def _cited_act(session: AsyncSession) -> uuid.UUID:
    akn = parse_to_akn(_FOI.read_text(encoding="utf-8"), "xa", date="2001", number="3")
    vid = await save_document(
        session,
        parse_akn(akn),
        jurisdiction_code="xa",
        law_title="Freedom of Information Act, 2001",
        akn_xml=akn,
        year=2001,
        number="3",
    )
    return (await _law_of(session, vid)).id


async def _stored(session: AsyncSession, doctype: str, locality: str | None) -> uuid.UUID:
    done = await ingest(doctype, locality)
    _, title, date, number = SOURCES[doctype]
    vid = await save_document(
        session,
        done.document,
        jurisdiction_code="xa",
        law_title=title,
        doctype=doctype,
        akn_xml=done.akn_xml,
        year=int(date[:4]),
        number=number,
    )
    await resolve_references_for_version(session, vid)
    return vid


async def _edges(session: AsyncSession, vid: uuid.UUID) -> list[tuple[str, bool, uuid.UUID | None]]:
    """Each citing row's eId, whether it is operative, and the law it resolved to."""
    rows = await session.execute(
        select(Provision.akn_eid, Provision.normative, CrossReference.target_law_id)
        .join(CrossReference, CrossReference.source_provision_id == Provision.id)
        .where(Provision.version_id == vid)
        .order_by(Provision.akn_eid)
    )
    return [tuple(r) for r in rows]


async def _cites(session: AsyncSession, vid: uuid.UUID, eid: str) -> list[tuple[str | None, str]]:
    """What a consumer asks of one unit: each edge's target eId, or the cited work."""
    pid = await find_provision_id_by_eid(session, vid, eid)
    assert pid is not None, eid
    chain = await citation_chain(session, pid, direction="outbound", max_depth=1)
    return [(n.akn_eid, n.target_uri or "") for n in chain.nodes]


async def test_a_decision_cites_the_act_from_its_recitals_and_its_operative_part(
    session: AsyncSession,
) -> None:
    act = await _cited_act(session)
    vid = await _stored(session, "determination", "c01")
    law = await _law_of(session, vid)
    assert (law.frbr_work_uri, law.doctype) == (
        "/akn/xa-c01/statement/determination/2024/9",
        "determination",
    )
    expected = [("recitals__rec_1", False, act), ("sec_1__content", True, act)]
    assert await _edges(session, vid) == expected
    assert await _cites(session, vid, "recitals__rec_1") == [(None, "/akn/xa/act/2001/3")]

    await refresh_references_for_version(session, vid)
    assert await _edges(session, vid) == expected


async def test_a_guidance_section_cites_the_act_and_another_section(session: AsyncSession) -> None:
    act = await _cited_act(session)
    vid = await _stored(session, "guidance", None)
    assert await _edges(session, vid) == [
        ("part_1__sec_1__content", True, act),
        ("part_2__sec_2__content", True, None),
    ]
    assert await _cites(session, vid, "part_2__sec_2__content") == [
        ("part_1__sec_1__content", "#part_1__sec_1")
    ]


async def test_a_schedules_citation_survives_a_refresh(session: AsyncSession) -> None:
    act = await _cited_act(session)
    bluebell = (
        "BODY\n\nSECTION 1\n\n  This note applies to every request.\n\n"
        "SCHEDULE - Model reply\n\n  SECTION 1\n\n"
        "    A reply cites {{>/akn/xa/act/2001/3 the Freedom of Information Act, 2001}}.\n"
    )
    akn = parse_to_akn(bluebell, "xa", doctype="guidance", date="2024-07-01", number="7")
    vid = await save_document(
        session, parse_akn(akn), jurisdiction_code="xa", law_title="Model replies", akn_xml=akn
    )
    await resolve_references_for_version(session, vid)
    expected = [("att_1__sec_1__content", True, act)]
    assert await _edges(session, vid) == expected

    await refresh_references_for_version(session, vid)
    assert await _edges(session, vid) == expected
