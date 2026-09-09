"""Integration tests for aggregation and filtering in the findings read path."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from codify.lenses.types import Finding
from codify.storage.findings import (
    aggregate_findings,
    count_findings,
    list_findings,
    save_finding,
)
from codify.storage.lens_runs import create_lens_run
from codify.storage.models import Jurisdiction, Law, Version

pytestmark = pytest.mark.integration

_LENS = "anticorruption"


def _postgres_url() -> str:
    raw = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _table_exists(engine: AsyncEngine, name: str) -> bool:
    async with engine.connect() as c:
        result = await c.execute(text("SELECT to_regclass(:n)"), {"n": name})
        return result.scalar() is not None


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    url = _postgres_url()
    engine = create_async_engine(url, pool_pre_ping=True)
    if not await _table_exists(engine, "findings"):
        await engine.dispose()
        pytest.skip("findings table not migrated; run alembic upgrade head")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """A version + run + four varied findings; returns (version_id, run_id)."""
    suffix = uuid.uuid4().hex[:8]
    j = Jurisdiction(code=f"zz-{suffix}", name="Test", languages=["en"])
    session.add(j)
    await session.flush()
    law = Law(
        jurisdiction_id=j.id,
        title=f"Act {suffix}",
        doctype="act",
        frbr_work_uri=f"/akn/zz/{suffix}/2026/1",
    )
    session.add(law)
    await session.flush()
    v = Version(
        law_id=law.id,
        expression_uri=f"/akn/zz/{suffix}/2026/1/eng@2026-01-01",
        language="en",
        expression_date=date(2026, 1, 1),
        akn_xml="<akomaNtoso/>",
    )
    session.add(v)
    await session.flush()
    run_id = uuid.uuid4()
    await create_lens_run(
        session, lens_run_id=run_id, lens_name=_LENS, version_id=v.id, prompt_version="v1"
    )
    rows = [
        ("high", "F1", "art_1__p_1"),
        ("high", "F1", "art_1__p_2"),
        ("medium", "F2", "art_2__p_1"),
        ("low", "F1", "art_2__p_2"),
    ]
    for severity, factor, eid in rows:
        await save_finding(
            session,
            Finding(
                lens_run_id=run_id,
                lens_name=_LENS,
                version_id=v.id,
                provision_eid=eid,
                severity=severity,
                confidence=0.5,
                rationale="seed",
                payload={"factor_code": factor, "risk_class": "corruption"},
            ),
        )
    await session.flush()
    return v.id, run_id


async def test_aggregate_by_severity_factor_and_section(session: AsyncSession) -> None:
    version_id, run_id = await _seed(session)
    by_sev = dict(await aggregate_findings(session, version_id, _LENS, group_by="severity"))
    assert by_sev == {"high": 2, "medium": 1, "low": 1}
    by_factor = dict(await aggregate_findings(session, version_id, _LENS, group_by="factor_code"))
    assert by_factor == {"F1": 3, "F2": 1}
    by_section = dict(await aggregate_findings(session, version_id, _LENS, group_by="section"))
    assert by_section == {"art_1": 2, "art_2": 2}  # folds by the outermost eId segment


async def test_filters_narrow_the_listing_and_count(session: AsyncSession) -> None:
    version_id, _ = await _seed(session)
    high, _ = await list_findings(session, version_id, _LENS, severity="high")
    assert len(high) == 2 and all(f.severity == "high" for f in high)
    f2, _ = await list_findings(session, version_id, _LENS, factor_code="F2")
    assert len(f2) == 1
    # provision_eid prefix matches the section's descendants.
    sec1, _ = await list_findings(session, version_id, _LENS, provision_eid="art_1")
    assert len(sec1) == 2
    assert await count_findings(session, version_id, _LENS, severity="high") == 2
    assert await count_findings(session, version_id, _LENS, factor_code="F1") == 3


async def _add_finding(
    session: AsyncSession, run_id: uuid.UUID, version_id: uuid.UUID, eid: str
) -> None:
    await save_finding(
        session,
        Finding(
            lens_run_id=run_id,
            lens_name=_LENS,
            version_id=version_id,
            provision_eid=eid,
            severity="high",
            confidence=0.5,
            rationale="seed",
            payload={"factor_code": "F1", "risk_class": "corruption"},
        ),
    )
    await session.flush()


async def test_provision_eid_prefix_no_sibling_overmatch(session: AsyncSession) -> None:
    version_id, run_id = await _seed(session)
    # eIds are underscore-dense: the art_1 prefix must not capture the double-digit
    # sibling art_10 (the LIKE-wildcard trap the autoescape guards against).
    await _add_finding(session, run_id, version_id, "art_10__p_1")
    sec1, _ = await list_findings(session, version_id, _LENS, provision_eid="art_1")
    assert len(sec1) == 2 and all(f.provision_eid.startswith("art_1__") for f in sec1)
    assert await count_findings(session, version_id, _LENS, provision_eid="art_1") == 2
    # Exact-leaf query returns just that leaf, not its siblings.
    leaf, _ = await list_findings(session, version_id, _LENS, provision_eid="art_1__p_1")
    assert len(leaf) == 1 and leaf[0].provision_eid == "art_1__p_1"


async def test_aggregate_scopes_to_the_requested_run(session: AsyncSession) -> None:
    version_id, run1 = await _seed(session)
    # A second run on the same version whose findings must not bleed into run1's stats.
    run2 = uuid.uuid4()
    await create_lens_run(
        session, lens_run_id=run2, lens_name=_LENS, version_id=version_id, prompt_version="v1"
    )
    await save_finding(
        session,
        Finding(
            lens_run_id=run2,
            lens_name=_LENS,
            version_id=version_id,
            provision_eid="art_9__p_1",
            severity="critical",
            confidence=0.5,
            rationale="other run",
            payload={"factor_code": "F9", "risk_class": "corruption"},
        ),
    )
    await session.flush()
    scoped = dict(
        await aggregate_findings(session, version_id, _LENS, group_by="severity", lens_run_id=run1)
    )
    assert scoped == {"high": 2, "medium": 1, "low": 1}  # run2's critical excluded
