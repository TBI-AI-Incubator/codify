"""Persist and query the goods codes a version states. A separate pass from
`document_to_rows`: a code lives in a table cell, and a row is not a provision."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import NamedTuple

import structlog
from sqlalchemy import ColumnElement, delete, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from codify.pipeline.enrich.goods_codes import GoodsCode, extract_goods_codes
from codify.pipeline.formats.eu_directive import on_the_cpu_pool
from codify.storage.akn_size import oversize_reason
from codify.storage.models import GoodsCodeReference, Jurisdiction, Law, Version

logger = structlog.get_logger()


async def index_version_goods_codes(
    session: AsyncSession, version_id: uuid.UUID, akn_xml: str | None = None
) -> int:
    """Rewrite one version's code rows, reading the stored AKN when none is
    given. Idempotent by replacement, so a retry does not double-count.
    Extraction runs on the CPU pool: most of a second on a large annex."""
    if akn_xml is None:
        size = (
            await session.execute(
                select(func.char_length(Version.akn_xml)).where(Version.id == version_id)
            )
        ).scalar_one_or_none()
        if not size:
            logger.warning("goods_codes_no_akn", version_id=str(version_id))
            return 0
    else:
        size = len(akn_xml)
    if reason := oversize_reason(size):
        logger.warning("goods_codes_skipped", version_id=str(version_id), reason=reason)
        return 0
    if akn_xml is None:
        akn_xml = (
            await session.execute(select(Version.akn_xml).where(Version.id == version_id))
        ).scalar_one()
    # Extract before deleting. The caller commits regardless, so a failure
    # after the delete would leave a version that had codes yesterday with none.
    try:
        codes: list[GoodsCode] = await on_the_cpu_pool(extract_goods_codes, akn_xml)
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
                excluded=code.excluded,
                source=code.source,
                lineage=list(code.lineage),
            )
        )
    return len(codes)


class GoodsCodeMatch(NamedTuple):
    """One stated code that answers a lookup, with the law it sits in."""

    version_id: uuid.UUID
    expression_uri: str
    akn_eid: str
    code: str
    surface: str
    partial: bool
    lineage: list[str]
    excluded: bool
    system: str
    source: str
    law_id: uuid.UUID
    frbr_work_uri: str
    law_title: str
    jurisdiction_code: str


async def find_versions_by_goods_code(
    session: AsyncSession,
    code: str,
    *,
    include_narrower: bool = True,
    include_broader: bool = True,
    jurisdiction_codes: Sequence[str] | None = None,
    current_only: bool = True,
    limit: int = 200,
    offset: int = 0,
) -> list[GoodsCodeMatch]:
    """Stated codes that answer `code`. Narrower reaches the subheadings that
    carry a measure; broader reaches the heading or chapter a measure names
    whole (a list naming chapter 72, not 7208). `excluded` rows say a measure
    carves the code out, so a caller reads them as "not covered"."""
    digits = "".join(ch for ch in code if ch.isdigit())
    if not digits:
        return []
    stated = col(GoodsCodeReference.code)
    match: ColumnElement[bool] = stated == digits
    if include_narrower:
        match = or_(match, stated.startswith(digits))
    if include_broader:
        # The prefixes of the question, so the btree serves it.
        match = or_(match, stated.in_([digits[:n] for n in range(2, len(digits))]))
    statement = (
        select(
            Version.id,
            Version.expression_uri,
            GoodsCodeReference.akn_eid,
            GoodsCodeReference.code,
            GoodsCodeReference.surface,
            GoodsCodeReference.partial,
            GoodsCodeReference.lineage,
            GoodsCodeReference.excluded,
            GoodsCodeReference.system,
            GoodsCodeReference.source,
            Law.id,
            Law.frbr_work_uri,
            Law.title,
            Jurisdiction.code,
        )
        .join(GoodsCodeReference, GoodsCodeReference.version_id == Version.id)
        .join(Law, Law.id == Version.law_id)
        .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
        .where(match)
        .order_by(Law.frbr_work_uri, GoodsCodeReference.created_at, GoodsCodeReference.id)
        .limit(limit)
        .offset(offset)
    )
    if jurisdiction_codes is not None:
        statement = statement.where(Jurisdiction.code.in_(list(jurisdiction_codes)))
    if current_only:
        # `is_current` is kept by trigger and deliberately unmapped.
        statement = statement.where(literal_column("versions.is_current"))
    rows = (await session.execute(statement)).all()
    return [
        GoodsCodeMatch(
            version_id=r[0],
            expression_uri=r[1],
            akn_eid=r[2],
            code=r[3],
            surface=r[4],
            partial=r[5],
            lineage=list(r[6] or []),
            excluded=r[7],
            system=r[8],
            source=r[9],
            law_id=r[10],
            frbr_work_uri=r[11],
            law_title=r[12],
            jurisdiction_code=r[13],
        )
        for r in rows
    ]


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
    "GoodsCodeMatch",
    "find_versions_by_goods_code",
    "goods_codes_for_version",
    "index_version_goods_codes",
]
