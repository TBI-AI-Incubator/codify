"""Acquisitions audit log, one row per upstream fetch."""

from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import Acquisition


async def record_acquisition(session: AsyncSession, a: Acquisition) -> Acquisition:
    """Idempotent on (jurisdiction_code, source_url, content_sha256).

    The duplicate is absorbed in a savepoint. A bare rollback would undo the
    caller's whole transaction, and callers save the document on the same
    session before recording the fetch.
    """
    try:
        async with session.begin_nested():
            session.add(a)
            await session.flush()
        return a
    except IntegrityError:
        existing = await find_acquisition_by_sha(
            session, a.jurisdiction_code, a.source_url, a.content_sha256
        )
        if existing is None:
            raise
        return existing


async def find_acquisition_by_sha(
    session: AsyncSession,
    jurisdiction_code: str,
    source_url: str,
    content_sha256: str,
) -> Acquisition | None:
    stmt = select(Acquisition).where(
        Acquisition.jurisdiction_code == jurisdiction_code,
        Acquisition.source_url == source_url,
        Acquisition.content_sha256 == content_sha256,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_acquisition_by_url(
    session: AsyncSession,
    jurisdiction_code: str,
    source_url: str,
) -> Acquisition | None:
    """Most-recent fetch for this URL, drives If-None-Match / If-Modified-Since."""
    stmt = (
        select(Acquisition)
        .where(
            Acquisition.jurisdiction_code == jurisdiction_code,
            Acquisition.source_url == source_url,
        )
        .order_by(desc(Acquisition.fetched_at))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


__all__ = [
    "Acquisition",
    "find_acquisition_by_sha",
    "find_acquisition_by_url",
    "record_acquisition",
]
