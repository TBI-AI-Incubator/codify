"""Jurisdictions CRUD + corpus-count aggregations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import Jurisdiction, Law, Provision, Version
from codify.storage.partitions import ensure_embedding_partition


class JurisdictionCounts(BaseModel):
    laws: int
    versions: int
    provisions: int


async def get_jurisdiction_by_code(session: AsyncSession, code: str) -> Jurisdiction | None:
    result = await session.execute(select(Jurisdiction).where(Jurisdiction.code == code))
    return result.scalar_one_or_none()


async def get_or_create_jurisdiction(
    session: AsyncSession,
    code: str,
    *,
    name: str | None = None,
    family: str | None = None,
    calendar: str | None = None,
    languages: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Jurisdiction:
    """Idempotent fetch-or-insert. Defaults: name=code, calendar=gregorian, lists empty."""
    existing = await get_jurisdiction_by_code(session, code)
    if existing is not None:
        return existing
    row = Jurisdiction(
        code=code,
        name=name or code,
        family=family,
        calendar=calendar or "gregorian",
        languages=languages or [],
        extra=extra or {},
    )
    session.add(row)
    await session.flush()
    await ensure_embedding_partition(session, row.id)
    return row


async def list_jurisdictions(session: AsyncSession) -> list[Jurisdiction]:
    result = await session.execute(select(Jurisdiction).order_by(Jurisdiction.code))
    return list(result.scalars().all())


async def count_laws_by_code(session: AsyncSession) -> dict[str, int]:
    """Return `{jurisdiction_code: laws_count}` for every jurisdiction in the DB."""
    rows = (
        await session.execute(
            select(Jurisdiction.code, func.count(Law.id))
            .join(Law, Law.jurisdiction_id == Jurisdiction.id, isouter=True)
            .group_by(Jurisdiction.code)
        )
    ).all()
    return {code: int(count) for code, count in rows}


async def jurisdiction_counts(session: AsyncSession, code: str) -> JurisdictionCounts:
    """Laws / versions / provisions for a single jurisdiction. Zeros if absent."""
    row = (
        await session.execute(
            select(
                func.count(func.distinct(Law.id)).label("laws"),
                func.count(func.distinct(Version.id)).label("versions"),
                func.count(func.distinct(Provision.id)).label("provisions"),
            )
            .select_from(Jurisdiction)
            .join(Law, Law.jurisdiction_id == Jurisdiction.id, isouter=True)
            .join(Version, Version.law_id == Law.id, isouter=True)
            .join(Provision, Provision.version_id == Version.id, isouter=True)
            .where(Jurisdiction.code == code)
        )
    ).first()
    if row is None:
        return JurisdictionCounts(laws=0, versions=0, provisions=0)
    laws, versions, provisions = row
    return JurisdictionCounts(laws=int(laws), versions=int(versions), provisions=int(provisions))


__all__ = [
    "JurisdictionCounts",
    "count_laws_by_code",
    "get_jurisdiction_by_code",
    "get_or_create_jurisdiction",
    "jurisdiction_counts",
    "list_jurisdictions",
]
