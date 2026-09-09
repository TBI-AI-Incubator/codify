"""AKN Document ↔ DB rows. Containers → sections, leaves → provisions.
Lossy: inline-ref offsets and container-level text are dropped."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Literal, cast

import structlog

from codify.akn import (
    AmendmentReference,
    Article,
    Chapter,
    Citation,
    CrossReference,
    Document,
    Paragraph,
    Point,
    Section,
    Subparagraph,
    Title,
)
from codify.akn.analysis import (
    LifecycleEvent as DomainLifecycleEvent,
)
from codify.akn.analysis import (
    QuotedContent,
    TextualMod,
)
from codify.akn.elements import BodyElement, ElementBase
from codify.akn.references import InlineReference
from codify.jurisdictions import placeholder_markers_for
from codify.quality.sentinels import exclusion_reason
from codify.search import TOKENISER_VERSION, tokenise_to_text
from codify.storage.models import (
    AmendmentEffect as AmendmentEffectRow,
)
from codify.storage.models import (
    CrossReference as CrossReferenceRow,
)
from codify.storage.models import (
    LifecycleEventRow,
)
from codify.storage.models import (
    Provision as ProvisionRow,
)
from codify.storage.models import (
    Section as SectionRow,
)
from codify.storage.models import (
    Version as VersionRow,
)

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = structlog.get_logger()

# AKN preserves XML pretty-print whitespace inside <p>…</p>; collapse runs
# of whitespace to single spaces and strip.
_WS_RUN = re.compile(r"\s+")


def _normalise(text: str) -> str:
    if not text:
        return ""
    return _WS_RUN.sub(" ", text).strip()


ROW_SECTION_KINDS = {"title", "chapter", "section", "article"}
LEAF_KINDS = {"paragraph", "subparagraph", "point"}

_KIND_TO_DOMAIN: dict[str, type[ElementBase]] = {
    "title": Title,
    "chapter": Chapter,
    "section": Section,
    "article": Article,
    "paragraph": Paragraph,
    "subparagraph": Subparagraph,
    "point": Point,
}


def _attachment_is_normative(doc: Document, heading: str | None) -> bool:
    """Whether an attachment carries norm. AKN cannot separate a Penjelasan
    from a Lampiran, so the jurisdiction declares which of its captions do."""
    from codify.akn.frbr import country_of
    from codify.jurisdictions import try_load_config

    country = country_of(doc.frbr_work_uri)
    # Probed, not required: without a config every attachment is normative,
    # which is the same answer the declaration gives for most of them.
    config = try_load_config(country) if country else None
    if config is None or not heading:
        return True
    # Anchored to the head of the heading: a substring match reads "LAMPIRAN II
    # PENJELASAN TEKNIS" as an elucidation and strips a schedule of its force.
    upper = heading.upper().lstrip("0123456789 -.\t")
    return next(
        (
            a.normative
            for a in config.attachments
            if a.caption and upper.startswith(a.caption.upper())
        ),
        True,
    )


def _exclusion(text: str, akn_eid: str, markers: tuple[str, ...]) -> dict[str, object]:
    reason = exclusion_reason(text, akn_eid, markers)
    return {"excluded_from_pool": reason is not None, "exclusion_reason": reason}


def document_to_rows(
    doc: Document,
    law_id: uuid.UUID,
    known_ids: set[str] | None = None,
) -> tuple[
    VersionRow,
    list[SectionRow],
    list[ProvisionRow],
    list[CrossReferenceRow],
    list[AmendmentEffectRow],
    list[LifecycleEventRow],
]:
    """Flatten a Document into row tuples. Caller resolves intra-doc target ids post-flush.

    `known_ids` is every name the source document identifies an element by. Given,
    a link the document arrived with naming none of them is dropped rather than
    rowed; withheld, nothing is dropped, because there is nothing to check.
    """
    version = VersionRow(
        law_id=law_id,
        expression_uri=doc.frbr_expression_uri,
        language=doc.language,
        expression_date=doc.expression_date,
        akn_xml="",
    )

    sections: list[SectionRow] = []
    provisions: list[ProvisionRow] = []
    markers = placeholder_markers_for(doc.frbr_work_uri)
    cross_refs: list[CrossReferenceRow] = []
    dropped_anchors: list[str] = []

    # Nested points flatten to siblings under their article (v2 schema has no
    # point>point), so a point's own `position` is local to its parent and would
    # collide with its uncles. `_walk` already appends in document pre-order, so
    # number each section's provisions by append order to keep reading order.
    section_pos: dict[uuid.UUID | None, int] = {}

    def _next_pos(section_id: uuid.UUID | None) -> int:
        pos = section_pos.get(section_id, 0)
        section_pos[section_id] = pos + 1
        return pos

    def _walk(
        element: BodyElement,
        parent_section_id: uuid.UUID | None,
        normative: bool = True,
    ) -> None:
        if element.kind in ROW_SECTION_KINDS:
            section_row = SectionRow(
                version_id=version.id,
                parent_section_id=parent_section_id,
                akn_eid=element.akn_eid,
                akn_wid=element.akn_wid,
                akn_type=element.akn_type,
                title=element.heading,
                position=element.position,
            )
            sections.append(section_row)
            # AKN allows containers (e.g. <article>) to carry body text directly
            # via `<content><p>...</p></content>` without an enclosing
            # paragraph element. The parser flattens that into `element.text`.
            # Materialise it as a synthetic Provision child so the row split
            # captures the body, otherwise articles render as empty headings
            # in /laws/:id and the comparator has nothing to align against.
            has_leaf_children = any(c.kind in LEAF_KINDS for c in element.children)
            if element.text.strip() and not has_leaf_children:
                provisions.append(
                    ProvisionRow(
                        version_id=version.id,
                        section_id=section_row.id,
                        akn_eid=f"{element.akn_eid}__content",
                        akn_wid=f"{element.akn_wid}__content",
                        akn_type="paragraph",
                        text=_normalise(element.text),
                        position=_next_pos(section_row.id),
                        normative=normative,
                        **_exclusion(element.text, f"{element.akn_eid}", markers),
                    )
                )
            elif element.text.strip():
                # Container has both inline lead-in text AND leaf children
                # (e.g. "The Authority shall: (a)… (b)…"). Keep the lead-in as a
                # position-0 provision so it isn't dropped ahead of the points.
                provisions.append(
                    ProvisionRow(
                        version_id=version.id,
                        section_id=section_row.id,
                        akn_eid=f"{element.akn_eid}__intro",
                        akn_wid=f"{element.akn_wid}__intro",
                        akn_type="paragraph",
                        text=_normalise(element.text),
                        position=_next_pos(section_row.id),
                        normative=normative,
                        **_exclusion(element.text, f"{element.akn_eid}", markers),
                    )
                )
            for child in element.children:
                _walk(child, parent_section_id=section_row.id, normative=normative)
        elif element.kind in LEAF_KINDS:
            # A grouping point carries its lead-in in `intro` (and any tail in
            # `wrap_up`) with an empty `text`; fold both in so the lead-in isn't
            # dropped, otherwise a first sub-point the body-fill merged into the
            # lead-in (e.g. "أ- …التالية:- ١- …") would vanish entirely.
            own_text = "\n".join(
                p for p in (element.intro, element.text, element.wrap_up) if p.strip()
            )
            # Skip a truly empty point (a bare marker grouping sub-points, or a
            # mis-detected enumerator), its children still reparent below.
            if own_text.strip():
                provision_row = ProvisionRow(
                    version_id=version.id,
                    section_id=parent_section_id,
                    akn_eid=element.akn_eid,
                    akn_wid=element.akn_wid,
                    akn_type=element.akn_type,
                    text=_normalise(own_text),
                    position=_next_pos(parent_section_id),
                    normative=normative,
                    **_exclusion(own_text, element.akn_eid, markers),
                )
                provisions.append(provision_row)
                for ref in element.references:
                    if _anchor_to_nothing(ref, known_ids):
                        dropped_anchors.append(str(getattr(ref, "target_eid", "")))
                        continue
                    row = _ref_to_row(ref, source_provision_id=provision_row.id)
                    if row is not None:
                        cross_refs.append(row)
            # Nested leaves (point > indent) collapse to siblings in v2 schema.
            for child in element.children:
                _walk(child, parent_section_id=parent_section_id, normative=normative)
        else:
            raise ValueError(f"unknown element kind: {element.kind}")

    for top in doc.body:
        _walk(top, parent_section_id=None)
    # Indexed so search reaches it; operative unless the jurisdiction says not.
    # Positioned after the body: the reader orders sections by position alone,
    # and an attachment's own numbering restarts at zero, so leaving it there
    # interleaves an elucidation article between two chapters.
    next_top = max((s.position for s in sections if s.parent_section_id is None), default=-1) + 1
    for offset, top in enumerate(doc.attachments):
        _walk(
            top.model_copy(update={"position": next_top + offset}),
            parent_section_id=None,
            normative=_attachment_is_normative(doc, top.heading),
        )

    amendment_effects = [
        AmendmentEffectRow(
            version_id=version.id,
            source_akn_wid=mod.source_akn_wid,
            target_frbr_uri=mod.target_frbr_uri,
            target_akn_wid=mod.target_akn_wid,
            akn_category=mod.akn_category,
            akn_action=mod.akn_action,
            quoted=mod.quoted.model_dump(mode="json") if mod.quoted is not None else None,
            authority_uri=mod.authority_uri,
            mod_eid_ref=mod.mod_eid_ref,
        )
        for mod in doc.textual_mods
    ]

    from datetime import date as _date

    lifecycle_events = [
        LifecycleEventRow(
            version_id=version.id,
            event_date=_date.fromisoformat(ev.event_date),
            event_type=ev.event_type,
            source_uri=ev.source_uri,
            refers_uri=ev.refers_uri,
            originating_uri=ev.originating_uri,
        )
        for ev in doc.lifecycle_events
    ]

    # Lexical-arm tokens, in one pass rather than at each ProvisionRow site so a
    # new construction site cannot silently ship untokenised rows.
    for provision in provisions:
        provision.search_tokens = tokenise_to_text(provision.text, version.language)
        provision.search_pipeline_version = TOKENISER_VERSION

    if dropped_anchors:
        logger.info(
            "anchor_to_nothing_dropped",
            expression_uri=doc.frbr_expression_uri,
            count=len(dropped_anchors),
            ids=sorted(set(dropped_anchors))[:50],
        )
    return version, sections, provisions, cross_refs, amendment_effects, lifecycle_events


def _anchor_to_nothing(ref: InlineReference, known: set[str] | None) -> bool:
    """A link the document arrived with, naming a unit it does not carry.

    A publisher's rendering can key anchors to its own transform rather than to
    the legal structure; those name nothing here at any later date, and kept
    they are indistinguishable from a citation we failed to resolve. What our
    own passes mint is spared: they check a target as they write it.
    """
    if known is None:
        return False  # no document to check against, so nothing is refutable
    return (
        isinstance(ref, CrossReference)
        and ref.origin == "href"
        and bool(ref.target_eid)
        and ref.target_eid not in known
    )


def _ref_to_row(ref: InlineReference, source_provision_id: uuid.UUID) -> CrossReferenceRow | None:
    """None for target-less refs (empty href), the DB CHECK requires a target."""
    if isinstance(ref, Citation):
        if not ref.target_uri:
            return None
        return CrossReferenceRow(
            source_provision_id=source_provision_id,
            target_uri=ref.target_uri,
            ref_type="citation",
            edge_class="freetext_reference",
            # Recorded by the pass that read it, not inferred from the URI:
            # publisher AKN carries relative `/akn/...` hrefs of exactly the
            # shape our own passes mint.
            resolution_origin=ref.origin,
        )
    if isinstance(ref, CrossReference):
        # Intra-doc references carry an akn_eid; external ones a URI. The DB
        # has only target_uri, so encode an eId as `#<eid>` so the repository
        # can spot it and rewrite to target_provision_id post-flush.
        target = f"#{ref.target_eid}" if ref.target_eid else ref.target_uri
        if not target:
            return None
        return CrossReferenceRow(
            source_provision_id=source_provision_id,
            target_uri=target,
            ref_type="cross_reference",
            edge_class="freetext_reference",
            # Stored for an intra-document target too: read back from the
            # database, an unrecorded origin reads as the publisher's own link,
            # and the rule that spares our readings would stop sparing them.
            resolution_origin=ref.origin,
        )
    if isinstance(ref, AmendmentReference):
        return CrossReferenceRow(
            source_provision_id=source_provision_id,
            target_uri=ref.amends_uri,
            ref_type=f"amendment_{ref.operation}",
            edge_class="mod_textual",
            # Recorded by the pass that read it, not inferred from the URI:
            # publisher AKN carries relative `/akn/...` hrefs of exactly the
            # shape our own passes mint.
            resolution_origin=ref.origin,
        )
    raise TypeError(f"unknown inline reference type: {type(ref).__name__}")


def rows_to_document(
    version: VersionRow,
    sections: list[SectionRow],
    provisions: list[ProvisionRow],
    refs: list[CrossReferenceRow],
    *,
    frbr_work_uri: str,
    amendment_effects: list[AmendmentEffectRow] | None = None,
    lifecycle_events: list[LifecycleEventRow] | None = None,
) -> Document:
    """Reconstruct a Document from row tuples ordered by parent + position."""
    section_children: dict[uuid.UUID | None, list[SectionRow]] = {}
    for s in sections:
        section_children.setdefault(s.parent_section_id, []).append(s)
    for sec_kids in section_children.values():
        sec_kids.sort(key=lambda s: s.position)

    provisions_by_section: dict[uuid.UUID | None, list[ProvisionRow]] = {}
    for p in provisions:
        provisions_by_section.setdefault(p.section_id, []).append(p)
    for prov_kids in provisions_by_section.values():
        prov_kids.sort(key=lambda p: p.position)

    refs_by_source: dict[uuid.UUID, list[CrossReferenceRow]] = {}
    for r in refs:
        refs_by_source.setdefault(r.source_provision_id, []).append(r)

    def _build_section(row: SectionRow) -> BodyElement:
        cls = _KIND_TO_DOMAIN.get(row.akn_type)
        if cls is None or row.akn_type not in ROW_SECTION_KINDS:
            cls = Section  # fallback for unknown akn_type
        children: list[BodyElement] = []
        for sub in section_children.get(row.id, []):
            children.append(_build_section(sub))
        for prov in provisions_by_section.get(row.id, []):
            children.append(_build_provision(prov))
        return cast(
            BodyElement,
            cls(
                id=row.id,
                akn_eid=row.akn_eid,
                akn_wid=row.akn_wid,
                akn_type=row.akn_type,
                position=row.position,
                heading=row.title,
                text="",
                children=children,
                created_at=row.created_at,
                updated_at=row.updated_at,
            ),
        )

    def _build_provision(row: ProvisionRow) -> BodyElement:
        cls = _KIND_TO_DOMAIN.get(row.akn_type, Paragraph)
        return cast(
            BodyElement,
            cls(
                id=row.id,
                akn_eid=row.akn_eid,
                akn_wid=row.akn_wid,
                akn_type=row.akn_type,
                position=row.position,
                text=row.text,
                references=[_row_to_ref(r) for r in refs_by_source.get(row.id, [])],
                created_at=row.created_at,
                updated_at=row.updated_at,
            ),
        )

    body: list[BodyElement] = []
    for top in section_children.get(None, []):
        body.append(_build_section(top))
    for prov in provisions_by_section.get(None, []):
        body.append(_build_provision(prov))

    textual_mods = [_row_to_textual_mod(row) for row in (amendment_effects or [])]
    lifecycle_domain = [_row_to_lifecycle_event(row) for row in (lifecycle_events or [])]

    return Document(
        id=version.id,
        frbr_work_uri=frbr_work_uri,
        frbr_expression_uri=version.expression_uri,
        language=version.language,
        expression_date=version.expression_date,
        body=body,
        textual_mods=textual_mods,
        lifecycle_events=lifecycle_domain,
    )


def _row_to_textual_mod(row: AmendmentEffectRow) -> TextualMod:
    quoted: QuotedContent | None = None
    if row.quoted:
        quoted = QuotedContent.model_validate(row.quoted)
    return TextualMod(
        akn_category=row.akn_category,
        akn_action=row.akn_action,
        source_akn_wid=row.source_akn_wid,
        target_frbr_uri=row.target_frbr_uri,
        target_akn_wid=row.target_akn_wid,
        quoted=quoted,
        authority_uri=row.authority_uri,
        mod_eid_ref=row.mod_eid_ref,
    )


def _row_to_lifecycle_event(row: LifecycleEventRow) -> DomainLifecycleEvent:
    return DomainLifecycleEvent(
        event_date=row.event_date.isoformat(),
        event_type=row.event_type,
        source_uri=row.source_uri,
        refers_uri=row.refers_uri,
        originating_uri=row.originating_uri,
    )


def _row_to_ref(row: CrossReferenceRow) -> InlineReference:
    # Origin restored on every variant. Defaulting it here silently turned a
    # reading back into an authored link on the way out of the database, so a
    # document read back carried the opposite provenance to the one stored.
    origin = cast(Literal["href", "text", "registry"], row.resolution_origin or "href")
    if row.ref_type == "citation":
        return Citation(
            start_offset=0,
            end_offset=0,
            text_snippet="",
            target_uri=row.target_uri,
            origin=origin,
        )
    if row.ref_type == "cross_reference":
        # `#<eid>` encoding signals an intra-doc target; everything else is a URI.
        if row.target_uri and row.target_uri.startswith("#"):
            return CrossReference(
                start_offset=0,
                end_offset=0,
                text_snippet="",
                target_eid=row.target_uri[1:],
                origin=origin,
            )
        return CrossReference(
            start_offset=0,
            end_offset=0,
            text_snippet="",
            target_uri=row.target_uri,
            origin=origin,
        )
    if row.ref_type.startswith("amendment_"):
        op_str = row.ref_type.split("_", 1)[1]
        if op_str not in {"insert", "delete", "replace", "renumber"}:
            op_str = "replace"
        return AmendmentReference(
            start_offset=0,
            end_offset=0,
            text_snippet="",
            amends_uri=row.target_uri or "",
            operation=op_str,
            origin=origin,
        )
    raise ValueError(f"unknown ref_type: {row.ref_type}")


__all__ = [
    "ROW_SECTION_KINDS",
    "LEAF_KINDS",
    "document_to_rows",
    "rows_to_document",
]
