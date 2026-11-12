"""Official sources: where each version's text came from at its publisher.

One row per version, source anchors keyed by eId, and publisher ids mapped to work
URIs. Callers commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import ARRAY, bindparam, delete, func, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from codify.provenance import PUBLISHERS, parse_official_url
from codify.storage.models import OfficialAnchor, OfficialId, OfficialSource, Version

_LINEAGE_DEPTH_CAP = 100
_ANCHOR_CHUNK = 5000
_FILLABLE = ("edition_id", "edition_date", "fetch_url", "retrieved_at", "content_sha256", "licence")


@dataclass(frozen=True)
class OfficialSourceRecord:
    publisher: str
    work_id: str
    edition_id: str | None = None
    edition_date: date | None = None
    fetch_url: str | None = None
    retrieved_at: datetime | None = None
    content_sha256: str | None = None
    licence: str | None = None
    recorded_by: str = "ingest"


@dataclass(frozen=True)
class OfficialSourceRead:
    """A version's official source; `derived` when it is an ancestor's, as for a translation."""

    source: OfficialSource
    derived: bool


async def _own_hash(session: AsyncSession, version_id: uuid.UUID) -> str | None:
    return (
        await session.execute(select(Version.source_sha256).where(Version.id == version_id))
    ).scalar_one_or_none()


def _values(
    version_id: uuid.UUID, record: OfficialSourceRecord, own_hash: str | None, *, gate_hash: bool
) -> dict[str, Any]:
    return {
        "version_id": version_id,
        "publisher": record.publisher,
        "work_id": record.work_id,
        "edition_id": record.edition_id,
        "edition_date": record.edition_date,
        "fetch_url": record.fetch_url,
        "retrieved_at": record.retrieved_at,
        # On reuse, a hash of bytes the version was not built from would vouch for the wrong text.
        "content_sha256": record.content_sha256
        if not gate_hash or record.content_sha256 == own_hash
        else None,
        "licence": record.licence,
        "recorded_by": record.recorded_by,
    }


async def record_official_source(
    session: AsyncSession,
    version_id: uuid.UUID,
    record: OfficialSourceRecord,
    *,
    stored: bool,
) -> None:
    """Record a version's source: written in full for a newly stored version, and for a
    reused one only into fields still empty, so a reuse never rewrites what was recorded."""
    await _write(session, version_id, record, fill_only=not stored)


async def _write(
    session: AsyncSession, version_id: uuid.UUID, record: OfficialSourceRecord, *, fill_only: bool
) -> None:
    own_hash = await _own_hash(session, version_id)
    values = _values(version_id, record, own_hash, gate_hash=fill_only)
    stmt = insert(OfficialSource).values(values)
    if not fill_only:
        update = {k: v for k, v in values.items() if k != "version_id"} | {
            "recorded_at": func.now()
        }
    else:
        update = {k: func.coalesce(getattr(OfficialSource, k), stmt.excluded[k]) for k in _FILLABLE}
    await session.execute(stmt.on_conflict_do_update(index_elements=["version_id"], set_=update))


async def upsert_official_source(
    session: AsyncSession, version_id: uuid.UUID, record: OfficialSourceRecord
) -> None:
    """Replace a version's row after a refetch; the hash is stored as fetched."""
    await _write(session, version_id, record, fill_only=False)


async def get_official_sources(
    session: AsyncSession, version_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, OfficialSourceRead]:
    """Each version's own row, else its nearest ancestor's. Versions with neither are absent."""
    if not version_ids:
        return {}
    chain = text(
        f"""
        WITH RECURSIVE chain(start_id, id, parent_version_id, depth) AS (
            SELECT v.id, v.id, v.parent_version_id, 0 FROM versions v WHERE v.id = ANY(:ids)
            UNION ALL
            SELECT c.start_id, p.id, p.parent_version_id, c.depth + 1
            FROM chain c JOIN versions p ON p.id = c.parent_version_id
            WHERE c.depth < {_LINEAGE_DEPTH_CAP}
        )
        SELECT DISTINCT ON (c.start_id) c.start_id, c.id, c.depth
        FROM chain c JOIN official_sources o ON o.version_id = c.id
        ORDER BY c.start_id, c.depth
        """  # noqa: S608
    ).bindparams(bindparam("ids", value=list(version_ids), type_=ARRAY(PG_UUID(as_uuid=True))))
    found = [(r[0], r[1], r[2]) for r in (await session.execute(chain)).all()]
    if not found:
        return {}
    rows = (
        (
            await session.execute(
                select(OfficialSource).where(
                    col(OfficialSource.version_id).in_({source_id for _, source_id, _ in found})
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {r.version_id: r for r in rows}
    return {
        start: OfficialSourceRead(by_id[source_id], derived=depth > 0)
        for start, source_id, depth in found
    }


async def get_official_source(
    session: AsyncSession, version_id: uuid.UUID
) -> OfficialSourceRead | None:
    return (await get_official_sources(session, [version_id])).get(version_id)


async def write_official_anchors(
    session: AsyncSession, version_id: uuid.UUID, anchors: Mapping[str, str]
) -> int:
    """Upsert eId to source-fragment pairs for a version; returns how many were given."""
    items = [
        {"version_id": version_id, "akn_eid": eid, "fragment": frag}
        for eid, frag in anchors.items()
    ]
    for start in range(0, len(items), _ANCHOR_CHUNK):
        stmt = insert(OfficialAnchor).values(items[start : start + _ANCHOR_CHUNK])
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["version_id", "akn_eid"], set_={"fragment": stmt.excluded.fragment}
            )
        )
    return len(items)


async def clear_official_anchors(session: AsyncSession, version_id: uuid.UUID) -> None:
    await session.execute(
        delete(OfficialAnchor).where(col(OfficialAnchor.version_id) == version_id)
    )


async def official_anchors(session: AsyncSession, version_id: uuid.UUID) -> dict[str, str]:
    """A version's eId to fragment map."""
    rows = await session.execute(
        select(col(OfficialAnchor.akn_eid), col(OfficialAnchor.fragment)).where(
            col(OfficialAnchor.version_id) == version_id
        )
    )
    return {eid: frag for eid, frag in rows.all()}


def _provision_url(source: OfficialSource, eid: str, anchor: str | None) -> str | None:
    publisher = PUBLISHERS.get(source.publisher)
    if publisher is None:
        return None
    fragment = anchor or publisher.fragment_for_eid(eid)
    if fragment is None:
        return None
    return publisher.provision_url(source.work_id, source.edition_id, fragment)


async def official_provision_urls(
    session: AsyncSession, items: Iterable[tuple[uuid.UUID, OfficialSource, str]]
) -> dict[tuple[uuid.UUID, str], str | None]:
    """`official_provision_url` for many `(version_id, source, eid)` triples, one anchors query."""
    wanted = list(items)
    if not wanted:
        return {}
    rows = await session.execute(
        select(
            col(OfficialAnchor.version_id),
            col(OfficialAnchor.akn_eid),
            col(OfficialAnchor.fragment),
        )
        .where(col(OfficialAnchor.version_id).in_({v for v, _, _ in wanted}))
        .where(col(OfficialAnchor.akn_eid).in_({e for _, _, e in wanted}))
    )
    anchors = {(v, e): f for v, e, f in rows.all()}
    return {(v, e): _provision_url(src, e, anchors.get((v, e))) for v, src, e in wanted}


async def official_provision_url(
    session: AsyncSession, version_id: uuid.UUID, source: OfficialSource, eid: str
) -> str | None:
    """The publisher's URL for one provision of a version, or None.

    A recorded anchor wins over the publisher's eId rule; either way the link is only
    given once its URL pattern is verified. `source` is the version's own or ancestor's row.
    """
    urls = await official_provision_urls(session, [(version_id, source, eid)])
    return urls[(version_id, eid)]


async def eid_for_fragment(
    session: AsyncSession, version_id: uuid.UUID, fragment: str
) -> str | None:
    """The eId a source fragment was recorded against; where several share it, the shortest."""
    return (
        await session.execute(
            select(col(OfficialAnchor.akn_eid))
            .where(col(OfficialAnchor.version_id) == version_id)
            .where(col(OfficialAnchor.fragment) == fragment)
            .order_by(func.length(col(OfficialAnchor.akn_eid)), col(OfficialAnchor.akn_eid))
            .limit(1)
        )
    ).scalar_one_or_none()


async def upsert_official_ids(
    session: AsyncSession,
    publisher: str,
    ids: Iterable[tuple[str, str, str]],
) -> int:
    """Upsert `(id, frbr_work_uri, id_kind)` triples for a publisher; returns how many."""
    # One statement cannot touch a key twice; the last triple for an id wins.
    by_id = {
        i: {"publisher": publisher, "id": i, "frbr_work_uri": uri, "id_kind": kind}
        for i, uri, kind in ids
    }
    items = list(by_id.values())
    for start in range(0, len(items), _ANCHOR_CHUNK):
        stmt = insert(OfficialId).values(items[start : start + _ANCHOR_CHUNK])
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["publisher", "id"],
                set_={
                    "frbr_work_uri": stmt.excluded.frbr_work_uri,
                    "id_kind": stmt.excluded.id_kind,
                },
            )
        )
    return len(items)


async def lookup_official_id(
    session: AsyncSession, publisher: str, official_id: str
) -> OfficialId | None:
    return (
        await session.execute(
            select(OfficialId)
            .where(col(OfficialId.publisher) == publisher)
            .where(col(OfficialId.id) == official_id)
        )
    ).scalar_one_or_none()


async def work_uri_for_official_url(session: AsyncSession, url: str) -> str | None:
    """The work URI a publisher URL names, such as an amending act's, or None if unrecorded."""
    parsed = parse_official_url(url)
    for official_id in parsed.lookup if parsed else ():
        row = await lookup_official_id(session, parsed.publisher, official_id)
        if row is not None:
            return row.frbr_work_uri
    return None


__all__ = [
    "OfficialSourceRead",
    "OfficialSourceRecord",
    "clear_official_anchors",
    "eid_for_fragment",
    "get_official_source",
    "get_official_sources",
    "lookup_official_id",
    "official_anchors",
    "official_provision_url",
    "official_provision_urls",
    "record_official_source",
    "upsert_official_ids",
    "upsert_official_source",
    "work_uri_for_official_url",
    "write_official_anchors",
]
