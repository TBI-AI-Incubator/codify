"""Provision lookup with section-path ancestry."""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import Jurisdiction, Law, Provision, Section, Version

_MAX_DEPTH = 20


class SectionRef(BaseModel):
    id: uuid.UUID
    akn_eid: str
    akn_type: str
    title: str | None


class ProvisionWithPath(BaseModel):
    id: uuid.UUID
    akn_eid: str
    akn_type: str
    text: str
    position: int
    law_id: uuid.UUID
    version_id: uuid.UUID
    frbr_work_uri: str
    section_path: list[SectionRef]


async def find_provision_id_by_eid(
    session: AsyncSession, version_id: uuid.UUID, akn_eid: str
) -> uuid.UUID | None:
    """Resolve a `(version_id, akn_eid)` pair to a provision UUID, used by
    surfaces (J7 directive comparison) that have an AKN eId from the Document
    layer but need the persisted provision row to attach feedback to."""
    return (
        await session.execute(
            select(Provision.id)
            .where(Provision.version_id == version_id)
            .where(Provision.akn_eid == akn_eid)
        )
    ).scalar_one_or_none()


async def provision_id_map_for_version(
    session: AsyncSession, version_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    """{akn_eid: provision_id} for a single version's provisions."""
    rows = (
        await session.execute(
            select(Provision.akn_eid, Provision.id).where(Provision.version_id == version_id)
        )
    ).all()
    return {eid: pid for eid, pid in rows}


async def find_section_id_by_eid(
    session: AsyncSession, version_id: uuid.UUID, akn_eid: str
) -> uuid.UUID | None:
    """Resolve a `(version_id, akn_eid)` pair to a section UUID, the container
    twin of `find_provision_id_by_eid` (section-level amendment targets)."""
    return (
        await session.execute(
            select(Section.id)
            .where(Section.version_id == version_id)
            .where(Section.akn_eid == akn_eid)
        )
    ).scalar_one_or_none()


async def get_provision_with_path(
    session: AsyncSession, provision_id: uuid.UUID
) -> ProvisionWithPath | None:
    """Return a provision plus its root → leaf-parent section ancestry."""
    row = (
        await session.execute(
            select(Provision, Version, Law)
            .join(Version, Version.id == Provision.version_id)
            .join(Law, Law.id == Version.law_id)
            .where(Provision.id == provision_id)
        )
    ).first()
    if row is None:
        return None
    provision, version, law = row

    chain: list[SectionRef] = []
    if provision.section_id is not None:
        rows = (
            (
                await session.execute(
                    text(
                        f"""
                    WITH RECURSIVE ancestry(id, parent_section_id, akn_eid, akn_type,
                                            title, depth) AS (
                        SELECT id, parent_section_id, akn_eid, akn_type, title, 0
                        FROM sections WHERE id = :start
                        UNION ALL
                        SELECT s.id, s.parent_section_id, s.akn_eid, s.akn_type,
                               s.title, a.depth + 1
                        FROM sections s JOIN ancestry a ON s.id = a.parent_section_id
                        WHERE a.depth < {_MAX_DEPTH}
                    )
                    SELECT id, akn_eid, akn_type, title FROM ancestry ORDER BY depth DESC
                    """  # noqa: S608
                    ),
                    {"start": provision.section_id},
                )
            )
            .mappings()
            .all()
        )
        chain = [SectionRef(**dict(r)) for r in rows]
        if len(chain) >= _MAX_DEPTH:
            import structlog

            structlog.get_logger().warning(
                "section_lineage_truncated", provision_id=str(provision_id), cap=_MAX_DEPTH
            )

    return ProvisionWithPath(
        id=provision.id,
        akn_eid=provision.akn_eid,
        akn_type=provision.akn_type,
        text=provision.text,
        position=provision.position,
        law_id=law.id,
        version_id=version.id,
        frbr_work_uri=law.frbr_work_uri,
        section_path=chain,
    )


__all__ = [
    "ProvisionWithPath",
    "SectionRef",
    "find_provision_id_by_eid",
    "get_provision_with_path",
]


class ProvisionContext(BaseModel):
    """Where a provision lives, for a result list that links to its law."""

    version_id: uuid.UUID
    law_id: uuid.UUID
    law_title: str
    frbr_work_uri: str
    jurisdiction_code: str


async def provision_contexts(
    session: AsyncSession, provision_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ProvisionContext]:
    if not provision_ids:
        return {}
    rows = await session.execute(
        select(Provision.id, Version.id, Law.id, Law.title, Law.frbr_work_uri, Jurisdiction.code)
        .join(Version, Version.id == Provision.version_id)
        .join(Law, Law.id == Version.law_id)
        .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
        .where(Provision.id.in_(provision_ids))
    )
    return {
        pid: ProvisionContext(
            version_id=vid, law_id=lid, law_title=title, frbr_work_uri=uri, jurisdiction_code=code
        )
        for pid, vid, lid, title, uri, code in rows.all()
    }
