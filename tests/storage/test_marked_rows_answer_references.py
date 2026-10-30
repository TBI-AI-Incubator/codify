"""A unit marked out of the pool is still law: a reference to it resolves, as
one to a placeholder never does."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.akn import Document, Paragraph, Section
from codify.akn.references import CrossReference as InlineCrossReference
from codify.storage.models import CrossReference, Provision
from codify.storage.repository import save_document
from codify.storage.resolve_refs import _provision_in_version, resolve_references_for_version
from codify.testing import postgres_url

pytestmark = pytest.mark.integration

_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_AKN = f"""<akomaNtoso xmlns="{_NS}"><act><body>
<section eId="sec_1"><paragraph eId="sec_1__para_1"><content><p>See below.</p></content>
</paragraph></section>
<section eId="sec_2" refersTo="#elsewhere"><paragraph eId="sec_2__para_2" refersTo="#elsewhere">
<content><p>The Committee shall <term eId="sec_2__para_2__term_1">report</term>.</p></content>
</paragraph></section>
<paragraph eId="para_9"><content><p>[TIFF not transcribed: annex.tif]</p></content></paragraph>
</body></act></akomaNtoso>"""


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    try:
        async with engine.connect() as c:
            if (await c.execute(text("SELECT to_regclass('provisions')"))).scalar() is None:
                pytest.skip("schema not migrated; run alembic upgrade head")
    except OSError:
        await engine.dispose()
        pytest.skip("postgres not reachable")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


def _doc(work: str) -> Document:
    see = InlineCrossReference(
        start_offset=0, end_offset=9, text_snippet="See below", target_eid="sec_2__para_2"
    )
    duty = Paragraph(
        akn_eid="sec_2__para_2",
        akn_type="paragraph",
        position=0,
        refers_to="#elsewhere",
        text="The Committee shall report.",
    )
    return Document(
        frbr_work_uri=work,
        frbr_expression_uri=f"{work}/eng@2020-01-01",
        language="eng",
        expression_date=date(2020, 1, 1),
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        text="See below.",
                        references=[see],
                    )
                ],
            ),
            Section(
                akn_eid="sec_2",
                akn_type="section",
                position=1,
                refers_to="#elsewhere",
                children=[duty],
            ),
            Paragraph(
                akn_eid="para_9",
                akn_type="paragraph",
                position=2,
                text="[TIFF not transcribed: annex.tif]",
            ),
        ],
    )


async def test_a_marked_row_answers_a_reference_and_a_placeholder_does_not(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "codify.storage.mappers.pool_excluded_refers_to_for", lambda _: frozenset({"#elsewhere"})
    )
    work = f"/akn/xa/act/2020/{uuid.uuid4().hex[:8]}"
    version = await save_document(
        session, _doc(work), jurisdiction_code="xa", law_title="T", year=2020, akn_xml=_AKN
    )
    rows = {
        eid: (pid, reason)
        for eid, pid, reason in (
            await session.execute(
                select(Provision.akn_eid, Provision.id, Provision.exclusion_reason).where(
                    Provision.version_id == version
                )
            )
        ).all()
    }
    assert {eid: reason for eid, (_, reason) in rows.items()} == {
        "sec_1__para_1": None,
        "sec_2__para_2": "marked",
        "para_9": "placeholder",
    }
    duty, placeholder = rows["sec_2__para_2"][0], rows["para_9"][0]

    # Saving links the in-document reference to the marked row.
    saved = (
        await session.execute(
            select(CrossReference.target_provision_id).where(
                CrossReference.source_provision_id == rows["sec_1__para_1"][0]
            )
        )
    ).scalar_one()
    assert saved == duty

    # A reference from another law reaches it by eId; a placeholder never answers.
    assert await _provision_in_version(session, version, "sec_2__para_2") == duty
    assert await _provision_in_version(session, version, "para_9") is None

    # The resolver lands on it as well, by eId or, for a part with no row of its
    # own, as the enclosing row; a placeholder's parts land nowhere.
    refs = {
        anchor: CrossReference(
            source_provision_id=rows["sec_1__para_1"][0],
            target_uri=f"#{anchor}",
            ref_type="cross_reference",
            edge_class="freetext_reference",
        )
        for anchor in ("sec_2__para_2", "sec_2__para_2__term_1", "para_9")
    }
    session.add_all(refs.values())
    await session.flush()
    await resolve_references_for_version(session, version)
    assert {a: r.target_provision_id for a, r in refs.items()} == {
        "sec_2__para_2": duty,
        "sec_2__para_2__term_1": duty,
        "para_9": None,
    }
    assert placeholder not in {r.target_provision_id for r in refs.values()}
