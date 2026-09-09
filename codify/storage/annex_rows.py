"""Persist and read resolved table rows. Replacement, not merge: a row's
meaning comes from its neighbours."""

from __future__ import annotations

import uuid

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from codify.search import TOKENISER_VERSION, tokenise_to_text
from codify.storage.models import AnnexRow, Version
from codify.tables.resolve import resolve_tables

logger = structlog.get_logger()

__all__ = [
    "TOKENISER_VERSION",
    "TableRowMatch",
    "count_annex_rows",
    "count_stale_annex_row_tokens",
    "index_version_annex_rows",
    "retokenise_version_annex_rows",
    "rows_for_table",
    "search_annex_rows",
]


async def index_version_annex_rows(
    session: AsyncSession, version_id: uuid.UUID, akn_xml: str | None = None
) -> tuple[int, str | None]:
    """Rewrite one version's table rows, as (count, skipped reason).

    Idempotent by replacement, so a retry does not double-count.
    """
    language = "eng"
    if akn_xml is None:
        row = (
            await session.execute(
                select(Version.akn_xml, Version.language).where(Version.id == version_id)
            )
        ).first()
        if row is None or not row[0]:
            return 0, "no akn"
        akn_xml, language = row[0], row[1] or "eng"
    else:
        found = (
            await session.execute(select(Version.language).where(Version.id == version_id))
        ).scalar_one_or_none()
        language = found or "eng"

    # Resolve before deleting. The caller commits regardless, so a failure
    # after the delete would leave a version that had rows yesterday with none.
    try:
        resolved = resolve_tables(akn_xml)
    except Exception as exc:  # noqa: BLE001, the prior rows are worth more than this run
        logger.warning(
            "annex_rows_resolve_failed",
            version_id=str(version_id),
            error=f"{type(exc).__name__}: {str(exc)[:160]}",
        )
        return 0, f"{type(exc).__name__}: {str(exc)[:120]}"
    await session.execute(delete(AnnexRow).where(AnnexRow.version_id == version_id))

    for item in resolved:
        session.add(
            AnnexRow(
                version_id=version_id,
                akn_eid=item.akn_eid,
                table_eid=item.table_eid,
                row_index=item.row_index,
                cells=[{"column": c.column, "label": c.label, "text": c.text} for c in item.cells],
                lineage=list(item.lineage),
                mechanisms=sorted(item.mechanisms),
                resolved_text=item.resolved_text,
                search_tokens=tokenise_to_text(item.resolved_text, language),
                search_pipeline_version=TOKENISER_VERSION,
            )
        )
    # A row with no mechanism carries no context beyond its frame. That is
    # correct for a flat listing and a defect for a table whose structure was
    # missed, and the two are indistinguishable without the count.
    contextless = sum(1 for item in resolved if not item.mechanisms)
    if contextless:
        logger.info(
            "annex_rows_indexed",
            version_id=str(version_id),
            rows=len(resolved),
            contextless=contextless,
        )
    return len(resolved), None


async def rows_for_table(
    session: AsyncSession,
    version_id: uuid.UUID,
    table_eid: str,
    *,
    offset: int = 0,
    limit: int = 200,
) -> list[AnnexRow]:
    """One table's rows in document order. Windowed: the largest table in the
    corpus runs to six figures, and a reader asks for a page of it."""
    return list(
        (
            await session.execute(
                select(AnnexRow)
                .where(AnnexRow.version_id == version_id, AnnexRow.table_eid == table_eid)
                .order_by(AnnexRow.row_index)
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def count_annex_rows(
    session: AsyncSession, version_id: uuid.UUID, table_eid: str | None = None
) -> int:
    """How many rows a version holds, or one of its tables."""
    statement = select(func.count(AnnexRow.id)).where(AnnexRow.version_id == version_id)
    if table_eid is not None:
        statement = statement.where(AnnexRow.table_eid == table_eid)
    return int((await session.execute(statement)).scalar_one())


class TableRowMatch(BaseModel):
    """One row, with the law it belongs to and where to open it.

    `cells` keeps each cell's column label, which is what makes a row readable
    on its own: a cell reading "Article 12" means nothing until it sits under
    the column naming the act it came from.
    """

    version_id: uuid.UUID
    akn_eid: str
    table_eid: str
    row_index: int
    cells: list[dict[str, object]] = Field(default_factory=list)
    lineage: list[str] = Field(default_factory=list)
    mechanisms: list[str] = Field(default_factory=list)
    resolved_text: str
    law_id: uuid.UUID
    law_title: str | None = None
    frbr_work_uri: str
    jurisdiction_code: str


# Rank in a bounded CTE, then fetch the page's law metadata. Ranking and joining
# in one statement read 100,000 rows to return 20; bounding first reads 20
# (120,244 rows, 1,238ms against 13ms). The score is a column, not a window
# function, which would see every row before the limit applied.
#
# Ties are broken after the page is cut, so a tie group on a boundary can shift
# between requests. Breaking them inside the CTE costs 1.56M buffer hits against
# 713; keyset pagination on `(score, id)` is the fix if paging goes deep.
#
# Two statements, because a scoped-or-not disjunction cannot use the version index.
_ROW_RANK_HEAD = """
    WITH ranked AS (
      SELECT id, search_tokens <@> to_bm25query(:query_tokens, 'annex_rows_bm25_idx') AS score
      FROM annex_rows
      WHERE search_tsv @@ to_tsquery('simple', :query_match)
"""
_ROW_RANK_TAIL = """
      ORDER BY score
      LIMIT :limit OFFSET :offset
    )
    SELECT a.version_id, a.akn_eid, a.table_eid, a.row_index, a.cells,
           a.lineage, a.mechanisms, a.resolved_text,
           l.id AS law_id, l.title AS law_title, l.frbr_work_uri, j.code AS jurisdiction_code
    FROM ranked r
    JOIN annex_rows a ON a.id = r.id
    JOIN versions v ON v.id = a.version_id
    JOIN laws l ON l.id = v.law_id
    JOIN jurisdictions j ON j.id = l.jurisdiction_id
    ORDER BY r.score, a.version_id, a.table_eid, a.row_index
"""
_ROW_SEARCH_SQL = text(_ROW_RANK_HEAD + _ROW_RANK_TAIL)
_ROW_SEARCH_SCOPED_SQL = text(
    _ROW_RANK_HEAD + "        AND version_id = ANY(:version_ids)\n" + _ROW_RANK_TAIL
).bindparams(bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))))


async def search_annex_rows(
    session: AsyncSession,
    query_tokens: str,
    *,
    version_ids: list[uuid.UUID] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[TableRowMatch]:
    """Rows ranked by BM25 over their tokens, with the law they belong to.

    `query_tokens` is already tokenised, by the same tokeniser that wrote the
    rows; an untokenised query would match on stems the index does not hold.
    """
    tokens = query_tokens.split()
    if not tokens:
        return []
    # An empty list is a caller with nothing in scope, not a caller asking for
    # everything, so scope is a flag rather than the list being falsy.
    params: dict[str, object] = {
        "query_tokens": " ".join(tokens),
        "query_match": " | ".join(tokens),
        "limit": limit,
        "offset": offset,
    }
    if version_ids is None:
        statement = _ROW_SEARCH_SQL
    else:
        statement = _ROW_SEARCH_SCOPED_SQL
        params["version_ids"] = version_ids
    rows = (await session.execute(statement, params)).mappings()
    return [TableRowMatch.model_validate(dict(row)) for row in rows]


async def count_stale_annex_row_tokens(
    session: AsyncSession, version_id: uuid.UUID | None = None
) -> tuple[int, int]:
    """`(rows the current tokeniser has not written, total)`.

    Reported apart from the provisions count rather than folded into it: this
    migration leaves every existing row unstamped, and a readiness probe that
    counts provisions alone would say zero stale while 1.66M rows wait.
    """
    row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE search_pipeline_version "
                "IS DISTINCT FROM :current) AS stale, count(*) AS total FROM annex_rows "
                "WHERE CAST(:v AS uuid) IS NULL OR version_id = :v"
            ),
            {"current": TOKENISER_VERSION, "v": version_id},
        )
    ).one()
    return int(row.stale), int(row.total)


async def retokenise_version_annex_rows(session: AsyncSession, version_id: uuid.UUID) -> int:
    """Rewrite the tokens of one version's rows, returning the number changed.

    Idempotent: rows already at the current tokeniser are skipped, so a retried
    child does no work the first attempt did. The row's text is stored, so this
    re-tokenises what is there rather than re-resolving the tables.
    """
    language = (
        await session.execute(select(Version.language).where(Version.id == version_id))
    ).scalar_one_or_none()
    if language is None:
        logger.warning("retokenise_annex_rows_missing_version", version_id=str(version_id))
        return 0
    # Two columns, not the entity: one table runs to six figures and `cells`,
    # `lineage` and `mechanisms` are JSON this pass never reads.
    rows = (
        await session.execute(
            select(AnnexRow.id, AnnexRow.resolved_text).where(
                AnnexRow.version_id == version_id,
                AnnexRow.search_pipeline_version.is_distinct_from(TOKENISER_VERSION),
            )
        )
    ).all()
    if not rows:
        return 0

    # One executemany against the Core table, matching the provisions pass: a
    # whole-corpus backfill is over a million rows.
    table = AnnexRow.__table__
    statement = (
        update(table)
        .where(table.c.id == bindparam("target_id"))
        .values(
            search_tokens=bindparam("tokens"),
            search_pipeline_version=bindparam("pipeline_version"),
        )
    )
    await session.execute(
        statement,
        [
            {
                "target_id": row_id,
                "tokens": tokenise_to_text(row_text, language),
                "pipeline_version": TOKENISER_VERSION,
            }
            for row_id, row_text in rows
        ],
    )
    return len(rows)
