"""`provision_embeddings` is partitioned by jurisdiction. A row lands in its
jurisdiction's partition carrying the version it belongs to, and a scoped search
reads that partition's own vector index rather than one graph over the corpus."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.storage.embeddings import embed_version_provisions, upsert_embedding
from codify.storage.jurisdictions import get_or_create_jurisdiction
from codify.storage.models import Jurisdiction, Law, Provision, Version
from codify.storage.partitions import embedding_partition_name
from codify.storage.retrieval import _DENSE_ONLY_SQL, _HYBRID_SQL, hybrid_search
from codify.testing import postgres_url

pytestmark = pytest.mark.integration

_DIM = 768


def _vec(seed: float) -> list[float]:
    # Cosine distance is about direction, so the seed tilts a fixed axis.
    return [1.0, seed] + [0.0] * (_DIM - 2)


class _FixedVectors:
    """Embeds by position, so the seed decides the neighbour order."""

    model = "test-model"

    async def embed_documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
        return [_vec(0.1 * (i + 1)) for i in range(len(items))]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _seed(
    session: AsyncSession, code: str, provisions: int = 3
) -> tuple[Jurisdiction, Version]:
    jurisdiction = await get_or_create_jurisdiction(session, code, languages=["en"])
    law = Law(
        jurisdiction_id=jurisdiction.id,
        title=f"Act {code}",
        doctype="act",
        frbr_work_uri=f"/akn/{code}/act/2026/1",
    )
    session.add(law)
    await session.flush()
    version = Version(
        law_id=law.id,
        expression_uri=f"/akn/{code}/act/2026/1/eng@2026-01-01",
        language="en",
        expression_date=date(2026, 1, 1),
        akn_xml="<akomaNtoso/>",
    )
    session.add(version)
    await session.flush()
    for i in range(provisions):
        session.add(
            Provision(
                version_id=version.id,
                akn_eid=f"sec_{i}",
                akn_wid=f"sec_{i}",
                akn_type="section",
                text=f"Provision {i} of {code} says something.",
                position=i,
            )
        )
    await session.flush()
    return jurisdiction, version


async def test_a_new_jurisdiction_gets_a_partition_with_its_own_vector_index(
    session: AsyncSession,
) -> None:
    code = f"zp{uuid.uuid4().hex[:6]}"
    jurisdiction = await get_or_create_jurisdiction(session, code)
    partition = embedding_partition_name(code, jurisdiction.id)
    bound = (
        await session.execute(
            text(
                "SELECT pg_get_expr(c.relpartbound, c.oid) FROM pg_class c WHERE c.relname = :name"
            ),
            {"name": partition},
        )
    ).scalar_one()
    assert str(jurisdiction.id) in bound
    # The parent's HNSW is partitioned, so the child index exists and is attached.
    child_indexes = (
        (
            await session.execute(
                text(
                    "SELECT i.indexrelid::regclass::text FROM pg_index i "
                    "JOIN pg_inherits h ON h.inhrelid = i.indexrelid "
                    "WHERE i.indrelid = CAST(:name AS regclass) "
                    "AND h.inhparent = 'provision_embeddings_hnsw_idx'::regclass"
                ),
                {"name": partition},
            )
        )
        .scalars()
        .all()
    )
    assert child_indexes, "the partition has no vector index attached to the parent's"


async def test_the_writer_routes_a_row_to_its_partition_with_its_version(
    session: AsyncSession,
) -> None:
    code = f"zw{uuid.uuid4().hex[:6]}"
    jurisdiction, version = await _seed(session, code)
    written = await embed_version_provisions(
        session,
        version.id,
        client=_FixedVectors(),  # type: ignore[arg-type]
    )
    assert written == 3
    rows = (
        await session.execute(
            text(
                "SELECT tableoid::regclass::text, version_id, jurisdiction_id "
                "FROM provision_embeddings WHERE version_id = :version"
            ),
            {"version": version.id},
        )
    ).all()
    assert len(rows) == 3
    assert {r[0] for r in rows} == {embedding_partition_name(code, jurisdiction.id)}
    assert {r[1] for r in rows} == {version.id}
    assert {r[2] for r in rows} == {jurisdiction.id}


async def test_an_embedding_for_a_jurisdiction_with_no_partition_fails_loudly(
    session: AsyncSession,
) -> None:
    """A jurisdiction row written around `get_or_create_jurisdiction` has no
    partition; the insert refuses rather than misfiling the vector."""
    jurisdiction = Jurisdiction(code=f"zn{uuid.uuid4().hex[:6]}", name="No partition")
    session.add(jurisdiction)
    await session.flush()
    law = Law(
        jurisdiction_id=jurisdiction.id,
        title="Act",
        doctype="act",
        frbr_work_uri=f"/akn/{jurisdiction.code}/act/2026/1",
    )
    session.add(law)
    await session.flush()
    version = Version(
        law_id=law.id,
        expression_uri=f"/akn/{jurisdiction.code}/act/2026/1/eng@2026-01-01",
        language="en",
        expression_date=date(2026, 1, 1),
        akn_xml="<akomaNtoso/>",
    )
    session.add(version)
    await session.flush()
    provision = Provision(
        version_id=version.id,
        akn_eid="sec_1",
        akn_wid="sec_1",
        akn_type="section",
        text="Orphaned.",
        position=0,
    )
    session.add(provision)
    await session.flush()
    with pytest.raises(Exception, match="no partition of relation"):
        await upsert_embedding(session, provision.id, _vec(0.5), "test-model")


async def test_a_scoped_search_is_served_from_the_partition(session: AsyncSession) -> None:
    code = f"zs{uuid.uuid4().hex[:6]}"
    jurisdiction, version = await _seed(session, code)
    await embed_version_provisions(session, version.id, client=_FixedVectors())  # type: ignore[arg-type]
    # Three rows cost nothing to scan or sort; the shape under test is that the
    # ordered vector index scan can serve this query at all.
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    await session.execute(text("SET LOCAL enable_bitmapscan = off"))
    await session.execute(text("SET LOCAL enable_sort = off"))
    await session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
    params = {
        "query_vec": _vec(0.26),
        "version_ids": [version.id],
        "jurisdiction_ids": [jurisdiction.id],
        "k": 2,
        "pool": 10,
        "rrf_k": 60,
        "query_tokens": "provision says",
        "query_match": "provision | says",
        "model_id": "test-model",
        "fallback_model_id": None,
        "akn_type": None,
        "include_non_normative": False,
    }
    partition = embedding_partition_name(code, jurisdiction.id)
    # Both dense paths: the hybrid arm is what a worded query runs.
    for sql in (_HYBRID_SQL, _DENSE_ONLY_SQL):
        plan = "\n".join(
            row[0]
            for row in (
                await session.execute(
                    text("EXPLAIN " + str(sql)).bindparams(
                        bindparam("query_vec", type_=HALFVEC(_DIM)),
                        bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
                        bindparam("jurisdiction_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
                    ),
                    {k: v for k, v in params.items() if f":{k}" in str(sql)},
                )
            ).all()
        )
        assert f"Index Scan using {partition}_embedding_idx" in plan, plan
    # And the answer is the nearest provisions by vector, in order.
    rows = await hybrid_search(
        session,
        query_vec=_vec(0.26),
        query_text="",
        version_ids=[version.id],
        k=2,
        candidate_pool=10,
        rrf_k=60,
        model_id="test-model",
    )
    assert [r[1] for r in rows] == ["sec_2", "sec_1"]


# Migration on a populated database: rows, scope values and partitions after
# the swap, and the flat table back after the downgrade.

_CORE_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
_BASE_URL = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")


def _sync(url: str) -> str:
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


@pytest.fixture
def temp_db() -> Iterator[str]:
    admin = create_engine(_sync(_BASE_URL), isolation_level="AUTOCOMMIT")
    try:
        admin.connect().close()
    except Exception:
        admin.dispose()
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail("REQUIRE_DB=1 but POSTGRES_URL is not reachable")
        pytest.skip("POSTGRES_URL not reachable")
    name = f"corepart_{uuid.uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(_BASE_URL).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def _migrate(url: str, direction, revision: str) -> None:
    previous = os.environ.get("POSTGRES_URL")
    os.environ["POSTGRES_URL"] = url
    try:
        direction(Config(str(_CORE_INI)), revision)
    finally:
        if previous is None:
            os.environ.pop("POSTGRES_URL", None)
        else:
            os.environ["POSTGRES_URL"] = previous


def _seed_flat(conn, jurisdictions: int, per: int) -> dict[str, list[tuple[str, str]]]:
    """Two jurisdictions of flat-schema rows; returns provision → (version, jurisdiction)."""
    expected: dict[str, list[tuple[str, str]]] = {}
    for j in range(jurisdictions):
        code = f"zm{j}"
        jid, lid, vid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO jurisdictions (id, code, name, calendar, languages, extra) "
                "VALUES (:id, :code, :code, 'gregorian', '{}', '{}')"
            ),
            {"id": jid, "code": code},
        )
        conn.execute(
            text(
                "INSERT INTO laws (id, jurisdiction_id, title, doctype, frbr_work_uri) "
                "VALUES (:id, :j, 'Act', 'act', :uri)"
            ),
            {"id": lid, "j": jid, "uri": f"/akn/{code}/act/2026/1"},
        )
        conn.execute(
            text(
                "INSERT INTO versions (id, law_id, expression_uri, language, expression_date, "
                "akn_xml) VALUES (:id, :law, :uri, 'en', '2026-01-01', '<akomaNtoso/>')"
            ),
            {"id": vid, "law": lid, "uri": f"/akn/{code}/act/2026/1/eng@2026-01-01"},
        )
        expected[code] = []
        for i in range(per):
            pid = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO provisions (id, version_id, akn_eid, akn_wid, akn_type, text, "
                    "position) VALUES (:id, :v, :eid, :eid, 'section', 'text', :pos)"
                ),
                {"id": pid, "v": vid, "eid": f"sec_{i}", "pos": i},
            )
            conn.execute(
                text(
                    "INSERT INTO provision_embeddings (provision_id, embedding, model_id) "
                    "VALUES (:p, :vec, 'm')"
                ),
                {"p": pid, "vec": str(_vec(0.1 * i))},
            )
            expected[code].append((str(vid), str(jid)))
    return expected


def test_the_migration_carries_every_row_into_its_partition_and_back(temp_db: str) -> None:
    _migrate(temp_db, command.upgrade, "0019_search_term_lexicon")
    engine = create_engine(_sync(temp_db))
    try:
        with engine.begin() as conn:
            expected = _seed_flat(conn, jurisdictions=2, per=3)
        _migrate(temp_db, command.upgrade, "head")
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT j.code, e.version_id::text, e.jurisdiction_id::text, "
                    "e.tableoid::regclass::text FROM provision_embeddings e "
                    "JOIN jurisdictions j ON j.id = e.jurisdiction_id ORDER BY 1"
                )
            ).all()
            assert len(rows) == 6
            for code, version, jurisdiction, partition in rows:
                assert (version, jurisdiction) in expected[code]
                assert partition == embedding_partition_name(code, uuid.UUID(jurisdiction))
            valid = conn.execute(
                text(
                    "SELECT indisvalid FROM pg_index "
                    "WHERE indexrelid = 'provision_embeddings_hnsw_idx'::regclass"
                )
            ).scalar_one()
            # No partition built its vector index yet: that is the runbook's step.
            assert valid is False
            assert _suffixed_constraints(conn) == []
        _migrate(temp_db, command.downgrade, "0019_search_term_lexicon")
        with engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM provision_embeddings")).scalar_one()
            assert count == 6
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'provision_embeddings'"
                    )
                )
            }
            assert "jurisdiction_id" not in columns
            assert _suffixed_constraints(conn) == []
    finally:
        engine.dispose()


def _suffixed_constraints(conn) -> list[tuple[str, str]]:
    """Names Postgres deduplicated with a digit while the old table held them,
    on the table and its partitions; the migration restores the bare names."""
    return [
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT c.conrelid::regclass::text, c.conname FROM pg_constraint c "
                "JOIN pg_partition_tree('provision_embeddings'::regclass) t "
                "ON t.relid = c.conrelid WHERE c.conname ~ '[a-z]\\d+$'"
            )
        ).all()
    ]


async def test_codes_that_fold_alike_get_their_own_partitions(session: AsyncSession) -> None:
    stem = uuid.uuid4().hex[:5]
    first = await get_or_create_jurisdiction(session, f"z{stem}-a")
    second = await get_or_create_jurisdiction(session, f"z{stem}_a")
    assert embedding_partition_name(first.code, first.id) != embedding_partition_name(
        second.code, second.id
    )
    partitions = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_inherits "
                "WHERE inhparent = 'provision_embeddings'::regclass "
                "AND inhrelid::regclass::text LIKE :stem"
            ),
            {"stem": f"provision_embeddings_p_z{stem}%"},
        )
    ).scalar_one()
    assert partitions == 2
