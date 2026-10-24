"""Rederive runs its parse, mapping, table and code passes on the CPU pool, and
writes the same rows the ingest path writes from the same AKN."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import codify.storage.annex_rows as annex_rows
import codify.storage.goods_codes as goods_codes
import codify.storage.repository as repository
from codify.akn.io import parse_akn
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.goods_codes import extract_goods_codes
from codify.search import tokenise_to_text
from codify.storage.annex_rows import index_version_annex_rows
from codify.storage.goods_codes import index_version_goods_codes
from codify.storage.repository import rederive_version_rows, save_document
from codify.tables.resolve import resolve_tables

pytestmark = pytest.mark.integration

_TARIFF = (
    Path(__file__).parents[1] / "fixtures/synthetic/xu/regulation-2006-3-customs-tariff.bluebell"
)

# Invented: a schedule whose header names a nomenclature, so codes are read off it.
_SCHEDULE = """BODY
  ARTICLE 1 - Scope
    This Regulation applies to the goods listed in Article 2.
  ARTICLE 2 - Schedule
    The goods are:

    TABLE
      TR
        TH
          CN code
        TH
          Description
      TR
        TC
          ex 8471 30 90
        TC
          Other portable machines
      TR
        TC
          2710 19 43
        TC
          Gas oils
"""

# Each derived table, keyed off the version and stripped of what a rewrite mints
# afresh (ids, timestamps); a link to another row is carried as that row's eId.
_SNAPSHOT_SQL = {
    "sections": """
        SELECT (to_jsonb(s) - ARRAY['id', 'version_id', 'parent_section_id', 'created_at',
                'updated_at']) || jsonb_build_object('parent', ps.akn_eid)
        FROM sections s LEFT JOIN sections ps ON ps.id = s.parent_section_id
        WHERE s.version_id = :v""",
    "provisions": """
        SELECT (to_jsonb(p) - ARRAY['id', 'version_id', 'section_id', 'created_at',
                'updated_at']) || jsonb_build_object('section', s.akn_eid)
        FROM provisions p LEFT JOIN sections s ON s.id = p.section_id
        WHERE p.version_id = :v""",
    "cross_references": """
        SELECT (to_jsonb(c) - ARRAY['id', 'source_provision_id', 'target_provision_id',
                'target_section_id', 'created_at'])
               || jsonb_build_object('source', sp.akn_eid, 'target', tp.akn_eid)
        FROM cross_references c JOIN provisions sp ON sp.id = c.source_provision_id
        LEFT JOIN provisions tp ON tp.id = c.target_provision_id
        WHERE sp.version_id = :v""",
    "amendment_effects": """
        SELECT to_jsonb(a) - ARRAY['id', 'version_id', 'created_at', 'updated_at']
        FROM amendment_effects a WHERE a.version_id = :v""",
    "lifecycle_events": """
        SELECT to_jsonb(e) - ARRAY['id', 'version_id', 'created_at', 'updated_at']
        FROM lifecycle_events e WHERE e.version_id = :v""",
    "annex_rows": """
        SELECT to_jsonb(r) - ARRAY['id', 'version_id', 'created_at']
        FROM annex_rows r WHERE r.version_id = :v""",
    "goods_code_references": """
        SELECT to_jsonb(g) - ARRAY['id', 'version_id', 'created_at']
        FROM goods_code_references g WHERE g.version_id = :v""",
}


def _url() -> str:
    raw = os.environ.get("POSTGRES_URL", "postgresql://codify:codify@localhost:5432/codify")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _snapshot(session: AsyncSession, version_id: uuid.UUID) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for table, sql in _SNAPSHOT_SQL.items():
        rows = (await session.execute(text(sql), {"v": version_id})).scalars().all()
        out[table] = sorted(json.dumps(row, sort_keys=True) for row in rows)
    return out


def _on_thread(fn: Callable[..., Any], name: str, seen: dict[str, str]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        seen[name] = threading.current_thread().name
        return fn(*args, **kwargs)

    return wrapper


@pytest.mark.parametrize(
    ("source", "expect_codes"),
    [(_TARIFF.read_text(encoding="utf-8"), False), (_SCHEDULE, True)],
    ids=["customs-tariff", "cn-schedule"],
)
async def test_rederive_writes_the_ingest_rows_from_the_cpu_pool(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, source: str, expect_codes: bool
) -> None:
    suffix = uuid.uuid4().hex[:8]
    akn = parse_to_akn(source, country="xu", doctype="act", number=suffix, date="2006-07-01")
    version_id = await save_document(
        session, parse_akn(akn), jurisdiction_code="xu", law_title=f"Parity {suffix}", akn_xml=akn
    )
    try:
        await index_version_annex_rows(session, version_id, akn)
        await index_version_goods_codes(session, version_id, akn)
        await session.commit()
        ingested = await _snapshot(session, version_id)

        seen: dict[str, str] = {}
        monkeypatch.setattr(
            repository,
            "document_to_rows",
            _on_thread(repository.document_to_rows, "document_to_rows", seen),
        )
        monkeypatch.setattr(
            annex_rows, "resolve_tables", _on_thread(resolve_tables, "resolve_tables", seen)
        )
        monkeypatch.setattr(
            goods_codes,
            "extract_goods_codes",
            _on_thread(extract_goods_codes, "extract_goods_codes", seen),
        )
        await rederive_version_rows(session, version_id, akn)
        await session.commit()
        rederived = await _snapshot(session, version_id)

        assert rederived == ingested
        counts = {table: len(rows) for table, rows in rederived.items()}
        assert counts["sections"] and counts["provisions"] and counts["annex_rows"], counts
        assert bool(counts["goods_code_references"]) is expect_codes, counts
        # The rows as the passes produce them inline, on the loop, before the move.
        language = (
            await session.execute(
                text("SELECT language FROM versions WHERE id = :v"), {"v": version_id}
            )
        ).scalar_one()
        stored_rows = [json.loads(row) for row in rederived["annex_rows"]]
        assert sorted((r["akn_eid"], r["search_tokens"]) for r in stored_rows) == sorted(
            (row.akn_eid, tokenise_to_text(row.resolved_text, language))
            for row in resolve_tables(akn)
        )
        stored_codes = [json.loads(row) for row in rederived["goods_code_references"]]
        assert sorted((c["akn_eid"], c["code"]) for c in stored_codes) == sorted(
            (code.eid, code.code) for code in extract_goods_codes(akn)
        )
        assert set(seen) == {"document_to_rows", "resolve_tables", "extract_goods_codes"}, seen
        assert all(name.startswith("codify-cpu") for name in seen.values()), seen
    finally:
        await session.rollback()
        await session.execute(
            text("DELETE FROM laws WHERE id = (SELECT law_id FROM versions WHERE id = :v)"),
            {"v": version_id},
        )
        await session.commit()
