"""Official sources: recording rules, anchors, id lookup, lineage reads and the resolver rung."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.akn import Document, Paragraph
from codify.storage.models import (
    CrossReference,
    Jurisdiction,
    Law,
    OfficialSource,
    Provision,
    SourceDocument,
    Version,
)
from codify.storage.official_sources import (
    OfficialSourceRecord,
    clear_official_anchors,
    eid_for_fragment,
    get_official_source,
    get_official_sources,
    lookup_official_id,
    official_anchors,
    official_provision_url,
    record_official_source,
    upsert_official_ids,
    upsert_official_source,
    work_uri_for_official_url,
    write_official_anchors,
)
from codify.storage.repository import save_document
from codify.storage.resolve_refs import resolve_references_for_version
from codify.storage.versions import update_repaired_akn
from codify.testing import postgres_url

pytestmark = pytest.mark.integration

_AKN = "<akomaNtoso xmlns='http://docs.oasis-open.org/legaldocml/ns/akn/3.0'><act/></akomaNtoso>"
_RT = "riigi-teataja"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _version(
    session: AsyncSession,
    *,
    parent: uuid.UUID | None = None,
    sha: str | None = None,
    when: date = date(2024, 1, 1),
) -> uuid.UUID:
    tag = uuid.uuid4().hex[:8]
    if sha is not None:
        session.add(
            SourceDocument(sha256=sha, original_filename="a.xml", byte_size=1, object_key=sha)
        )
        await session.flush()
    juris = Jurisdiction(code=f"zz-{tag}", name="Test", languages=["en"])
    session.add(juris)
    await session.flush()
    law = Law(
        jurisdiction_id=juris.id, title=f"Act {tag}", doctype="act", frbr_work_uri=f"/akn/zz/{tag}"
    )
    session.add(law)
    await session.flush()
    version = Version(
        law_id=law.id,
        expression_uri=f"/akn/zz/{tag}/eng@{when}",
        language="eng",
        expression_date=when,
        akn_xml=_AKN,
        source_sha256=sha,
        parent_version_id=parent,
    )
    session.add(version)
    await session.flush()
    return version.id


def _record(**kw: object) -> OfficialSourceRecord:
    base: dict[str, object] = {"publisher": _RT, "work_id": "5555", "edition_id": "123"}
    return OfficialSourceRecord(**(base | kw))  # type: ignore[arg-type]


async def _row(session: AsyncSession, version_id: uuid.UUID) -> OfficialSource:
    await session.flush()
    session.expire_all()
    return (
        await session.execute(select(OfficialSource).where(OfficialSource.version_id == version_id))
    ).scalar_one()


async def test_a_new_version_gets_its_row_and_a_reuse_drops_a_foreign_hash(
    session: AsyncSession,
) -> None:
    sha = uuid.uuid4().hex * 2
    own = await _version(session, sha=sha)
    await record_official_source(
        session, own, _record(content_sha256=sha, licence="open"), stored=True
    )
    assert (await _row(session, own)).content_sha256 == sha

    other = await _version(session, sha=uuid.uuid4().hex * 2)
    await record_official_source(session, other, _record(content_sha256=sha), stored=False)
    assert (await _row(session, other)).content_sha256 is None


async def test_a_reused_version_only_has_its_empty_fields_filled(session: AsyncSession) -> None:
    version = await _version(session)
    await record_official_source(
        session, version, _record(edition_id="enacted", fetch_url="https://a"), stored=True
    )
    await record_official_source(
        session,
        version,
        _record(
            edition_id="2026-04-06",
            fetch_url="https://b",
            edition_date=date(2026, 4, 6),
            licence="open",
        ),
        stored=False,
    )
    row = await _row(session, version)
    assert (row.edition_id, row.fetch_url, row.edition_date, row.licence) == (
        "enacted",
        "https://a",
        date(2026, 4, 6),
        "open",
    )


async def test_a_refetch_replaces_the_row(session: AsyncSession) -> None:
    version = await _version(session)
    await record_official_source(session, version, _record(), stored=True)
    when = datetime(2026, 11, 1, tzinfo=UTC)
    await upsert_official_source(
        session, version, _record(retrieved_at=when, fetch_url="https://c")
    )
    row = await _row(session, version)
    assert (row.retrieved_at, row.fetch_url) == (when, "https://c")


async def test_a_translation_reads_its_sources_row_as_derived(session: AsyncSession) -> None:
    parent = await _version(session)
    child = await _version(session, parent=parent)
    grandchild = await _version(session, parent=child)
    bare = await _version(session)
    await record_official_source(session, parent, _record(), stored=True)
    read = await get_official_sources(session, [parent, child, grandchild, bare])
    assert read[parent].derived is False
    assert read[child].derived is True and read[grandchild].derived is True
    assert read[child].source.version_id == parent
    assert bare not in read
    await record_official_source(session, child, _record(edition_id="own"), stored=True)
    nearest = await get_official_source(session, grandchild)
    assert nearest is not None and nearest.source.edition_id == "own"
    assert await get_official_sources(session, []) == {}


async def test_anchors_write_look_up_both_ways_and_clear(session: AsyncSession) -> None:
    version = await _version(session)
    await write_official_anchors(
        session, version, {"art_10": "para10", "art_10__para_2": "para10lg2", "art_101": "para10b1"}
    )
    await write_official_anchors(session, version, {"art_10": "para10"})
    assert await official_anchors(session, version) == {
        "art_10": "para10",
        "art_10__para_2": "para10lg2",
        "art_101": "para10b1",
    }
    assert await eid_for_fragment(session, version, "para10b1") == "art_101"
    assert await eid_for_fragment(session, version, "para9") is None
    await write_official_anchors(session, version, {"art_10__para_1": "para10"})
    assert await eid_for_fragment(session, version, "para10") == "art_10"
    await clear_official_anchors(session, version)
    assert await official_anchors(session, version) == {}


async def test_a_repair_write_drops_the_versions_anchors(session: AsyncSession) -> None:
    version = await _version(session)
    other = await _version(session)
    await write_official_anchors(session, version, {"art_1": "para1"})
    await write_official_anchors(session, other, {"art_1": "para1"})
    await update_repaired_akn(session, version, _AKN.replace("<act/>", "<act> </act>"), "test")
    assert await official_anchors(session, version) == {}
    assert await official_anchors(session, other) == {"art_1": "para1"}


async def test_ids_upsert_and_survive_without_a_law(session: AsyncSession) -> None:
    pub = f"test-{uuid.uuid4().hex[:6]}"
    await upsert_official_ids(
        session,
        pub,
        [("5555", "/akn/zz/act/2020/a", "group"), ("123", "/akn/zz/act/2020/a", "redaction")],
    )
    await upsert_official_ids(session, pub, [("5555", "/akn/zz/act/2020/b", "group")])
    row = await lookup_official_id(session, pub, "5555")
    assert row is not None and (row.frbr_work_uri, row.id_kind) == ("/akn/zz/act/2020/b", "group")
    assert await lookup_official_id(session, pub, "missing") is None
    assert await work_uri_for_official_url(session, "https://example.org/akt/5555") is None
    count = (
        await session.execute(
            text("SELECT count(*) FROM official_ids WHERE publisher = :p"), {"p": pub}
        )
    ).scalar_one()
    assert count == 2


_BODY = (
    "<akomaNtoso xmlns='http://docs.oasis-open.org/legaldocml/ns/akn/3.0'><act><body>"
    "<paragraph eId='art_1'><content><p>Body.</p></content></paragraph>"
    "<paragraph eId='art_101'><content><p>Body.</p></content></paragraph>"
    "</body></act></akomaNtoso>"
)


def _doc(work: str, when: date, text_: str = "Body.") -> Document:
    return Document(
        frbr_work_uri=work,
        frbr_expression_uri=f"{work}/eng@{when}",
        language="eng",
        expression_date=when,
        body=[
            Paragraph(akn_eid="art_1", akn_type="paragraph", position=0, text=text_),
            Paragraph(akn_eid="art_101", akn_type="paragraph", position=1, text=text_),
        ],
    )


async def test_a_publisher_url_resolves_to_the_version_in_force_and_its_unit(
    session: AsyncSession,
) -> None:
    tag = uuid.uuid4().hex[:8]
    target_work = f"/akn/xa/act/2000/t-{tag}"
    early = await save_document(
        session,
        _doc(target_work, date(2020, 1, 1)),
        jurisdiction_code="xa",
        law_title="Target",
        year=2000,
        akn_xml=_BODY,
    )
    late = await save_document(
        session,
        _doc(target_work, date(2025, 1, 1), "Newer."),
        jurisdiction_code="xa",
        law_title="Target",
        year=2000,
        akn_xml=_BODY,
    )
    pub = _RT
    redaction, group = f"9{tag}"[:12], f"8{tag}"[:12]
    await upsert_official_ids(
        session, pub, [(redaction, target_work, "redaction"), (group, target_work, "group")]
    )
    await write_official_anchors(session, early, {"art_101": "para10b1"})
    await write_official_anchors(session, late, {"art_1": "para10b1"})

    citing = await save_document(
        session,
        _doc(f"/akn/xa/act/2021/c-{tag}", date(2021, 6, 1)),
        jurisdiction_code="xa",
        law_title="Citing",
        year=2021,
        akn_xml=_BODY,
    )
    source = (
        await session.execute(
            select(Provision.id).where(Provision.version_id == citing, Provision.akn_eid == "art_1")
        )
    ).scalar_one()
    urls = [
        f"https://www.riigiteataja.ee/akt/{redaction}#para10b1",
        f"https://www.riigiteataja.ee/akt/{redaction}",
        "https://www.riigiteataja.ee/akt/000000000000",
    ]
    for url in urls:
        session.add(
            CrossReference(
                source_provision_id=source,
                target_uri=url,
                ref_type="citation",
                edge_class="freetext_reference",
            )
        )
    await session.flush()

    stats = await resolve_references_for_version(session, citing)

    assert (stats.resolved_official, stats.unparsed, stats.unknown_law) == (2, 0, 1)
    rows = {
        r.target_uri: r
        for r in (
            await session.execute(
                select(CrossReference).where(CrossReference.source_provision_id == source)
            )
        ).scalars()
    }
    early_unit = (
        await session.execute(
            select(Provision.id).where(
                Provision.version_id == early, Provision.akn_eid == "art_101"
            )
        )
    ).scalar_one()
    # In force on the citing text's date, so the 2020 version, through the reverse anchor.
    assert rows[urls[0]].target_provision_id == early_unit
    law_id = (
        await session.execute(select(Law.id).where(Law.frbr_work_uri == target_work))
    ).scalar_one()
    assert rows[urls[1]].target_law_id == law_id
    assert rows[urls[2]].target_law_id is None


async def test_a_repeated_id_in_one_batch_keeps_the_last(session: AsyncSession) -> None:
    pub = f"test-{uuid.uuid4().hex[:6]}"
    await upsert_official_ids(
        session,
        pub,
        [("5", "/akn/zz/act/2020/a", "group"), ("5", "/akn/zz/act/2020/b", "group")],
    )
    row = await lookup_official_id(session, pub, "5")
    assert row is not None and row.frbr_work_uri == "/akn/zz/act/2020/b"


async def test_a_refetch_stores_the_hash_it_fetched_where_a_reuse_does_not(
    session: AsyncSession,
) -> None:
    version = await _version(session, sha=uuid.uuid4().hex * 2)
    fetched = uuid.uuid4().hex * 2
    await record_official_source(session, version, _record(content_sha256=fetched), stored=False)
    assert (await _row(session, version)).content_sha256 is None
    await upsert_official_source(session, version, _record(content_sha256=fetched))
    assert (await _row(session, version)).content_sha256 == fetched


async def test_a_provision_url_prefers_an_anchor_and_waits_for_verification(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from codify.provenance import PATTERN_VERIFIED

    version = await _version(session)
    await record_official_source(
        session,
        version,
        _record(publisher="legislation-gov-uk", work_id="ukpga/1998/42"),
        stored=True,
    )
    read = await get_official_source(session, version)
    assert read is not None
    source = read.source
    assert await official_provision_url(session, version, source, "section-13-2") is None
    monkeypatch.setitem(PATTERN_VERIFIED, "legislation-gov-uk.fragment.section.2", True)
    assert (
        await official_provision_url(session, version, source, "section-13-2")
        == "https://www.legislation.gov.uk/ukpga/1998/42/section/13/2/123"
    )
    await write_official_anchors(session, version, {"section-13-2": "section/13/2A"})
    url = await official_provision_url(session, version, source, "section-13-2")
    assert url is not None and "/section/13/2A" in url
    assert await official_provision_url(session, version, source, "crossheading-x") is None


async def test_a_citation_older_than_every_held_version_resolves_to_the_law_only(
    session: AsyncSession,
) -> None:
    tag = uuid.uuid4().hex[:8]
    target_work = f"/akn/xa/act/2000/f-{tag}"
    await save_document(
        session,
        _doc(target_work, date(2025, 1, 1)),
        jurisdiction_code="xa",
        law_title="Target",
        year=2000,
        akn_xml=_BODY,
    )
    redaction = f"7{tag}"
    await upsert_official_ids(session, _RT, [(redaction, target_work, "redaction")])
    citing = await save_document(
        session,
        _doc(f"/akn/xa/act/2019/c-{tag}", date(2019, 1, 1)),
        jurisdiction_code="xa",
        law_title="Citing",
        year=2019,
        akn_xml=_BODY,
    )
    source = (
        await session.execute(
            select(Provision.id).where(Provision.version_id == citing, Provision.akn_eid == "art_1")
        )
    ).scalar_one()
    ref = CrossReference(
        source_provision_id=source,
        target_uri=f"https://www.riigiteataja.ee/akt/{redaction}#para1",
        ref_type="citation",
        edge_class="freetext_reference",
    )
    session.add(ref)
    await session.flush()
    await resolve_references_for_version(session, citing)
    assert ref.target_provision_id is None and ref.target_law_id is not None


async def test_a_mapped_rada_url_resolves_through_official_ids_first(
    session: AsyncSession,
) -> None:
    tag = uuid.uuid4().hex[:8]
    work = f"/akn/xa/act/2000/r-{tag}"
    await save_document(
        session,
        _doc(work, date(2020, 1, 1)),
        jurisdiction_code="xa",
        law_title="Target",
        year=2000,
        akn_xml=_BODY,
    )
    nreg = f"{tag}-15"
    await upsert_official_ids(session, "zakon-rada", [(nreg, work, "nreg")])
    citing = await save_document(
        session,
        _doc(f"/akn/xa/act/2021/c-{tag}", date(2021, 1, 1)),
        jurisdiction_code="xa",
        law_title="Citing",
        year=2021,
        akn_xml=_BODY,
    )
    source = (
        await session.execute(
            select(Provision.id).where(Provision.version_id == citing, Provision.akn_eid == "art_1")
        )
    ).scalar_one()
    ref = CrossReference(
        source_provision_id=source,
        target_uri=f"https://zakon.rada.gov.ua/laws/show/{nreg}",
        ref_type="citation",
        edge_class="freetext_reference",
    )
    session.add(ref)
    await session.flush()
    stats = await resolve_references_for_version(session, citing)
    assert ref.target_law_id is not None and stats.resolved_official == 1
