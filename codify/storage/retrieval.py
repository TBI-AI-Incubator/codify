"""Hybrid dense + BM25 retrieval over `provisions`, RRF combined server-side.

The lexical arm ranks with BM25 over `provisions.search_tokens` rather than
`ts_rank`, which has no inverse document frequency: measured on the corpus, a
term in 2,326 provisions and one in 21 scored identically. Query and text go
through the same tokeniser; applied on one side only it returns nothing,
silently.
"""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import HALFVEC
from pydantic import BaseModel
from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from codify.search import tokenise
from codify.storage.lexicon import expand_terms
from codify.storage.models import Jurisdiction, Law, Provision, Version

# Measured, not conventional. Sweeping lexical:dense weights 0.25-4.0 against
# rrf_k 10-100 at every pool production fuses found equal weight best everywhere:
# each off-1.0 weight buys one language at another's expense. k=40 did beat 60,
# until BM25's length normalisation was fixed in migration 0130, after which the
# gain fell to +0.0004 ndcg@10. It was recovering what a badly-normalised lexical
# arm had lost, so 60 stands. Equal weight is the SQL's `SUM(1.0 / ...)` with no
# per-arm term: a weight that must be 1.0 is not a parameter.
RRF_K = 60

# `<@>` returns the NEGATIVE BM25 score: Postgres scans an ordering operator
# ascending only, so lower is better and ORDER BY stays plain. It has no boolean
# form and scores a non-matching row 0 rather than dropping it, so `search_tsv`
# decides membership and BM25 orders what survives. The `version_id` predicate
# pre-filters through `provisions_version_id_idx`, `pg_textsearch`'s documented
# best case. An empty `search_tsv` is a row the tokeniser has not reached, which
# backfill-search-tokens closes.
#
# HALFVEC binding relies on pgvector's TEXT-mode wire format, where the
# `bind_processor` serialises list[float] to '[x1,x2,...]'. Do not call
# `pgvector.asyncpg.register_vector` on this engine without adapting the query:
# the binary codec would route the text string into the binary encoder.
#
# `fused.id` is the secondary sort key. Equal arm weights make the score a bare
# `SUM(1.0 / (:rrf_k + rn))`, so two ids each seen by one arm at the same rank
# score identically; without it Postgres orders those ties any way it likes and
# the ranking is not reproducible offline.
# The dense arm orders and limits on `provision_embeddings` itself, which is
# partitioned by jurisdiction with one HNSW each: the jurisdiction predicate
# prunes to the scope's partitions and the version predicate is applied inside
# the ordered index scan. A window over the join instead sorts every embedding
# in scope exactly, which measured 60 s cold on 67k provisions. The window
# orders by `distance + 0`: the relaxed scan is approximately ordered and the
# planner would otherwise take its order for the window's. The anti-join
# excludes fallback vectors wherever a preferred vector exists.
_HYBRID_SQL = text(
    """
    WITH fts AS (
      SELECT id, ROW_NUMBER() OVER (
               ORDER BY search_tokens <@> to_bm25query(:query_tokens, 'provisions_bm25_idx')
             ) AS rn
      FROM provisions
      WHERE version_id = ANY(:version_ids)
        AND search_tsv @@ to_tsquery('simple', :query_match)
        AND (CAST(:akn_type AS text) IS NULL OR akn_type = CAST(:akn_type AS text))
        AND (normative OR CAST(:include_non_normative AS boolean))
        AND excluded_from_pool IS NOT TRUE
      LIMIT :pool
    ),
    vec AS (
      SELECT id, ROW_NUMBER() OVER (ORDER BY distance + 0) AS rn
      FROM (
        SELECT e.provision_id AS id, e.embedding <=> :query_vec AS distance
        FROM provision_embeddings e
        JOIN provisions p ON p.id = e.provision_id
        WHERE e.jurisdiction_id = ANY(:jurisdiction_ids)
          AND e.version_id = ANY(:version_ids)
          AND (
            e.model_id = :model_id
            OR (
              e.model_id = CAST(:fallback_model_id AS text)
              AND NOT EXISTS (
                SELECT 1 FROM provision_embeddings preferred
                WHERE preferred.provision_id = e.provision_id
                  AND preferred.jurisdiction_id = e.jurisdiction_id
                  AND preferred.model_id = :model_id
              )
            )
          )
          AND (CAST(:akn_type AS text) IS NULL OR p.akn_type = CAST(:akn_type AS text))
          AND (p.normative OR CAST(:include_non_normative AS boolean))
          AND p.excluded_from_pool IS NOT TRUE
        ORDER BY e.embedding <=> :query_vec
        LIMIT :pool
      ) nearest
    ),
    fused AS (
      SELECT id, SUM(1.0 / (:rrf_k + rn)) AS rrf_score
      FROM (SELECT id, rn FROM fts UNION ALL SELECT id, rn FROM vec) t
      GROUP BY id
    )
    SELECT p.id, p.akn_eid, p.text, fused.rrf_score
    FROM fused
    JOIN provisions p ON p.id = fused.id
    ORDER BY fused.rrf_score DESC, fused.id
    LIMIT :k
    """
).bindparams(
    bindparam("query_vec", type_=HALFVEC(768)),
    bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("jurisdiction_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
)


_LANGUAGES_SQL = text(
    "SELECT DISTINCT language FROM versions WHERE id = ANY(:version_ids)"
).bindparams(bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))))


async def query_tokens_for(
    session: AsyncSession, query_text: str, version_ids: list[uuid.UUID]
) -> str:
    """Tokenise `query_text` under every language present in the searched set. A
    mixed set has no single right language and guessing would stem the query
    wrongly. Tokens from different scripts cannot collide, and a term absent from
    the corpus contributes nothing to BM25, so the union costs less than a guess.
    """
    # `= ANY(array)` rather than `IN (...)`, matching `_HYBRID_SQL` above: a
    # global search passes one version per law across every jurisdiction, and
    # `.in_()` renders a bind parameter each, against a 32767 cap.
    languages = (
        (await session.execute(_LANGUAGES_SQL, {"version_ids": version_ids})).scalars().all()
    )
    seen: set[str] = set()
    tokens: list[str] = []
    for language in languages or ["eng"]:
        for token in tokenise(query_text, language):
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    # Widened against the corpus's own vocabulary, which is what reaches a
    # clitic form, an OCR corruption or a typo. Stemming cannot: the stemmers
    # are per-language and three of the corpus's languages have none.
    return " ".join(await expand_terms(session, tokens))


# The relaxed scan may hand back its candidates a little out of order, so the
# limited set is sorted once more outside it; `+ 0` so the planner does not
# take the scan's claimed order for the sort's.
_DENSE_ONLY_SQL = text(
    """
    SELECT id, akn_eid, text, 0.0 AS rrf_score
    FROM (
    SELECT p.id, p.akn_eid, p.text, e.embedding <=> :query_vec AS distance
    FROM provision_embeddings e
    JOIN provisions p ON p.id = e.provision_id
    WHERE e.jurisdiction_id = ANY(:jurisdiction_ids)
      AND e.version_id = ANY(:version_ids)
      AND (
        e.model_id = :model_id
        OR (
          e.model_id = CAST(:fallback_model_id AS text)
          AND NOT EXISTS (
            SELECT 1 FROM provision_embeddings preferred
            WHERE preferred.provision_id = e.provision_id
              AND preferred.jurisdiction_id = e.jurisdiction_id
              AND preferred.model_id = :model_id
          )
        )
      )
      AND (CAST(:akn_type AS text) IS NULL OR p.akn_type = CAST(:akn_type AS text))
      AND (p.normative OR CAST(:include_non_normative AS boolean))
      AND p.excluded_from_pool IS NOT TRUE
    ORDER BY e.embedding <=> :query_vec
    LIMIT :k
    ) nearest
    ORDER BY distance + 0, id
    """
).bindparams(
    bindparam("query_vec", type_=HALFVEC(768)),
    bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("jurisdiction_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
)

_JURISDICTIONS_SQL = text(
    "SELECT DISTINCT l.jurisdiction_id FROM versions v JOIN laws l ON l.id = v.law_id "
    "WHERE v.id = ANY(:version_ids)"
).bindparams(bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))))


async def scope_jurisdictions(
    session: AsyncSession, version_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """The partitions a version set lives in, so the planner prunes to them."""
    rows = await session.execute(_JURISDICTIONS_SQL, {"version_ids": version_ids})
    return [row[0] for row in rows.all()]


async def _dense_only(
    session: AsyncSession,
    query_vec: list[float],
    version_ids: list[uuid.UUID],
    jurisdiction_ids: list[uuid.UUID],
    k: int,
    model_id: str,
    akn_type: str | None = None,
    include_non_normative: bool = False,
    fallback_model_id: str | None = None,
) -> list[tuple[uuid.UUID, str, str, float]]:
    """Dense arm alone, for a query the tokeniser finds nothing lexical in."""
    result = await session.execute(
        _DENSE_ONLY_SQL,
        {
            "query_vec": query_vec,
            "version_ids": version_ids,
            "jurisdiction_ids": jurisdiction_ids,
            "k": k,
            "model_id": model_id,
            "fallback_model_id": fallback_model_id,
            "akn_type": akn_type,
            "include_non_normative": include_non_normative,
        },
    )
    return [(row[0], row[1], row[2], float(row[3])) for row in result.all()]


async def hybrid_search(
    session: AsyncSession,
    *,
    query_vec: list[float],
    query_text: str,
    version_ids: list[uuid.UUID],
    k: int,
    candidate_pool: int,
    rrf_k: int,
    model_id: str,
    akn_type: str | None = None,
    include_non_normative: bool = False,
    fallback_model_id: str | None = None,
) -> list[tuple[uuid.UUID, str, str, float]]:
    """RRF-fused dense + BM25.

    `fallback_model_id` is read per provision where the preferred construction
    has not been written yet, so a re-embed in progress never empties the arm.

    Returns (provision_id, akn_eid, text, rrf_score).
    Normative only unless `include_non_normative`: an elucidation cannot be a
    legal basis, so it does not compete with law for a first page. On the flag
    alone, never on where a provision sits, because a normative annex is law."""
    if not version_ids:
        return []
    # Continue the approximate index scan past rows the version and model
    # filters discard, rather than stopping at pgvector's default 40 candidates.
    # Relaxed: strict order re-reads the graph per candidate (37 s against
    # 0.96 s measured) and the window above re-sorts the pool anyway.
    # Transaction-local: never change the next borrower's search settings.
    await session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
    jurisdiction_ids = await scope_jurisdictions(session, version_ids)
    tokens = (await query_tokens_for(session, query_text, version_ids)).split()
    if not tokens:
        # An empty tokenisation means the query carried no letters or digits.
        # Dense-only rather than the raw text, which scores every row zero:
        # an arm that looks like it fired and did nothing.
        return await _dense_only(
            session,
            query_vec,
            version_ids,
            jurisdiction_ids,
            k,
            model_id,
            akn_type,
            include_non_normative,
            fallback_model_id,
        )
    result = await session.execute(
        _HYBRID_SQL,
        {
            "query_vec": query_vec,
            "query_tokens": " ".join(tokens),
            # ORed: the tokens span every language in the searched set, so an
            # AND would demand one language's stem and another's at once.
            "query_match": " | ".join(tokens),
            "version_ids": version_ids,
            "jurisdiction_ids": jurisdiction_ids,
            "k": k,
            "pool": candidate_pool,
            "rrf_k": rrf_k,
            "model_id": model_id,
            "fallback_model_id": fallback_model_id,
            "akn_type": akn_type,
            "include_non_normative": include_non_normative,
        },
    )
    return [(row[0], row[1], row[2], float(row[3])) for row in result.all()]


class ProvisionContext(BaseModel):
    """Per-match metadata: law, jurisdiction, AKN type. Joined in one round trip."""

    provision_id: uuid.UUID
    law_id: uuid.UUID
    version_id: uuid.UUID
    law_title: str
    # Short display name where the law has one; `law_title` is the fallback.
    law_short_title: str | None = None
    jurisdiction_code: str
    frbr_work_uri: str
    doctype: str
    year: int | None
    akn_type: str
    # Whether this provision carries norm. A widened search returns law and the
    # apparatus explaining it together, and neither eId nor type separates them.
    normative: bool = True


async def enrich_matches(
    session: AsyncSession, provision_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ProvisionContext]:
    """Resolve law + jurisdiction metadata for a batch of provisions."""
    if not provision_ids:
        return {}
    rows = (
        await session.execute(
            select(
                Provision.id,
                Provision.akn_type,
                Provision.normative,
                Provision.version_id,
                Law.id,
                Law.title,
                Law.short_title,
                Law.frbr_work_uri,
                Law.doctype,
                Law.year,
                Jurisdiction.code,
            )
            .join(Version, Version.id == Provision.version_id)
            .join(Law, Law.id == Version.law_id)
            .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
            .where(Provision.id.in_(provision_ids))
        )
    ).all()
    return {
        pid: ProvisionContext(
            provision_id=pid,
            law_id=law_id,
            version_id=version_id,
            law_title=law_title,
            law_short_title=law_short_title,
            jurisdiction_code=code,
            frbr_work_uri=frbr,
            doctype=doctype,
            year=year,
            akn_type=akn_type,
            normative=normative,
        )
        for (
            pid,
            akn_type,
            normative,
            version_id,
            law_id,
            law_title,
            law_short_title,
            frbr,
            doctype,
            year,
            code,
        ) in rows
    }


# Metadata orders for the grouped search. Relevance is absent on purpose: it is
# the BM25 index-ordered path above, and routing it through here would give up
# the early termination that makes it fast.
_LAW_ORDERS = {
    "title": "min(coalesce(l.short_title, l.title)) ASC",
    "year_desc": "min(l.year) DESC NULLS LAST",
    "year_asc": "min(l.year) ASC NULLS LAST",
    # Across every version of the law, not the expression this search selected:
    # with a language chosen, an older translation would make a recently
    # updated law sort as old, and "recently added" would differ per tab.
    "ingested": (
        "max((SELECT max(av.ingested_at) FROM versions av WHERE av.law_id = v.law_id))"
        " DESC NULLS LAST"
    ),
}


async def matching_laws_ordered(
    session: AsyncSession,
    *,
    version_ids: list[uuid.UUID],
    query_match: str,
    sort: str,
    limit: int,
    offset: int,
    akn_type: str | None = None,
    include_non_normative: bool = False,
) -> tuple[list[tuple[uuid.UUID, int]], int]:
    """Laws with a matching provision ordered by law metadata, as
    `([(law_id, match_count)], total_laws)`.

    Reordering the relevance pool cannot serve a metadata sort: the pool is the
    top-ranked slice, so sorting it by year answers which of the 200 best matches is
    newest while presenting itself as an ordering of every match. Membership is the
    lexical population, so a provision only the dense arm surfaces is not counted;
    relevance order is the path that sees both arms.
    """
    if not version_ids or sort not in _LAW_ORDERS:
        return [], 0
    stmt = text(
        f"""
        SELECT v.law_id,
               count(*) AS match_count,
               count(*) OVER () AS total_laws
        FROM provisions p
        JOIN versions v ON v.id = p.version_id
        JOIN laws l ON l.id = v.law_id
        WHERE p.version_id = ANY(:version_ids)
          AND p.search_tsv @@ to_tsquery('simple', :query_match)
          AND (CAST(:akn_type AS text) IS NULL OR p.akn_type = CAST(:akn_type AS text))
          AND (p.normative OR CAST(:include_non_normative AS boolean))
        AND p.excluded_from_pool IS NOT TRUE
        GROUP BY v.law_id
        ORDER BY {_LAW_ORDERS[sort]}, v.law_id
        LIMIT :limit OFFSET :offset
        """  # noqa: S608
    ).bindparams(bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))))
    rows = (
        await session.execute(
            stmt,
            {
                "version_ids": version_ids,
                "query_match": query_match,
                "akn_type": akn_type,
                "include_non_normative": include_non_normative,
                "limit": limit,
                "offset": offset,
            },
        )
    ).all()
    if not rows:
        return [], 0
    return [(r[0], int(r[1])) for r in rows], int(rows[0][2])


# Capped here, not after the fetch: capping in Python still transfers every
# match, so one broad term against one large law reads thousands of full
# provision texts and throws them away.
_LAW_PROVISIONS_SQL = text(
    """
    SELECT law_id, id, akn_eid, text FROM (
        SELECT v.law_id, p.id, p.akn_eid, p.text,
               row_number() OVER (
                   PARTITION BY v.law_id
                   ORDER BY p.search_tokens
                            <@> to_bm25query(:query_tokens, 'provisions_bm25_idx')
               ) AS rn
        FROM provisions p
        JOIN versions v ON v.id = p.version_id
        WHERE p.version_id = ANY(:version_ids)
          AND v.law_id = ANY(:law_ids)
          AND p.search_tsv @@ to_tsquery('simple', :query_match)
          AND (CAST(:akn_type AS text) IS NULL OR p.akn_type = CAST(:akn_type AS text))
          AND (p.normative OR CAST(:include_non_normative AS boolean))
        AND p.excluded_from_pool IS NOT TRUE
    ) ranked
    WHERE rn <= :per_law
    ORDER BY law_id, rn
    """
).bindparams(
    bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("law_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
)


async def provisions_for_laws(
    session: AsyncSession,
    *,
    version_ids: list[uuid.UUID],
    law_ids: list[uuid.UUID],
    query_match: str,
    query_tokens: str,
    per_law: int,
    akn_type: str | None = None,
    include_non_normative: bool = False,
) -> dict[uuid.UUID, list[tuple[uuid.UUID, str, str]]]:
    """Best-matching provisions for each law on a page, BM25 first within a law.
    Bounded by `page size x per_law` because the cap is a window function inside
    the query; capping after the fetch would read every match for every law.
    """
    if not law_ids:
        return {}
    rows = (
        await session.execute(
            _LAW_PROVISIONS_SQL,
            {
                "version_ids": version_ids,
                "law_ids": law_ids,
                "query_match": query_match,
                "query_tokens": query_tokens,
                "akn_type": akn_type,
                "per_law": per_law,
                "include_non_normative": include_non_normative,
            },
        )
    ).all()
    out: dict[uuid.UUID, list[tuple[uuid.UUID, str, str]]] = {}
    for law_id, pid, eid, txt in rows:
        out.setdefault(law_id, []).append((pid, eid, txt))
    return out


__all__ = [
    "ProvisionContext",
    "enrich_matches",
    "hybrid_search",
    "matching_laws_ordered",
    "provisions_for_laws",
    "query_tokens_for",
]
