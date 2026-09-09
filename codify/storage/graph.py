"""Citation-chain traversal over the `cross_references` reference graph.
Deterministic walks of what a provision cites and what cites it, to a bounded
depth with cycle protection. A dangling edge, holding only a `target_uri`,
surfaces as a leaf and is never recursed through.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import structlog
from pydantic import BaseModel
from sqlalchemy import ARRAY, Text, and_, bindparam, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from codify.akn.analysis import QuotedContent
from codify.storage.models import (
    AmendmentEffect,
    CrossReference,
    Jurisdiction,
    Law,
    Provision,
    Section,
    Version,
)

_log = structlog.get_logger()

# Absolute ceiling on walk depth regardless of the caller's request, a
# malformed graph (or a cycle the visited-set somehow misses) can't run away.
_HARD_MAX_DEPTH = 10


class CitationNode(BaseModel):
    """One edge in a citation chain, resolved to its far node.

    Provision targets set `provision_id`, container targets `section_id`,
    dangling edges only `target_uri`. Section targets don't recurse.
    """

    provision_id: uuid.UUID | None
    section_id: uuid.UUID | None = None
    akn_eid: str | None
    target_uri: str | None
    edge_class: str
    ref_type: str
    depth: int


class CitationChain(BaseModel):
    """Result of a citation walk. `truncated` is True when a node was found at the
    maximum depth, so `nodes` may be incomplete. A cycle cut is not truncation:
    a node reappearing on a path is returned once, at its shallowest reach.
    """

    nodes: list[CitationNode]
    truncated: bool


def _walk_sql(
    direction: Literal["inbound", "outbound"],
    max_depth: int,
    *,
    restrict_jurisdictions: bool,
) -> str:
    """Assemble the recursive-CTE text for one direction. `near` is the anchor-side
    column, `far` the node hopped to. Outbound must treat NULL targets as
    non-recursable leaves; inbound's far node is always held, the FK being NOT
    NULL.
    """
    if direction == "outbound":
        near, far = "source_provision_id", "target_provision_id"
        recurse_guard = "w.far_id IS NOT NULL AND "
        final_join = "LEFT JOIN"
        uri_expr = "w.target_uri"
        section_expr = "w.section_id"
    else:
        near, far = "target_provision_id", "source_provision_id"
        recurse_guard = ""
        final_join = "JOIN"
        uri_expr = "NULL"
        section_expr = "NULL"
    allowed_ctes = ""
    allowed_edge = ""
    if restrict_jurisdictions:
        allowed_ctes = """
    allowed_provisions AS (
        SELECT p.id
        FROM provisions p
        JOIN versions v ON v.id = p.version_id
        JOIN laws l ON l.id = v.law_id
        JOIN jurisdictions j ON j.id = l.jurisdiction_id
        WHERE lower(j.code) = ANY(:jurisdictions)
    ),
    allowed_sections AS (
        SELECT s.id
        FROM sections s
        JOIN versions v ON v.id = s.version_id
        JOIN laws l ON l.id = v.law_id
        JOIN jurisdictions j ON j.id = l.jurisdiction_id
        WHERE lower(j.code) = ANY(:jurisdictions)
    ),
    """
        # `far` is selected from two fixed column names above, never caller input.
        allowed_edge = (
            f"\n          AND (cr.{far} IS NULL OR "  # noqa: S608
            f"cr.{far} IN (SELECT id FROM allowed_provisions))"
            "\n          AND (cr.target_section_id IS NULL OR "
            "cr.target_section_id IN (SELECT id FROM allowed_sections))"
        )
    return f"""
    WITH RECURSIVE {allowed_ctes}walk(edge_id, near_id, far_id, section_id, target_uri, ref_type,
                        edge_class, depth, visited) AS (
        SELECT cr.id, cr.{near}, cr.{far}, cr.target_section_id, cr.target_uri,
               cr.ref_type, cr.edge_class, 1, ARRAY[CAST(:start AS uuid)]
        FROM cross_references cr
        WHERE cr.{near} = :start
          AND (NOT :filter_edges OR cr.edge_class = ANY(:edge_classes))
          {allowed_edge}
        UNION ALL
        SELECT cr.id, cr.{near}, cr.{far}, cr.target_section_id, cr.target_uri,
               cr.ref_type, cr.edge_class, w.depth + 1, w.visited || w.far_id
        FROM cross_references cr
        JOIN walk w ON cr.{near} = w.far_id
        WHERE {recurse_guard}w.depth < {max_depth}
          AND cr.{near} <> ALL(w.visited)
          AND (NOT :filter_edges OR cr.edge_class = ANY(:edge_classes))
          {allowed_edge}
    )
    SELECT w.far_id AS provision_id, {section_expr} AS section_id,
           COALESCE(p.akn_eid, s.akn_eid) AS akn_eid, {uri_expr} AS target_uri,
           w.edge_class, w.ref_type, w.depth
    FROM walk w
    {final_join} provisions p ON p.id = w.far_id
    LEFT JOIN sections s ON s.id = {section_expr}
    ORDER BY w.depth, akn_eid NULLS LAST, w.edge_id
    """  # noqa: S608


async def citation_chain(
    session: AsyncSession,
    provision_id: uuid.UUID,
    *,
    direction: Literal["inbound", "outbound"],
    edge_classes: list[str] | None = None,
    max_depth: int = 3,
    jurisdictions: tuple[str, ...] | None = None,
) -> CitationChain:
    """Walk the citation graph from `provision_id`, outbound for what it references and
    inbound for what references it.

    `edge_classes` restricts every hop: `None` is unfiltered, an empty list matches
    nothing. `jurisdictions` limits every reached provision, not only the start.
    Depth is clamped to ``[1, _HARD_MAX_DEPTH]``, cycles terminate on a visited set,
    and `truncated` reports whether the cap was reached.
    """
    depth = min(max(max_depth, 1), _HARD_MAX_DEPTH)
    # None → unfiltered; [] → match nothing (fail closed, not open).
    filter_edges = edge_classes is not None
    normalised_jurisdictions = (
        tuple(sorted({code.lower() for code in jurisdictions}))
        if jurisdictions is not None
        else None
    )
    stmt = text(
        _walk_sql(
            direction,
            depth,
            restrict_jurisdictions=normalised_jurisdictions is not None,
        )
    ).bindparams(
        bindparam("edge_classes", type_=ARRAY(Text)),
    )
    rows = (
        (
            await session.execute(
                stmt,
                {
                    "start": provision_id,
                    "filter_edges": filter_edges,
                    "edge_classes": edge_classes or [],
                    "jurisdictions": list(normalised_jurisdictions or ()),
                },
            )
        )
        .mappings()
        .all()
    )
    truncated = any(r["depth"] >= depth for r in rows)
    if truncated:
        _log.warning(
            "citation_chain_depth_capped",
            provision_id=str(provision_id),
            direction=direction,
            cap=depth,
        )
    return CitationChain(nodes=[CitationNode(**dict(r)) for r in rows], truncated=truncated)


class AmendmentRecord(BaseModel):
    """One `<textualMod>` targeting a provision. `akn_category` and `akn_action` are
    the AKN modification group and action, `quoted` preserves the
    `<old>`/`<new>`/`<previous>` blocks, `authority_uri` identifies the amending
    act, and `target_frbr_uri` the exact destination the mod addresses.
    """

    akn_category: str
    akn_action: str
    source_akn_wid: str
    target_frbr_uri: str
    target_akn_wid: str | None
    quoted: dict[str, Any] | None
    authority_uri: str | None
    mod_eid_ref: str | None


async def amendment_history(
    session: AsyncSession, provision_id: uuid.UUID
) -> list[AmendmentRecord]:
    """Modifications targeting `provision_id`, matched on `target_akn_wid`, stable
    across renumbering, or on the destination fragment's eId when the amendment
    carries no wId. Whole-document mods live in `lifecycle_events`. Ordered by
    authority then a stable id, not chronologically: AKN records no reliable
    per-mod date.
    """
    return await _amendment_history_for_unit(session, Provision, provision_id)


async def section_amendment_history(
    session: AsyncSession, section_id: uuid.UUID
) -> list[AmendmentRecord]:
    """Modifications targeting `section_id`, the section-level twin of
    `amendment_history`. Section-level effects ("s. 65 heading substituted")
    target container units that live in `sections`, not `provisions`; same
    work-prefix + wId/eId matching, same ordering caveats."""
    return await _amendment_history_for_unit(session, Section, section_id)


class LawAmendmentRecord(BaseModel):
    """One `<textualMod>` targeting somewhere inside a law, with the amending
    act's title resolved when the platform holds it. `id` is the effect row's
    primary key, `authority_title` resolves `authority_uri` against holdings;
    `quoted` preserves the AKN `<previous>` / `<old>` / `<new>` blocks."""

    id: uuid.UUID
    akn_category: str
    akn_action: str
    target_frbr_uri: str
    target_akn_wid: str | None
    quoted: QuotedContent | None
    authority_uri: str | None
    authority_title: str | None
    mod_eid_ref: str | None


@dataclass(frozen=True)
class LocatedEffect:
    # Compatibility with timeline.Effect: this read-side value is a resolved
    # eId, never a mutation of the stored stable wId.
    target_akn_wid: str | None
    akn_action: str
    in_force_date: date | None
    applied: bool | None


async def effects_for_version(session: AsyncSession, version_id: uuid.UUID) -> list[LocatedEffect]:
    """Inbound effects located on the held expression, with exact mirrors folded.

    Row ownership identifies the supplying document, not the amended law.
    Conflicting dates, source identities or quoted text remain distinct records.
    """
    work = (
        await session.execute(
            select(Law.frbr_work_uri)
            .join(Version, Version.law_id == Law.id)
            .where(Version.id == version_id)
        )
    ).scalar_one()
    units: list[Any] = []
    for model in (Section, Provision):
        units.extend(
            (
                await session.execute(
                    select(model.akn_eid, model.akn_wid).where(model.version_id == version_id)
                )
            ).all()
        )
    by_wid: dict[str, set[str]] = {}
    held = {eid for eid, _wid in units}
    for eid, wid in units:
        if wid:
            by_wid.setdefault(wid, set()).add(eid)
    rows = (
        (
            await session.execute(
                select(AmendmentEffect)
                .where(
                    or_(
                        AmendmentEffect.target_frbr_uri == work,
                        AmendmentEffect.target_frbr_uri.startswith(work + "/", autoescape=True),
                        AmendmentEffect.target_frbr_uri.startswith(work + "#", autoescape=True),
                    )
                )
                .order_by(
                    AmendmentEffect.in_force_date.is_(None),
                    AmendmentEffect.in_force_date,
                    AmendmentEffect.id,
                )
            )
        )
        .scalars()
        .all()
    )
    groups: dict[tuple[Any, ...], dict[uuid.UUID, list[LocatedEffect]]] = {}
    for row in rows:
        candidates = set(by_wid.get(row.target_akn_wid or "", set()))
        fragment = row.target_frbr_uri.partition("#")[2]
        if fragment in held:
            candidates.add(fragment)
        eid = next(iter(candidates)) if len(candidates) == 1 else None
        effect = LocatedEffect(eid, row.akn_action, row.in_force_date, row.applied)
        key = (
            eid or row.target_frbr_uri,
            row.source_akn_wid,
            row.akn_category,
            row.akn_action,
            row.authority_uri,
            row.in_force_date,
            row.applied,
            json.dumps(row.quoted, sort_keys=True),
        )
        groups.setdefault(key, {}).setdefault(row.version_id, []).append(effect)
    # Preserve repeated records within one source; fold only exact copies
    # across supplying versions, retaining the greatest observed multiplicity.
    return [effect for owners in groups.values() for effect in max(owners.values(), key=len)]


async def law_amendment_history(
    session: AsyncSession, law_id: uuid.UUID
) -> list[LawAmendmentRecord]:
    """Every `<textualMod>` targeting any provision or section of `law_id`, with the
    amending act's title resolved when the platform holds it. Ordered by authority
    then a stable id: AKN records no reliable per-mod date, so this is not a
    chronological timeline.
    """
    work_prefix = Law.frbr_work_uri.concat("/")
    amending = aliased(Law)
    stmt = (
        select(AmendmentEffect, amending.title)
        .select_from(Law)
        .join(
            AmendmentEffect,
            func.left(AmendmentEffect.target_frbr_uri, func.length(work_prefix)) == work_prefix,
        )
        .outerjoin(amending, amending.frbr_work_uri == AmendmentEffect.authority_uri)
        .where(Law.id == law_id)
        .order_by(AmendmentEffect.authority_uri, AmendmentEffect.id)
    )
    rows = (await session.execute(stmt)).all()
    # Structured-lane effects carry upstream registry hrefs (`/go/{ref}`)
    # that never match an FRBR work URI; resolve those titles from the
    # official registry via the era bridge, same as law_impact.
    go_refs = {
        e.authority_uri[4:]
        for e, title in rows
        if title is None and (e.authority_uri or "").startswith("/go/")
    }
    go_titles = await _registry_titles(session, law_id, go_refs) if go_refs else {}
    return [
        LawAmendmentRecord(
            id=effect.id,
            akn_category=effect.akn_category,
            akn_action=effect.akn_action,
            target_frbr_uri=effect.target_frbr_uri,
            target_akn_wid=effect.target_akn_wid,
            quoted=effect.quoted,
            authority_uri=effect.authority_uri,
            authority_title=title
            or (
                go_titles.get((effect.authority_uri or "")[4:])
                if (effect.authority_uri or "").startswith("/go/")
                else None
            ),
            mod_eid_ref=effect.mod_eid_ref,
        )
        for effect, title in rows
    ]


async def _registry_titles(
    session: AsyncSession, law_id: uuid.UUID, refs: set[str]
) -> dict[str, str]:
    """Registry-work titles for `/go/{ref}` reference codes, scoped to the
    amended law's jurisdiction. The stored ref uses the internal convocation
    code (198-19); an incoming citation may use the roman form (198-VIII),
    so match either."""
    if not refs:
        return {}
    result = await session.execute(
        text(
            """
            WITH era(code, roman) AS (VALUES
                ('12','XII'),('13','XIII'),('14','XIV'),('15','IV'),('16','V'),
                ('17','VI'),('18','VII'),('19','VIII'),('20','IX'),('21','X')),
            j AS (SELECT jurisdiction_id AS jid FROM laws WHERE id = :law_id)
            SELECT rw.ref, rw.title
            FROM registry_works rw, j
            WHERE rw.jurisdiction_id = j.jid
              AND (
                rw.ref = ANY(:refs)
                OR rw.ref = ANY(
                    SELECT regexp_replace(r, '-' || e.roman || '$', '-' || e.code)
                    FROM unnest(:refs) AS r, era e
                    WHERE r ~ ('-' || e.roman || '$')
                )
              )
            """
        ),
        {"law_id": law_id, "refs": list(refs)},
    )
    out: dict[str, str] = {}
    roman_by_code = {
        "12": "XII",
        "13": "XIII",
        "14": "XIV",
        "15": "IV",
        "16": "V",
        "17": "VI",
        "18": "VII",
        "19": "VIII",
        "20": "IX",
        "21": "X",
    }
    for ref, title in result.all():
        out[ref] = title
        # also index by the roman form so the caller's lookup (which uses the
        # citation's own ref) resolves regardless of which form was stored
        base, _, code = str(ref).rpartition("-")
        if code in roman_by_code:
            out[f"{base}-{roman_by_code[code]}"] = title
    return out


class LawCitationCounterparty(BaseModel):
    """Another law that participates in a citation relationship with this law.
    `ref_count` is the total number of `cross_references` rows between the
    two laws in the requested direction. `edge_class_counts` maps each
    edge class to its per-class count."""

    law_id: uuid.UUID
    title: str
    frbr_work_uri: str
    jurisdiction_code: str
    ref_count: int
    edge_class_counts: dict[str, int]


async def _law_citation_counterparties(
    session: AsyncSession,
    law_id: uuid.UUID,
    *,
    direction: Literal["inbound", "outbound"],
) -> list[LawCitationCounterparty]:
    """Aggregate held cross_references crossing this law's boundary, from the
    perspective of `law_id`: outbound is what it cites, inbound what cites it.
    Only edges whose far side is a held provision count, since a dangling
    `target_uri` has no resolvable counterparty law.
    """
    outbound = direction == "outbound"
    source_v = aliased(Version)
    target_v = aliased(Version)
    source_p = aliased(Provision)
    target_p = aliased(Provision)
    source_law = aliased(Law)
    target_law = aliased(Law)
    near_law = source_law if outbound else target_law
    far_law = target_law if outbound else source_law

    stmt = (
        select(
            far_law.id.label("law_id"),
            far_law.title.label("title"),
            far_law.frbr_work_uri.label("frbr_work_uri"),
            Jurisdiction.code.label("jurisdiction_code"),
            CrossReference.edge_class.label("edge_class"),
            func.count().label("cnt"),
        )
        .select_from(CrossReference)
        .join(source_p, source_p.id == CrossReference.source_provision_id)
        .join(target_p, target_p.id == CrossReference.target_provision_id)
        .join(source_v, source_v.id == source_p.version_id)
        .join(target_v, target_v.id == target_p.version_id)
        .join(source_law, source_law.id == source_v.law_id)
        .join(target_law, target_law.id == target_v.law_id)
        .join(Jurisdiction, Jurisdiction.id == far_law.jurisdiction_id)
        .where(near_law.id == law_id)
        .where(source_law.id != target_law.id)  # exclude self-references
        .group_by(
            far_law.id,
            far_law.title,
            far_law.frbr_work_uri,
            Jurisdiction.code,
            CrossReference.edge_class,
        )
    )
    rows = (await session.execute(stmt)).all()
    grouped: dict[uuid.UUID, LawCitationCounterparty] = {}
    for row in rows:
        cp = grouped.get(row.law_id)
        if cp is None:
            cp = LawCitationCounterparty(
                law_id=row.law_id,
                title=row.title,
                frbr_work_uri=row.frbr_work_uri,
                jurisdiction_code=row.jurisdiction_code,
                ref_count=0,
                edge_class_counts={},
            )
            grouped[row.law_id] = cp
        cp.ref_count += row.cnt
        cp.edge_class_counts[row.edge_class] = cp.edge_class_counts.get(row.edge_class, 0) + row.cnt
    return sorted(grouped.values(), key=lambda c: (-c.ref_count, c.title))


async def law_outbound_citations(
    session: AsyncSession, law_id: uuid.UUID
) -> list[LawCitationCounterparty]:
    """Other laws this law references, aggregated by counterparty. Excludes
    dangling refs (no held target provision) and self-references."""
    return await _law_citation_counterparties(session, law_id, direction="outbound")


async def law_inbound_citations(
    session: AsyncSession, law_id: uuid.UUID
) -> list[LawCitationCounterparty]:
    """Other laws that reference this law, aggregated by counterparty.
    Same exclusions as outbound."""
    return await _law_citation_counterparties(session, law_id, direction="inbound")


async def _amendment_history_for_unit(
    session: AsyncSession, unit: type[Provision] | type[Section], unit_id: uuid.UUID
) -> list[AmendmentRecord]:
    work_prefix = Law.frbr_work_uri.concat("/")
    stmt = (
        select(AmendmentEffect)
        .select_from(unit)
        .join(Version, Version.id == unit.version_id)
        .join(Law, Law.id == Version.law_id)
        .join(
            AmendmentEffect,
            and_(
                func.left(AmendmentEffect.target_frbr_uri, func.length(work_prefix)) == work_prefix,
                or_(
                    AmendmentEffect.target_akn_wid == unit.akn_wid,
                    func.split_part(AmendmentEffect.target_frbr_uri, "#", 2) == unit.akn_eid,
                ),
            ),
        )
        .where(unit.id == unit_id)
        .order_by(AmendmentEffect.authority_uri, AmendmentEffect.id)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_effect_to_record(ae) for ae in rows]


def _effect_to_record(ae: AmendmentEffect) -> AmendmentRecord:
    return AmendmentRecord(
        akn_category=ae.akn_category,
        akn_action=ae.akn_action,
        source_akn_wid=ae.source_akn_wid,
        target_frbr_uri=ae.target_frbr_uri,
        target_akn_wid=ae.target_akn_wid,
        quoted=ae.quoted,
        authority_uri=ae.authority_uri,
        mod_eid_ref=ae.mod_eid_ref,
    )


__all__ = [
    "AmendmentRecord",
    "CitationChain",
    "CitationNode",
    "LawAmendmentRecord",
    "LawCitationCounterparty",
    "amendment_history",
    "citation_chain",
    "law_amendment_history",
    "law_inbound_citations",
    "law_outbound_citations",
    "section_amendment_history",
]
