"""The search-term lexicon, and query expansion over it.

The corpus may spell a word in a form the reader did not type, or the reader
mistyped. Trigram similarity answers both without per-language configuration.
Expanded terms are OR-ed into the lexical arm's query, so a variant is scored on
its own IDF and a rare one can outrank a common exact term.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# `word_similarity` asks whether the query looks like PART of the term:
# similarity('وزير','الوزير') is 0.33, `strict_word_similarity` 0.33,
# `word_similarity` 0.60.
SIMILARITY_THRESHOLD = 0.5

# Per query term: enough for a word plus its clitics and OCR corruptions, small
# enough that a common short token cannot drag a hundred neighbours in. A term
# whose provisions were repaired away would hold a slot; the janitor's
# `prune_dead_terms` removes it nightly.
MAX_VARIANTS = 6

# 3, not 4: `وزير` stems to three characters while `الوزير` stems to four, so a
# floor of 4 skipped the query term and the clitic case never fired. 426,701 of
# 1,599,171 Arabic tokens are exactly three. 4 to 3 lifts Arabic recall@50
# 0.909 to 0.927 and costs nDCG@10 0.785 to 0.774.
MIN_TERM_LENGTH = 3

_EXPAND = text(
    """
    SELECT term FROM search_terms
    WHERE :token <% term AND term <> :token
    ORDER BY :token <<-> term, ndoc DESC
    LIMIT :limit
    """
)


# The lexicon expands a reader's misspelling to a word the corpus uses, so a
# term this long is not one. Well under the 2704-byte btree ceiling the key
# would otherwise hit, and counted in bytes because the corpus is not ASCII.
_MAX_TERM_BYTES = 128


async def expand_terms(session: AsyncSession, tokens: list[str]) -> list[str]:
    """Terms in the corpus that look like `tokens`, `tokens` themselves first. The
    originals always lead and are never dropped, so expansion can only widen the
    query and an empty lexicon degrades to unexpanded rather than to nothing.
    """
    if not tokens:
        return []
    expanded = list(tokens)
    seen = set(tokens)
    # `set_config`, not `SET LOCAL`: SET takes no bind parameter. `true` scopes
    # it to the transaction, so it cannot leak into the next query on this
    # connection.
    await session.execute(
        text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)").bindparams(
            t=str(SIMILARITY_THRESHOLD)
        )
    )
    for token in tokens:
        if len(token) < MIN_TERM_LENGTH:
            continue
        rows = await session.execute(_EXPAND, {"token": token, "limit": MAX_VARIANTS})
        for (variant,) in rows.all():
            if variant not in seen:
                seen.add(variant)
                expanded.append(variant)
    return expanded


async def record_terms_from_rows(session: AsyncSession, provisions: Sequence[Any]) -> int:
    """Add the terms carried by provision rows about to be persisted. Takes the rows
    rather than a version id because it runs inside the write that created them,
    where a query by version would see only what has been flushed.
    """
    terms: set[str] = set()
    for row in provisions:
        if row.search_tokens:
            terms.update(row.search_tokens.split())
    if not terms:
        return 0
    result = await session.execute(
        text(
            "INSERT INTO search_terms (term, ndoc) SELECT unnest(CAST(:terms AS text[])), 0"
            " ON CONFLICT (term) DO NOTHING"
        ),
        {"terms": sorted(t for t in terms if len(t.encode()) <= _MAX_TERM_BYTES)},
    )
    return result.rowcount or 0


async def record_terms(session: AsyncSession, version_id: uuid.UUID) -> int:
    """Add one version's terms to the lexicon, returning the rows inserted, so a
    freshly ingested law is reachable by a misspelling of its own vocabulary.
    `ndoc` is left as-is for a term already present, so counts drift low; they
    order expansion candidates and nothing else.
    """
    result = await session.execute(
        text(
            """
            INSERT INTO search_terms (term, ndoc)
            SELECT t.term, count(*)
            FROM provisions p,
                 LATERAL unnest(string_to_array(p.search_tokens, ' ')) AS t(term)
            WHERE p.version_id = :version_id AND p.search_tokens IS NOT NULL
              AND octet_length(t.term) <= :max_bytes
            GROUP BY t.term
            ON CONFLICT (term) DO NOTHING
            """
        ),
        {"version_id": version_id, "max_bytes": _MAX_TERM_BYTES},
    )
    return result.rowcount or 0


async def prune_dead_terms(session: AsyncSession) -> int:
    """Drop terms nothing carries. Title search expands through this lexicon
    too, so a term any title holds survives, and both tables are locked because
    a writer of either could commit after this snapshot and lose its term.
    NOWAIT, not a timeout: writers disagree on lock order, and a sweep that
    waits can make a user's workflow the deadlock victim instead of itself."""
    await session.execute(text("LOCK TABLE laws, provisions IN SHARE MODE NOWAIT"))
    result = await session.execute(
        text(
            """
            DELETE FROM search_terms st
            WHERE NOT EXISTS (
                SELECT 1
                FROM provisions p,
                     LATERAL unnest(string_to_array(p.search_tokens, ' ')) AS t(term)
                WHERE p.search_tokens IS NOT NULL AND t.term = st.term
            )
              AND NOT EXISTS (
                SELECT 1
                FROM laws l,
                     LATERAL unnest(string_to_array(l.title_tokens, ' ')) AS t(term)
                WHERE l.title_tokens IS NOT NULL AND t.term = st.term
            )
            """
        )
    )
    removed = result.rowcount or 0
    logger.info("lexicon_dead_terms_pruned", removed=removed)
    return removed


async def rebuild_lexicon(session: AsyncSession) -> int:
    """Restate the whole lexicon from the corpus, returning the term count.
    Nothing schedules it, so `ndoc` drifts and orders expansion candidates on
    stale counts. Dead terms are `prune_dead_terms`' job and the janitor's.
    """
    await session.execute(text("TRUNCATE search_terms"))
    await session.execute(
        text(
            """
            INSERT INTO search_terms (term, ndoc)
            SELECT t.term, count(DISTINCT p.id)
            FROM provisions p,
                 LATERAL unnest(string_to_array(p.search_tokens, ' ')) AS t(term)
            WHERE p.search_tokens IS NOT NULL AND octet_length(t.term) <= :max_bytes
            GROUP BY t.term
            """
        ),
        {"max_bytes": _MAX_TERM_BYTES},
    )
    count = (await session.execute(text("SELECT count(*) FROM search_terms"))).scalar_one()
    logger.info("lexicon_rebuilt", terms=count)
    return int(count)
