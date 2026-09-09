"""Backfill `provisions.search_tokens` and `excluded_from_pool` for versions the
derive pass has not reached.

Ingest writes tokens through `document_to_rows`, so this closes the gap for
rows written before the tokeniser existed and for rows a tokeniser change has
made stale. It is deliberately cheaper than `rederive_version_rows`, which
rebuilds every provision row and clears `embedded_at`, forcing a re-embed the
corpus does not need when only the tokens changed.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import bindparam, select, text, true, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from codify.jurisdictions import placeholder_markers_for
from codify.quality.sentinels import exclusion_reason, is_placeholder_only
from codify.search import TOKENISER_VERSION, tokenise_to_text
from codify.storage.models import Law, Provision, Version

logger = structlog.get_logger()


async def retokenise_version_provisions(
    session: AsyncSession, version_id: uuid.UUID, *, reflag: bool = False
) -> int:
    """Rewrite tokens and the exclusion flag for one version. Returns the number
    of rows changed. Idempotent: rows already current are skipped, so a retried
    child does no work the first attempt did. `reflag` takes every row, for a
    changed marker list or rule that the version stamps cannot see."""
    head = (
        await session.execute(
            select(Version.language, Law.frbr_work_uri)
            .join(Law, Law.id == Version.law_id)
            .where(Version.id == version_id)
        )
    ).one_or_none()
    if head is None:
        logger.warning("retokenise_version_missing", version_id=str(version_id))
        return 0
    language, work_uri = head
    markers = placeholder_markers_for(work_uri)

    # The flag rides the same pass: both are derived from the text alone, and
    # an in-place update is what keeps embeddings and references intact.
    stale = Provision.search_pipeline_version.is_distinct_from(
        TOKENISER_VERSION
    ) | Provision.excluded_from_pool.is_(None)
    rows = (
        await session.execute(
            select(Provision.id, Provision.text, Provision.akn_eid).where(
                Provision.version_id == version_id, true() if reflag else stale
            )
        )
    ).all()
    if not rows:
        return 0

    # `tokenise_to_text` returns nothing for a placeholder, so both lexical arms
    # lose it: the BM25 index reads this column and the tsvector is generated from
    # it. Counted here only to report what the pass dropped.
    placeholders = sum(1 for _pid, txt, _eid in rows if is_placeholder_only(txt, markers))
    if placeholders:
        logger.info(
            "retokenise_skipped_placeholders",
            version_id=str(version_id),
            skipped=placeholders,
        )

    # One executemany rather than a statement per row: a whole-corpus backfill
    # is hundreds of thousands of provisions. Against the Core table, not the
    # ORM entity: an entity update with parameter dicts routes into the ORM's
    # bulk-update-by-primary-key path, which rejects extra WHERE criteria.
    table = Provision.__table__
    stmt = (
        update(table)
        .where(table.c.id == bindparam("target_id"))
        .values(
            search_tokens=bindparam("tokens"),
            search_pipeline_version=bindparam("pipeline_version"),
            excluded_from_pool=bindparam("excluded"),
            exclusion_reason=bindparam("reason"),
        )
    )
    await session.execute(
        stmt,
        [
            {
                "target_id": provision_id,
                "tokens": tokenise_to_text(provision_text, language),
                "pipeline_version": TOKENISER_VERSION,
                "excluded": reason is not None,
                "reason": reason,
            }
            for provision_id, provision_text, akn_eid in rows
            for reason in (exclusion_reason(provision_text, akn_eid, markers),)
        ],
    )
    return len(rows)


async def count_stale_search_tokens(
    session: AsyncSession, version_id: uuid.UUID | None = None
) -> tuple[int, int]:
    """`(provisions the current tokeniser has not written, total)`.

    The derive pass's equivalent of `versions.embedded_at`: a row the current
    tokeniser has not written, or whose exclusion flag is underived, is stale.
    Scoped to one version when `version_id` is given, corpus-wide otherwise. Without it a
    forgotten backfill is invisible: the arm returns nothing, the dense arm
    covers for it, and every response still looks healthy. A `TOKENISER_VERSION`
    bump is worse than a missing backfill, because the rows still match and rank,
    just under the wrong tokenisation.
    """
    row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE search_pipeline_version "
                "IS DISTINCT FROM :current OR excluded_from_pool IS NULL) AS stale, "
                "count(*) AS total FROM provisions "
                "WHERE CAST(:v AS uuid) IS NULL OR version_id = :v"
            ),
            {"current": TOKENISER_VERSION, "v": version_id},
        )
    ).one()
    return int(row.stale), int(row.total)


async def placeholder_shares_for_versions(
    session: AsyncSession, version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, float | None]:
    """Share of each version's units that are placeholders, from the stored reason.
    None where any flag is still underived, so a fresh migration reads as unknown
    rather than as complete; never a fragment or table share."""
    if not version_ids:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT version_id, count(*) AS total, "
                "count(*) FILTER (WHERE exclusion_reason = 'placeholder') AS placeholders, "
                "count(*) FILTER (WHERE excluded_from_pool IS NULL) AS underived "
                "FROM provisions WHERE version_id = ANY(:ids) GROUP BY version_id"
            ).bindparams(bindparam("ids", type_=ARRAY(PG_UUID(as_uuid=True)))),
            {"ids": version_ids},
        )
    ).all()
    out: dict[uuid.UUID, float | None] = {v: None for v in version_ids}
    for version_id, total, placeholders, underived in rows:
        out[version_id] = None if underived or not total else placeholders / total
    return out


__all__ = [
    "count_stale_search_tokens",
    "placeholder_shares_for_versions",
    "retokenise_version_provisions",
]
