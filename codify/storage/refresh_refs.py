"""Re-extract cross_references from a version's stored AKN.

Extraction is otherwise frozen at ingest time; this replays the reference
enrichment passes over `versions.akn_xml`, rebuilds the version's
cross_references against its existing provisions, then resolves. Every
extractor improvement becomes backfillable.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from codify.akn.io import parse_akn
from codify.storage.mappers import _ref_to_row
from codify.storage.models import CrossReference, Jurisdiction, Law, Provision, Version
from codify.storage.resolve_refs import ResolveStats, resolve_references_for_version

if TYPE_CHECKING:
    from codify.core.llm import LLMClient

logger = structlog.get_logger()


class LegacyReferenceEvidenceRequired(ValueError):
    """Historical targets lack the URI needed for safe replacement."""

    def __init__(self, version_id: uuid.UUID) -> None:
        super().__init__(version_id)
        self.version_id = version_id

    def __str__(self) -> str:
        return f"version {self.version_id} has targeted references without original URIs"


async def refresh_references_for_version(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    client: "LLMClient | None" = None,
) -> dict[str, Any]:
    """Replace `version_id`'s cross_references with freshly-extracted ones,
    then resolve. `client=None` runs the deterministic passes only. Not purely
    additive: resolution is re-derived, so links to since-removed laws regress
    to dangling."""
    row = (
        await session.execute(
            select(Version.akn_xml, Law.doctype, Jurisdiction.code)
            .join(Law, Law.id == Version.law_id)
            .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
            .where(Version.id == version_id)
            .with_for_update(of=Version)
        )
    ).one_or_none()
    if row is None:
        raise ValueError(f"version {version_id} not found")
    akn_xml, doctype, code = row

    # Parent locks conflict with FK checks for new provisions/references;
    # existing reference updates are covered by the subsequent row locks.
    prov_by_eid: dict[str, uuid.UUID] = dict(
        (
            await session.execute(
                select(Provision.akn_eid, Provision.id)
                .where(Provision.version_id == version_id)
                .order_by(Provision.id)
                .with_for_update()
            )
        ).all()
    )
    existing = (
        (
            await session.execute(
                select(CrossReference)
                .where(
                    col(CrossReference.source_provision_id).in_(
                        select(Provision.id).where(Provision.version_id == version_id)
                    )
                )
                .order_by(CrossReference.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if any(
        ref.target_uri is None
        and any(
            target is not None
            for target in (ref.target_provision_id, ref.target_section_id, ref.target_law_id)
        )
        for ref in existing
    ):
        raise LegacyReferenceEvidenceRequired(version_id)

    rows, unmatched = await _extract_reference_rows(akn_xml, code, doctype, prov_by_eid, client)

    await session.execute(
        delete(CrossReference).where(
            CrossReference.source_provision_id.in_(
                select(Provision.id).where(Provision.version_id == version_id)
            )
        )
    )
    session.add_all(rows)
    await session.flush()

    stats: ResolveStats = await resolve_references_for_version(session, version_id)
    out = {
        "extracted": len(rows),
        "unmatched_source_eids": unmatched,
        **{f"resolve_{k}": v for k, v in stats.as_dict().items()},
    }
    logger.info("refresh_references", version_id=str(version_id), **out)
    return out


async def _extract_reference_rows(
    akn_xml: str,
    code: str,
    doctype: str,
    prov_by_eid: dict[str, uuid.UUID],
    client: "LLMClient | None" = None,
) -> tuple[list[CrossReference], int]:
    """Extract candidate rows without changing stored references."""
    from codify.pipeline.enrich.inline_markup import emit_inline_markup
    from codify.pipeline.enrich.references import emit_references

    enriched = emit_references(akn_xml, code)
    enriched = await emit_inline_markup(enriched, code, doctype, client)
    doc = parse_akn(enriched)

    rows: list[CrossReference] = []
    unmatched = 0

    def _walk(el: object) -> None:
        nonlocal unmatched
        eid = getattr(el, "akn_eid", None)
        refs = getattr(el, "references", None) or []
        if refs:
            pid = prov_by_eid.get(eid or "")
            if pid is None:
                unmatched += len(refs)
            else:
                for ref in refs:
                    r = _ref_to_row(ref, source_provision_id=pid)
                    if r is not None:
                        rows.append(r)
        for child in getattr(el, "children", []) or []:
            _walk(child)

    for top in doc.body:
        _walk(top)

    return rows, unmatched
