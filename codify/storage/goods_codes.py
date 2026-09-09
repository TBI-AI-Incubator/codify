"""Persist and query the goods codes a version states. A separate pass from
`document_to_rows`: a code lives in a table cell, and a row is not a provision."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from codify.pipeline.enrich.goods_codes import extract_goods_codes
from codify.storage.models import GoodsCodeReference, Law, Version

logger = structlog.get_logger()


async def index_version_goods_codes(
    session: AsyncSession, version_id: uuid.UUID, akn_xml: str | None = None
) -> int:
    """Rewrite one version's code rows, reading the stored AKN when none is
    given. Idempotent by replacement, so a retry does not double-count."""
    if akn_xml is None:
        akn_xml = (
            await session.execute(select(Version.akn_xml).where(Version.id == version_id))
        ).scalar_one_or_none()
        if not akn_xml:
            logger.warning("goods_codes_no_akn", version_id=str(version_id))
            return 0
    # Extract before deleting. The caller commits regardless, so a failure
    # after the delete would leave a version that had codes yesterday with none.
    try:
        codes = extract_goods_codes(akn_xml)
    except Exception as exc:  # noqa: BLE001, the prior rows are worth more than this run
        logger.warning(
            "goods_codes_extract_failed",
            version_id=str(version_id),
            error=f"{type(exc).__name__}: {str(exc)[:160]}",
        )
        return 0
    await session.execute(
        delete(GoodsCodeReference).where(GoodsCodeReference.version_id == version_id)
    )
    for code in codes:
        session.add(
            GoodsCodeReference(
                version_id=version_id,
                akn_eid=code.eid,
                system=code.system,
                code=code.code,
                surface=code.surface,
                partial=code.partial,
                source=code.source,
                lineage=list(code.lineage),
            )
        )
    return len(codes)


async def find_versions_by_goods_code(
    session: AsyncSession,
    code: str,
    *,
    include_narrower: bool = True,
    limit: int = 200,
) -> list[tuple[uuid.UUID, str, str, str, str, bool, list[str]]]:
    """Versions stating `code`: (version_id, uri, eid, code, surface, partial,
    lineage). `include_narrower` reaches the subheadings that carry the measure."""
    digits = "".join(ch for ch in code if ch.isdigit())
    if not digits:
        return []
    statement = (
        select(
            Version.id,
            Version.expression_uri,
            GoodsCodeReference.akn_eid,
            GoodsCodeReference.code,
            GoodsCodeReference.surface,
            GoodsCodeReference.partial,
            GoodsCodeReference.lineage,
        )
        .join(GoodsCodeReference, GoodsCodeReference.version_id == Version.id)
        .limit(limit)
    )
    if include_narrower:
        statement = statement.where(GoodsCodeReference.code.startswith(digits))
    else:
        statement = statement.where(GoodsCodeReference.code == digits)
    rows = (await session.execute(statement)).all()
    return [(r[0], r[1], r[2], r[3], r[4], r[5], list(r[6] or [])) for r in rows]


async def goods_codes_for_version(
    session: AsyncSession, version_id: uuid.UUID
) -> list[GoodsCodeReference]:
    """Every code row for one version, in the order it was extracted.

    Not by eId: row ids are unpadded, so `__tr_10` sorts before `__tr_2`.
    """
    return list(
        (
            await session.execute(
                select(GoodsCodeReference)
                .where(GoodsCodeReference.version_id == version_id)
                .order_by(GoodsCodeReference.created_at, GoodsCodeReference.id)
            )
        )
        .scalars()
        .all()
    )


async def count_goods_codes_for_law(session: AsyncSession, law_id: uuid.UUID) -> int:
    """How many codes a law's versions state, for a coverage badge."""
    rows = (
        await session.execute(
            select(GoodsCodeReference.id)
            .join(Version, Version.id == GoodsCodeReference.version_id)
            .join(Law, Law.id == Version.law_id)
            .where(Law.id == law_id)
        )
    ).all()
    return len(rows)


__all__ = [
    "count_goods_codes_for_law",
    "find_versions_by_goods_code",
    "goods_codes_for_version",
    "index_version_goods_codes",
]
