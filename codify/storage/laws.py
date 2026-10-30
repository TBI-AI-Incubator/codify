"""Laws CRUD + LawSummary listing."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Float, and_, func, literal_column, or_, select, text, true, union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from codify.storage.citations import parse_citation
from codify.storage.models import Jurisdiction, Law, Version
from codify.storage.title_tokens import title_query_tokens


class LawSummary(BaseModel):
    """Listing-friendly projection of a Law row. `lens_coverage` is opt-in: the
    corpus-wide `/laws` route always populates it, the per-jurisdiction listing
    only on `?include=coverage`, and callers that skip it read `None`.
    """

    id: uuid.UUID
    jurisdiction_code: str
    title: str
    # Short name for display; `title` is the fallback.
    short_title: str | None = None
    doctype: str
    status: str  # 'draft' | 'enacted', matches Law.status (migration 0060)
    year: int | None
    number: str | None
    frbr_work_uri: str
    latest_expression_date: date | None
    # When the corpus last took a version of this law in. Answers "is it
    # ingested?" without an upload.
    latest_ingested_at: datetime | None = None
    title_translations: dict[str, str] = {}
    lens_coverage: dict[str, dict[str, Any]] | None = None


SORTS = ("relevance", "title", "year_desc", "year_asc", "ingested")

# How many BM25-ordered rows the rank re-sort sees. Wide enough that the law a
# reader named is inside it, bounded so the index keeps doing the ordering.
RERANK_WINDOW = 200

# One rank step in BM25 units. Measured floor: 0.35 lifts a `qanun` above a
# `qarar`, 0.2 does not, and recall and MRR are unchanged up to 1.5. No ceiling
# was found, so this is the smaller intervention rather than the better one.
RANK_WEIGHT = 0.35


def _title_words(tsquery: str) -> Any:
    return text("laws.title_tsv @@ to_tsquery('simple', :tsquery)").bindparams(tsquery=tsquery)


def _cites(citation: tuple[str, int]) -> Any:
    number, year = citation
    # Stored numbers are sometimes zero-padded (`01`, `04-213-11` in the corpus
    # today) and a reader does not type the padding. Compared with the padding
    # stripped from both sides, since neither side owns the canonical form.
    return and_(func.ltrim(Law.number, "0") == number, Law.year == year)


def _bm25(tokens: str) -> Any:
    """`<@>` is the negative BM25 score, so lower is better."""
    # The index name stays a literal, as the ordered index scan needs to see it.
    query = func.to_bm25query(tokens, literal_column("'laws_title_bm25_idx'"))
    return Law.title_tokens.op("<@>", return_type=Float)(query)


def _title_match(tsquery: str, citation: tuple[str, int] | None) -> Any:
    """Membership predicate for a title query: the words, or the citation. ORed
    rather than chosen, because a numeric citation is often in the stored title
    too and a reader typing one wants both routes to reach the same law.
    """
    match = _title_words(tsquery)
    return match if citation is None else or_(match, _cites(citation))


def _version_dates() -> Any:
    """The latest expression and ingest of the outer query's law, as a LATERAL."""
    return (
        select(
            func.max(Version.expression_date).label("latest"),
            func.max(Version.ingested_at).label("ingested"),
        )
        .where(Version.law_id == Law.id)
        .lateral("dates")
    )


def _relevance_candidates(
    tsquery: str,
    citation: tuple[str, int] | None,
    tokens: str,
    filters: list[Any],
    window: int,
) -> Any:
    """The laws a relevance listing ranks, each with its BM25 score.

    Two arms, unioned and materialised before anything orders them. The title arm
    takes `window` rows in the listing's own total order, so a tie at the cut is
    the same tie on every page; an incremental sort over the BM25 index order
    reads on only through the tied group, not the whole match. The citation arm
    stands apart because an ordered BM25 scan yields only titles sharing a token
    with the query: ORed into one predicate, a law found by its number and year
    alone was lost whenever the planner chose it.
    """

    def ids(predicate: Any) -> Select[Any]:
        return (
            select(Law.id.label("id"))
            .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
            .where(predicate, *filters)
        )

    def scored(rows: Any) -> Select[Any]:
        # Scored once per surviving row, not for every row the sort reads through.
        return select(Law.id.label("id"), _bm25(tokens).label("score")).join(
            rows, rows.c.id == Law.id
        )

    titled = scored(
        ids(_title_words(tsquery))
        .order_by(_bm25(tokens), Law.frbr_work_uri, Law.id)
        .limit(window)
        .subquery("titled")
    )
    arms = (
        titled
        if citation is None
        else union(titled, scored(ids(_cites(citation)).subquery("cited")))
    )
    return arms.cte("candidates").prefix_with("MATERIALIZED")


def _law_order(sort: str | None, ingested: Any) -> list[Any]:
    """ORDER BY for a law listing without a relevance-ranked query, falling back
    to browse order. Every sort ends on `frbr_work_uri, id` so paging is
    deterministic."""
    tail = [Law.frbr_work_uri.asc(), Law.id.asc()]
    if sort in (None, "relevance"):
        return [Law.year.desc().nulls_last(), *tail]
    if sort == "title":
        # The row renders `short_title or title`, so ordering on `title` alone
        # puts anything with a short name visibly out of sequence.
        return [func.coalesce(Law.short_title, Law.title).asc(), *tail]
    if sort == "year_asc":
        return [Law.year.asc().nulls_last(), *tail]
    if sort == "ingested":
        return [ingested.desc().nulls_last(), *tail]
    return [Law.year.desc().nulls_last(), *tail]


async def list_laws(
    session: AsyncSession,
    *,
    jurisdictions: list[str] | None = None,
    doctype: str | None = None,
    status: str | None = None,
    year: int | None = None,
    limit: int = 50,
    cursor: uuid.UUID | None = None,
    q: str | None = None,
    sort: str | None = None,
    offset: int = 0,
    doctype_ranks: dict[tuple[str, str], int] | None = None,
) -> tuple[list[LawSummary], uuid.UUID | None]:
    """Cursor-paginated law listing, ordered year DESC NULLS LAST then
    `frbr_work_uri, id` so corpus-wide and single-country calls page alike. The
    cursor is the last emitted row's id, or None on the final page. A keyset
    cursor encodes the browse order, so a `q` or `sort` call returns None and
    pages by offset instead.
    """
    # A title query turns this listing into a finder. Membership comes from the
    # generated tsvector through its GIN index, order from BM25 over the same
    # tokens, matching how the provision arm splits the two jobs.
    matched = False
    bm25_tokens = tsquery = ""
    if q and q.strip():
        bm25_tokens, tsquery = await title_query_tokens(session, q, jurisdictions)
        if not bm25_tokens:
            # Nothing lexical in the query: no title can match, and returning the
            # unfiltered corpus would read as "your search matched everything".
            return [], None
        matched = True

    filters: list[Any] = []
    if jurisdictions:
        filters.append(Jurisdiction.code.in_(jurisdictions))
    if doctype:
        filters.append(Law.doctype == doctype)
    if status:
        filters.append(Law.status == status)
    if year is not None:
        filters.append(Law.year == year)
    if cursor:
        cur_row = (
            await session.execute(
                select(Law.year, Law.frbr_work_uri, Law.id).where(Law.id == cursor)
            )
        ).first()
        if cur_row is None:
            return [], None
        cur_year, cur_uri, cur_id = cur_row
        if cur_year is None:
            filters.append(
                Law.year.is_(None)
                & (
                    (Law.frbr_work_uri > cur_uri)
                    | ((Law.frbr_work_uri == cur_uri) & (Law.id > cur_id))
                )
            )
        else:
            filters.append(
                or_(
                    Law.year.is_(None),
                    Law.year < cur_year,
                    (Law.year == cur_year) & (Law.frbr_work_uri > cur_uri),
                    (Law.year == cur_year) & (Law.frbr_work_uri == cur_uri) & (Law.id > cur_id),
                )
            )

    # A rank-weighted order cannot use the BM25 index: 218ms against 38ms on
    # 50k laws. The re-rank runs over a bounded window, and a page past it
    # falls back whole to relevance order, so nothing is shown twice.
    relevance = matched and sort in (None, "relevance")
    rerank = bool(doctype_ranks) and relevance and offset + limit <= RERANK_WINDOW
    # The page is chosen first and dated after, so only its laws' versions are
    # read, except under the ingested sort, which orders by those dates.
    page_q = select(Law.id.label("id")).join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
    if relevance:
        # The re-rank window is fixed, never widened by the offset: a larger set
        # re-sorts, and a law admitted at 205 could push a shown row back into view.
        window = RERANK_WINDOW if rerank else offset + limit + 1
        candidates = _relevance_candidates(
            tsquery, parse_citation(q or ""), bm25_tokens, filters, window
        )
        page_q = (
            page_q.join(candidates, candidates.c.id == Law.id)
            .add_columns(candidates.c.score)
            .order_by(candidates.c.score, Law.frbr_work_uri, Law.id)
        )
    else:
        page_q = page_q.where(*filters)
        if matched:
            page_q = page_q.where(_title_match(tsquery, parse_citation(q or "")))
        # One aggregate over every version, measured faster than a LATERAL per
        # law once the sort needs all of them.
        every = (
            select(Version.law_id, func.max(Version.ingested_at).label("ingested"))
            .group_by(Version.law_id)
            .subquery()
        )
        if sort == "ingested":
            page_q = page_q.outerjoin(every, every.c.law_id == Law.id)
        page_q = page_q.order_by(*_law_order(sort, every.c.ingested))
    page_q = page_q.limit(RERANK_WINDOW if rerank else limit + 1)
    if offset and not rerank:
        page_q = page_q.offset(offset)
    page = page_q.subquery("page")

    dates = _version_dates()
    stmt = (
        select(
            Law.id,
            Jurisdiction.code,
            Law.title,
            Law.short_title,
            Law.doctype,
            Law.status,
            Law.year,
            Law.number,
            Law.frbr_work_uri,
            dates.c.latest,
            Law.title_translations,
            dates.c.ingested,
        )
        .select_from(page)
        .join(Law, Law.id == page.c.id)
        .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
        .outerjoin(dates, true())
    )
    # The same total order again, since a join does not keep the page's.
    if relevance:
        stmt = stmt.order_by(page.c.score, Law.frbr_work_uri, Law.id)
        if rerank:
            stmt = stmt.add_columns(page.c.score)
    else:
        stmt = stmt.order_by(*_law_order(sort, dates.c.ingested))
    rows = (await session.execute(stmt)).all()

    if rerank:
        assert doctype_ranks is not None
        # Stable, so equal ranks keep BM25's order. Keyed on jurisdiction too:
        # one doctype name means different instruments in different countries.
        rows = sorted(
            rows,
            key=lambda r: r[-1] - doctype_ranks.get((r[1], r[4]), 0) * RANK_WEIGHT,
        )[offset:]

    page_rows = rows[:limit]
    summaries = [
        LawSummary(
            id=r[0],
            jurisdiction_code=r[1],
            title=r[2],
            short_title=r[3],
            doctype=r[4],
            status=r[5],
            year=r[6],
            number=r[7],
            frbr_work_uri=r[8],
            latest_expression_date=r[9],
            title_translations=r[10] or {},
            latest_ingested_at=r[11],
        )
        for r in page_rows
    ]
    # Keyset cursors encode the browse order, so a relevance- or title-ordered
    # page cannot produce one; those callers page by offset instead.
    has_more = len(rows) > limit
    if q or sort:
        return summaries, None
    next_cursor = page_rows[-1][0] if has_more else None
    return summaries, next_cursor


async def count_laws(
    session: AsyncSession,
    *,
    jurisdictions: list[str] | None = None,
    doctype: str | None = None,
    status: str | None = None,
    year: int | None = None,
    q: str | None = None,
) -> int:
    """How many laws match, corpus-wide rather than page-wide.

    The tab badge and the pager both need a true total; deriving one from a page
    would report the page size as the corpus size.
    """
    stmt = select(func.count(Law.id)).join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
    if jurisdictions:
        stmt = stmt.where(Jurisdiction.code.in_(jurisdictions))
    if doctype:
        stmt = stmt.where(Law.doctype == doctype)
    if status:
        stmt = stmt.where(Law.status == status)
    if year is not None:
        stmt = stmt.where(Law.year == year)
    if q and q.strip():
        _, tsquery = await title_query_tokens(session, q, jurisdictions)
        if not tsquery:
            return 0
        # The same predicate as the listing, so the total cannot disagree with
        # the rows it is counting.
        stmt = stmt.where(_title_match(tsquery, parse_citation(q)))
    return int((await session.execute(stmt)).scalar_one())


async def list_laws_facets(
    session: AsyncSession,
    *,
    jurisdiction_code: str | None = None,
    jurisdictions: list[str] | None = None,
    doctype: str | None = None,
    status: str | None = None,
    year: int | None = None,
    q: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Distinct-value and row-count aggregates for the doctype and year facets.

    Each facet is computed with every filter except the one being counted, so a
    selected chip stays visible with its own count. `q` is never skipped: a count
    describing the whole corpus beside a title-matched list is the lie these
    counts exist to avoid. Rows with a NULL year are omitted.
    """
    title_match: str | None = None
    if q and q.strip():
        _, title_match = await title_query_tokens(session, q, jurisdictions)
        if not title_match:
            return {"doctype": [], "year": []}

    citation = parse_citation(q) if q else None

    def _apply(stmt: Select[Any], *, skip: str) -> Select[Any]:
        if title_match is not None:
            # The same predicate the listing and the count use. A citation found
            # only by number and year would otherwise return a row and a total
            # beside two empty facet lists.
            stmt = stmt.where(_title_match(title_match, citation))
        if jurisdiction_code is not None:
            stmt = stmt.where(Jurisdiction.code == jurisdiction_code)
        if jurisdictions:
            stmt = stmt.where(Jurisdiction.code.in_(jurisdictions))
        if doctype and skip != "doctype":
            stmt = stmt.where(Law.doctype == doctype)
        if status and skip != "status":
            stmt = stmt.where(Law.status == status)
        if year is not None and skip != "year":
            stmt = stmt.where(Law.year == year)
        return stmt

    doctype_stmt = _apply(
        select(Law.doctype, func.count(Law.id))
        .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
        .group_by(Law.doctype)
        .order_by(func.count(Law.id).desc(), Law.doctype.asc()),
        skip="doctype",
    )
    year_stmt = _apply(
        select(Law.year, func.count(Law.id))
        .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
        .where(Law.year.is_not(None))
        .group_by(Law.year)
        .order_by(Law.year.desc()),
        skip="year",
    )

    doctype_rows = (await session.execute(doctype_stmt)).all()
    year_rows = (await session.execute(year_stmt)).all()

    return {
        "doctype": [{"value": v, "count": c} for v, c in doctype_rows if v],
        "year": [{"value": int(v), "count": c} for v, c in year_rows],
    }


async def get_law_with_jurisdiction(
    session: AsyncSession, law_id: uuid.UUID
) -> tuple[Law, str] | None:
    """Return (law, jurisdiction_code) or None if no such law."""
    row = (
        await session.execute(
            select(Law, Jurisdiction.code)
            .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
            .where(Law.id == law_id)
        )
    ).first()
    if row is None:
        return None
    law, code = row
    return law, code


async def get_law_by_frbr(session: AsyncSession, frbr_work_uri: str) -> tuple[Law, str] | None:
    """FRBR-keyed lookup, same shape as `get_law_with_jurisdiction`. Work URIs are
    unique in the corpus, so an agent addressing legislation by its canonical
    identifier need not page `list_laws` for a UUID first.
    """
    row = (
        await session.execute(
            select(Law, Jurisdiction.code)
            .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
            .where(Law.frbr_work_uri == frbr_work_uri)
        )
    ).first()
    if row is None:
        return None
    law, code = row
    return law, code


async def law_exists(session: AsyncSession, law_id: uuid.UUID) -> bool:
    return (
        await session.execute(select(Law.id).where(Law.id == law_id))
    ).scalar_one_or_none() is not None


__all__ = [
    "LawSummary",
    "get_law_by_frbr",
    "get_law_with_jurisdiction",
    "law_exists",
    "list_laws",
    "list_laws_facets",
]
