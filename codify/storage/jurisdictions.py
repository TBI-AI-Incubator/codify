"""Jurisdictions CRUD + corpus-count aggregations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import Jurisdiction, Law, Version


class JurisdictionCounts(BaseModel):
    laws: int
    versions: int
    # Always None: see `jurisdiction_counts`.
    provisions: int | None = None


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
    """Laws and versions for a single jurisdiction. Zeros if absent.

    Two counts on indexed keys. Provisions are not counted: an exact count
    reads every provision row the jurisdiction holds.
    """
    jurisdiction = await get_jurisdiction_by_code(session, code)
    if jurisdiction is None:
        return JurisdictionCounts(laws=0, versions=0)
    laws = await session.scalar(
        select(func.count()).select_from(Law).where(Law.jurisdiction_id == jurisdiction.id)
    )
    versions = await session.scalar(
        select(func.count())
        .select_from(Version)
        .join(Law, Law.id == Version.law_id)
        .where(Law.jurisdiction_id == jurisdiction.id)
    )
    return JurisdictionCounts(laws=int(laws or 0), versions=int(versions or 0))


__all__ = [
    "JurisdictionCounts",
    "count_laws_by_code",
    "get_jurisdiction_by_code",
    "get_or_create_jurisdiction",
    "jurisdiction_counts",
    "list_jurisdictions",
]
