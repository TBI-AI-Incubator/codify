"""Tokenised law titles, the index behind finding a law by name.

Both retrieval arms search `provisions`, so before this the only way to learn
whether a law was in the corpus was to re-upload it and read the duplicate
error. `laws.title_tokens` carries the same pre-normalised form
`codify.search.tokenise` produces for provision text.

Every name a law has goes in: the original title, the short title, and each
stored translation tokenised in its own language. Tokens from different scripts
cannot collide, so the union costs nothing, and the digit folding in the
normaliser means an Arabic title carrying ١٩٢٩ answers a query for 1929.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import bindparam, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from codify.search import TOKENISER_VERSION, tokenise, tokenise_to_text
from codify.storage.lexicon import expand_terms
from codify.storage.models import Law, Version

logger = structlog.get_logger()

_TITLE_LANGUAGE_SQL = text(
    """
    SELECT language FROM versions
    WHERE law_id = :law_id
    ORDER BY (parent_version_id IS NULL) DESC, expression_date DESC, ingested_at DESC
    LIMIT 1
    """
)


def title_tokens_for(
    title: str,
    *,
    language: str,
    short_title: str | None = None,
    translations: dict[str, str] | None = None,
) -> str:
    """Token stream for every name a law answers to.

    `language` tokenises the original title and short title; each translation is
    tokenised in the language it is filed under, because stemming a title with
    the wrong language's stemmer is how a term stops matching itself.
    """
    parts = [tokenise_to_text(title or "", language)]
    if short_title:
        parts.append(tokenise_to_text(short_title, language))
    for lang, translated in sorted((translations or {}).items()):
        if translated:
            parts.append(tokenise_to_text(translated, lang))
    return " ".join(part for part in parts if part)


async def title_language_for(session: AsyncSession, law_id: uuid.UUID) -> str | None:
    """Which language to tokenise a law's own title in.

    The original expression's language where there is one, otherwise the latest
    expression of any kind. A corpus holds laws whose only version is a
    translation, an EU directive reached through a Ukrainian expression among
    them. Insisting on an original left their tokens null, and since the backfill
    selects exactly the rows whose tokens are missing, it re-selected the same law
    every run and never converged. None now means no version at all, which is the
    only case with nothing to read a language from.
    """
    return (await session.execute(_TITLE_LANGUAGE_SQL, {"law_id": law_id})).scalar_one_or_none()


async def refresh_law_title_tokens(
    session: AsyncSession, law: Law, *, language: str | None = None
) -> None:
    """Recompute one law's title tokens in place. Caller commits.

    `language` is passed at ingest, where the law row is created before any
    version exists to look one up from. Everywhere else it is resolved from the
    law's own expressions, preferring the original so a translated title never
    restems the source title.
    """
    resolved = language or await title_language_for(session, law.id)
    if resolved is None:
        # No version at all, so nothing names a language. Leave the column null;
        # the backfill selects on it and reaches the law once one exists.
        logger.debug("law_title_tokens_no_version", law_id=str(law.id))
        return
    law.title_tokens = title_tokens_for(
        law.title,
        language=resolved,
        short_title=law.short_title,
        translations=law.title_translations,
    )
    law.title_search_pipeline_version = TOKENISER_VERSION


async def retokenise_laws_for_version(session: AsyncSession, version_id: uuid.UUID) -> int:
    """Refresh the title of the law this version belongs to."""
    law_id = (
        await session.execute(select(Version.law_id).where(Version.id == version_id))
    ).scalar_one_or_none()
    if law_id is None:
        return 0
    return await retokenise_laws(session, [law_id])


async def retokenise_laws(session: AsyncSession, law_ids: list[uuid.UUID] | None = None) -> int:
    """Backfill titles the tokeniser has not reached. Returns rows changed.

    Idempotent: rows already at `TOKENISER_VERSION` are skipped, so a retried
    child workflow does no work the first attempt already did.
    """
    query = select(Law.id, Law.title, Law.short_title, Law.title_translations).where(
        Law.title_search_pipeline_version.is_distinct_from(TOKENISER_VERSION)
    )
    if law_ids is not None:
        if not law_ids:
            return 0
        query = query.where(Law.id.in_(law_ids))
    rows = (await session.execute(query)).all()
    if not rows:
        return 0

    params = []
    for law_id, title, short_title, translations in rows:
        language = await title_language_for(session, law_id)
        if language is None:
            continue
        params.append(
            {
                "target_id": law_id,
                "tokens": title_tokens_for(
                    title,
                    language=language,
                    short_title=short_title,
                    translations=translations,
                ),
                "pipeline_version": TOKENISER_VERSION,
            }
        )
    if not params:
        return 0

    # One executemany against the Core table, matching `retokenise_version_
    # provisions`: an entity update with parameter dicts routes into the ORM's
    # bulk-update-by-primary-key path, which rejects extra WHERE criteria.
    table = Law.__table__
    stmt = (
        update(table)
        .where(table.c.id == bindparam("target_id"))
        .values(
            title_tokens=bindparam("tokens"),
            title_search_pipeline_version=bindparam("pipeline_version"),
        )
    )
    await session.execute(stmt, params)
    logger.info("law_titles_retokenised", count=len(params))
    return len(params)


async def title_query_tokens(
    session: AsyncSession, query: str, jurisdictions: list[str] | None = None
) -> tuple[str, str]:
    """Tokenise a title query under every language in scope.

    Returns `(bm25_tokens, tsquery)`, both empty when the query carries nothing
    lexical. The union mirrors `query_tokens_for` for provisions: a mixed-language
    scope has no single right language, and guessing one silently stems the query
    wrongly. Tokens from different scripts cannot collide, and a term absent from
    the corpus contributes nothing to a BM25 score.

    ORed, not ANDed, for the same reason the provision arm ORs: the tokens come
    from several languages, so an AND would demand one language's stem *and*
    another's. The tsvector decides membership, BM25 decides order.
    """
    # Both sources, because they diverge: a translated *title* can be recorded
    # against a law that has no version in that language, and tokenising the
    # query only under the version languages would stem an English query with the
    # Arabic stemmer and miss the English title it was stored under.
    scope = "WHERE j.code = ANY(:codes)" if jurisdictions else ""
    sql = f"""
        SELECT DISTINCT lang FROM (
            SELECT v.language AS lang
            FROM versions v
            JOIN laws l ON l.id = v.law_id
            JOIN jurisdictions j ON j.id = l.jurisdiction_id
            {scope}
            UNION
            SELECT jsonb_object_keys(l.title_translations) AS lang
            FROM laws l
            JOIN jurisdictions j ON j.id = l.jurisdiction_id
            {scope}
        ) t
    """  # noqa: S608
    params: dict[str, object] = {}
    if jurisdictions:
        params["codes"] = list(jurisdictions)
    languages = (await session.execute(text(sql), params)).scalars().all()

    seen: set[str] = set()
    tokens: list[str] = []
    for language in languages or ["eng"]:
        for token in tokenise(query, language):
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    if not tokens:
        return "", ""
    # Same widening as the provision arm: a reader who half-remembers a law's
    # name misspells it, and the title index is the surface where that is most
    # likely, because a title is the thing people type from memory.
    tokens = await expand_terms(session, tokens)
    # No escaping: the tokeniser emits letters and digits only, and expansion
    # returns stored tokens, which went through the same tokeniser. Neither can
    # carry a tsquery operator.
    return " ".join(tokens), " | ".join(tokens)
