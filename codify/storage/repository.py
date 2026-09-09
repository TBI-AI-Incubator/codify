"""save_document / get_document, span all body tables atomically. Caller commits."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from lxml import etree
from sqlalchemy import delete, false, or_, select, text, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codify.akn import Document
from codify.akn._parser import carried_ids
from codify.akn._schema import parse_xml
from codify.akn.io import parse_akn
from codify.pipeline.formats.eu_directive import on_the_cpu_pool
from codify.search import TOKENISER_VERSION, tokenise_to_text
from codify.storage.jurisdictions import get_or_create_jurisdiction
from codify.storage.lexicon import record_terms_from_rows
from codify.storage.mappers import document_to_rows, rows_to_document
from codify.storage.models import (
    AmendmentEffect as AmendmentEffectRow,
)
from codify.storage.models import (
    CrossReference as CrossReferenceRow,
)
from codify.storage.models import (
    Law,
    LifecycleEventRow,
    Provision,
    Section,
    Version,
    VersionUnitEmbedding,
)
from codify.storage.title_tokens import refresh_law_title_tokens, title_tokens_for
from codify.storage.versions import RECOVERY_ORDER_SQL, retain_source, save_version_source_text

logger = structlog.get_logger()

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


class ForeignJurisdictionCollision(ValueError):
    """A work URI already names a law under a different jurisdiction.
    `laws.frbr_work_uri` is globally unique, so there is no per-jurisdiction row
    to fall back to: the write is refused rather than filed under the tenant who
    holds that URI.
    """

    def __init__(self, frbr_work_uri: str, jurisdiction_code: str) -> None:
        # Both arguments stay in `args`: DBOS pickles a step's error and rebuilds
        # it on replay from `args`, so folding them into one message string makes
        # recovery raise a TypeError in place of this refusal.
        super().__init__(frbr_work_uri, jurisdiction_code)
        self.frbr_work_uri = frbr_work_uri
        self.jurisdiction_code = jurisdiction_code

    def __str__(self) -> str:
        return (
            f"{self.frbr_work_uri} already names a law outside "
            f"{self.jurisdiction_code}; refusing to write"
        )


async def merge_law_title_translation(
    session: AsyncSession, law_id: uuid.UUID, language: str, title: str
) -> None:
    """Record a translated law title under its ISO-639-3 language. No-op on a
    blank title. Caller commits."""
    title = (title or "").strip()
    if not title:
        return
    law = (await session.execute(select(Law).where(Law.id == law_id))).scalar_one_or_none()
    if law is None:
        return
    # Reassign so SQLAlchemy tracks the JSONB mutation.
    law.title_translations = {**(law.title_translations or {}), language: title}
    await refresh_law_title_tokens(session, law)


def _akn_root(akn_xml: str) -> etree._Element | None:
    if not akn_xml:
        return None
    try:
        return parse_xml(akn_xml)
    except etree.XMLSyntaxError:
        return None


def _extract_long_title(akn_xml: str) -> str | None:
    """Prefer the AKN preface's `<longTitle>` over the caller's placeholder."""
    root = _akn_root(akn_xml)
    if root is None:
        return None
    long_title = root.find(f".//{{{_AKN_NS}}}longTitle")
    if long_title is None:
        return None
    text = "".join(long_title.itertext())
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned or None


def short_title_from_akn(akn_xml: str, title: str, jurisdiction_code: str) -> str | None:
    """Derive a short title using the same rule for selection and persistence."""
    from codify.pipeline.enrich.titles import (
        declares_any_rule,
        markup_short_title,
        resolve_short_title,
    )

    country = (jurisdiction_code or "").lower()
    # Skip full-text extraction when only the markup rule applies.
    if not declares_any_rule(country):
        return markup_short_title(akn_xml)
    root = _akn_root(akn_xml)
    body_text = re.sub(r"\s+", " ", "".join(root.itertext())) if root is not None else ""
    return resolve_short_title(title=title, body_text=body_text, country=country, akn_xml=akn_xml)


@dataclass(frozen=True)
class SaveOutcome:
    """What `save_document` did, so a caller can tell a store from a reuse. Three of
    the four return paths hand back a version that already existed, discarding the
    freshly built AKN, which is correct for an idempotent re-ingest and invisible
    to anyone reading only the version id.
    """

    # None only where the row a caller named is gone and nothing replaced it in
    # that language: naming the id it asked for would send it to load a dead row.
    version_id: uuid.UUID | None
    stored: bool
    reason: str | None = None


async def _fill_gazette_if_null(
    session: AsyncSession, law_id: uuid.UUID, gazette: dict[str, Any]
) -> None:
    """Set a work's gazette only when it currently has none, at the database, so
    two concurrent ingests cannot both read NULL and race to overwrite it."""
    await session.execute(
        text("UPDATE laws SET gazette = CAST(:g AS jsonb) WHERE id = :id AND gazette IS NULL"),
        {"g": json.dumps(gazette), "id": law_id},
    )


async def fill_short_title_for_version(session: AsyncSession, version_id: uuid.UUID) -> str | None:
    """Fill a missing name from the canonical source; the caller commits.

    The law-title trigger invalidates title tokens when the name changes."""
    # Reused-law inserts also take this lock before returning to their caller.
    # Lock before choosing the source so a waiting call reads a fresh snapshot.
    await session.execute(
        text(
            "SELECT id FROM laws WHERE id = (SELECT law_id FROM versions WHERE id = :vid)"
            " FOR NO KEY UPDATE"
        ),
        {"vid": version_id},
    )
    row = (
        await session.execute(
            text(
                """
                SELECT l.id, l.title, j.code, v.akn_xml
                FROM versions v
                JOIN laws l ON l.id = v.law_id
                JOIN jurisdictions j ON j.id = l.jurisdiction_id
                WHERE v.id = :vid AND l.short_title IS NULL
                  AND v.id = (
                      SELECT v2.id FROM versions v2
                      WHERE v2.law_id = l.id
                        AND v2.parent_version_id IS NULL
                      ORDER BY v2.expression_date DESC, v2.ingested_at DESC, v2.id ASC
                      LIMIT 1
                  )
                """
            ),
            {"vid": version_id},
        )
    ).first()
    if row is None:
        return None
    short_title = await on_the_cpu_pool(
        short_title_from_akn, row[3] or "", row[1] or "", row[2] or ""
    )
    if not short_title:
        return None
    written = await session.execute(
        text(
            """
            UPDATE laws SET short_title = :s
            WHERE id = :id AND short_title IS NULL
            """
        ),
        # Preserve a name filled by another writer.
        {"s": short_title, "id": row[0]},
    )
    # Report only a write made by this call.
    return short_title if written.rowcount else None


async def _fill_short_title_if_null(
    session: AsyncSession, law_id: uuid.UUID, short_title: str
) -> None:
    """Give an existing work its short name without overwriting one it has."""
    await session.execute(
        text("UPDATE laws SET short_title = :s WHERE id = :id AND short_title IS NULL"),
        {"s": short_title, "id": law_id},
    )


def _ids_in(akn_xml: str | None) -> set[str] | None:
    """Every id the source document carries, or None when there is no source."""
    if not akn_xml:
        return None
    try:
        return carried_ids(parse_xml(akn_xml))
    except (ValueError, etree.XMLSyntaxError):
        return None


async def save_document_reporting(
    session: AsyncSession,
    doc: Document,
    *,
    jurisdiction_code: str,
    law_title: str,
    doctype: str = "act",
    year: int | None = None,
    number: str | None = None,
    akn_xml: str = "",
    parent_version_id: uuid.UUID | None = None,
    source_sha256: str | None = None,
    ocr_source: str | None = None,
    ocr_model: str | None = None,
    source_akn_sha256: str | None = None,
    ingest_run_id: uuid.UUID | None = None,
    source_text: str | None = None,
    source_legibility: float | None = None,
    gazette: dict[str, Any] | None = None,
) -> SaveOutcome:
    """Persist a Document atomically, reporting whether it stored the parse.

    `save_document` is the same call for a caller that wants only the id."""
    jurisdiction = await get_or_create_jurisdiction(session, jurisdiction_code)

    # Prefer the AKN preface's <longTitle> over the caller's placeholder.
    # Bulk ingesters may pass a placeholder; prefer the AKN designation for
    # retrieval (for example, the directive's full title).
    effective_title = await on_the_cpu_pool(_extract_long_title, akn_xml) or law_title
    try:
        short_title = await on_the_cpu_pool(
            short_title_from_akn, akn_xml, effective_title, jurisdiction_code
        )
    except Exception:  # noqa: BLE001 - a display name is not worth a failed ingest
        logger.error(
            "short_title_extraction_failed", frbr_work_uri=doc.frbr_work_uri, exc_info=True
        )
        short_title = None
    # Only source expressions may name the law.
    if parent_version_id is not None:
        short_title = None

    # Get-or-create law on frbr_work_uri. The column is globally unique, so a
    # match under another jurisdiction is not a row to reuse: honouring it would
    # file this version under that tenant's law.
    existing = (
        await session.execute(select(Law).where(Law.frbr_work_uri == doc.frbr_work_uri))
    ).scalar_one_or_none()
    if existing is not None and existing.jurisdiction_id != jurisdiction.id:
        raise ForeignJurisdictionCollision(doc.frbr_work_uri, jurisdiction_code)
    if existing is None:
        law = Law(
            jurisdiction_id=jurisdiction.id,
            title=effective_title,
            short_title=short_title,
            # Derived here rather than through `refresh_law_title_tokens`: no
            # version row exists yet for it to resolve a language from.
            title_tokens=title_tokens_for(
                effective_title, language=doc.language, short_title=short_title
            ),
            title_search_pipeline_version=TOKENISER_VERSION,
            doctype=doctype,
            year=year,
            number=number,
            frbr_work_uri=doc.frbr_work_uri,
            gazette=gazette,
        )
        session.add(law)
        await session.flush()
        reuse_law_id: uuid.UUID | None = None
    else:
        law = existing
        # Fill a missing gazette value atomically so concurrent ingests cannot
        # clobber one another. Re-apply it after a dedup-race rollback.
        reuse_law_id = law.id
        if gazette is not None:
            await _fill_gazette_if_null(session, reuse_law_id, gazette)

    # Same source and language means an idempotent re-ingest. The content hash
    # is the stable identity even though the expression URI includes a date.
    if source_sha256 is not None:
        dup = (
            await session.execute(
                select(Version).where(
                    Version.law_id == law.id,
                    Version.language == doc.language,
                    Version.source_sha256 == source_sha256,
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            await retain_source(session, dup.id, source_text, source_legibility)
            return SaveOutcome(dup.id, stored=False, reason="identical_source")

    (
        version,
        sections,
        provisions,
        cross_refs,
        amendment_effects,
        lifecycle_events,
    ) = document_to_rows(doc, law_id=law.id, known_ids=_ids_in(akn_xml))
    version.akn_xml = akn_xml
    version.source_sha256 = source_sha256
    version.ingest_run_id = ingest_run_id
    version.ocr_source = ocr_source
    version.ocr_model = ocr_model
    version.source_akn_sha256 = source_akn_sha256
    version.source_legibility = source_legibility
    if parent_version_id is not None:
        version.parent_version_id = parent_version_id
        # A child describes its parent's structure, so it inherits the refusal.
        # Without this a translation of a refused document grades clean beside a
        # blocking parent, and the column's clearing rule is bypassed by any child.
        version.structure_halt = (
            await session.execute(
                select(Version.structure_halt).where(Version.id == parent_version_id)
            )
        ).scalar_one_or_none()

    # Get-or-return on expression_uri: a same-day re-ingest hits the unique
    # constraint, and the existing version_id is the correct idempotent answer.
    existing_version = (
        await session.execute(
            select(Version).where(Version.expression_uri == version.expression_uri)
        )
    ).scalar_one_or_none()
    if existing_version is not None:
        await retain_source(session, existing_version.id, source_text, source_legibility)
        return SaveOutcome(existing_version.id, stored=False, reason="expression_uri_exists")

    expr_uri = version.expression_uri
    try:
        # Savepoint, so losing the race undoes only this insert. A bare rollback took
        # the caller's whole transaction with it, including the law and the reuse-path
        # fills, which is why the fills used to be re-applied by hand here.
        async with session.begin_nested():
            session.add(version)
            await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":  # not a unique violation, real error
            raise
        # Concurrent ingest won the dedup/expression_uri race, return its version.
        winner = (
            await session.execute(select(Version).where(Version.expression_uri == expr_uri))
        ).scalar_one_or_none()
        if winner is not None:
            logger.info("save_document_dedup_race", expression_uri=expr_uri)
            return SaveOutcome(winner.id, stored=False, reason="concurrent_race")
        raise

    # Only a stored canonical source may fill the name. Reused-law saves must
    # acquire the fill lock here before returning to the committing caller.
    if reuse_law_id is not None and await fill_short_title_for_version(session, version.id):
        await session.refresh(law)
        await refresh_law_title_tokens(session, law)

    if source_text is not None:
        await save_version_source_text(session, version.id, source_text)

    await _persist_derived_rows(
        session, sections, provisions, cross_refs, amendment_effects, lifecycle_events
    )
    return SaveOutcome(version.id, stored=True)


async def save_document(
    session: AsyncSession,
    doc: Document,
    *,
    jurisdiction_code: str,
    law_title: str,
    doctype: str = "act",
    year: int | None = None,
    number: str | None = None,
    akn_xml: str = "",
    parent_version_id: uuid.UUID | None = None,
    source_sha256: str | None = None,
    ocr_source: str | None = None,
    ocr_model: str | None = None,
    source_akn_sha256: str | None = None,
    source_text: str | None = None,
    source_legibility: float | None = None,
    gazette: dict[str, Any] | None = None,
) -> uuid.UUID:
    """The version id alone. Callers that need to know whether the parse was
    stored or an existing version reused want `save_document_reporting`."""
    outcome = await save_document_reporting(
        session,
        doc,
        jurisdiction_code=jurisdiction_code,
        law_title=law_title,
        doctype=doctype,
        year=year,
        number=number,
        akn_xml=akn_xml,
        parent_version_id=parent_version_id,
        source_sha256=source_sha256,
        ocr_source=ocr_source,
        ocr_model=ocr_model,
        source_akn_sha256=source_akn_sha256,
        source_text=source_text,
        source_legibility=source_legibility,
        gazette=gazette,
    )
    # Only a compare returns no version, and this wrapper never asks for one.
    if outcome.version_id is None:
        raise ValueError("save_document named no version")
    return outcome.version_id


async def _persist_derived_rows(
    session: AsyncSession,
    sections: list[Section],
    provisions: list[Provision],
    cross_refs: list[CrossReferenceRow],
    amendment_effects: list[AmendmentEffectRow],
    lifecycle_events: list[LifecycleEventRow],
) -> None:
    """Insert the derived rows in FK order, resolving intra-doc cross-refs."""
    if sections:
        session.add_all(sections)
        await session.flush()
    if provisions:
        session.add_all(provisions)
        await session.flush()
        # Every path that writes provisions passes through here (ingest,
        # translation, rederive), so new vocabulary joins the expansion lexicon
        # once, rather than each caller having to remember.
        await record_terms_from_rows(session, provisions)
    if cross_refs:
        # Resolve eligible local leaves while retaining the citation for reassessment.
        provision_by_eid = {p.akn_eid: p for p in provisions if p.excluded_from_pool is not True}
        for ref in cross_refs:
            if ref.target_uri and ref.target_uri.startswith("#"):
                eid = ref.target_uri[1:]
                if eid in provision_by_eid:
                    ref.target_provision_id = provision_by_eid[eid].id
        session.add_all(cross_refs)
        await session.flush()
    if amendment_effects:
        session.add_all(amendment_effects)
        await session.flush()
    if lifecycle_events:
        session.add_all(lifecycle_events)
        await session.flush()


def _effect_identity(row: AmendmentEffectRow) -> tuple[Any, ...]:
    """Exact document effect identity; external dates never migrate to a changed effect."""
    return (
        row.source_akn_wid,
        row.target_frbr_uri,
        row.target_akn_wid,
        row.akn_category,
        row.akn_action,
        row.authority_uri,
        row.mod_eid_ref,
        json.dumps(row.quoted, sort_keys=True),
    )


async def rederive_version_rows(session: AsyncSession, version_id: uuid.UUID, akn_xml: str) -> None:
    """Replace a version's derived section, provision and cross-ref rows from repaired
    AKN. In-place repair only updates ``versions.akn_xml``, so without this the
    ``provisions`` rows go stale. ``embedded_at`` is cleared, making a failed
    re-embed a visible gap rather than a false positive. Caller re-embeds and
    commits.
    """
    version = (
        await session.execute(select(Version).where(Version.id == version_id))
    ).scalar_one_or_none()
    if version is None:
        return
    doc = parse_akn(akn_xml)
    # document_to_rows mints a throwaway version; the derived rows' internal FKs
    # (section_id, source/target provision) are self-consistent, so we only
    # repoint version_id onto the existing row.
    _new_version, sections, provisions, cross_refs, amendment_effects, lifecycle_events = (
        document_to_rows(doc, law_id=version.law_id, known_ids=_ids_in(akn_xml))
    )
    for row in (*sections, *provisions, *amendment_effects, *lifecycle_events):
        row.version_id = version_id

    # The row's language wins over the AKN's FRBRlanguage, wrong for 959 EU
    # versions (`bul` over English text). `document_to_rows` tokenised against
    # the AKN, so redo it where they disagree: otherwise a rederive stamps rows
    # current with tokens from the wrong language and nothing revisits them.
    if doc.language != version.language:
        logger.warning(
            "rederive_language_mismatch",
            version_id=str(version_id),
            akn_language=doc.language,
            row_language=version.language,
        )
        for provision in provisions:
            provision.search_tokens = tokenise_to_text(provision.text, version.language)

    # `cross_references` and `provision_embeddings` cascade with their
    # provisions, which also removes INBOUND refs from other versions. Rederive
    # rebuilds intra-document refs only, so cross-version resolution around a
    # repaired law is lost until a resolve-references pass runs.
    vid = version_id
    # The AKN parser never supplies these fields. They identify an external
    # overlay, including legacy sidecars without publisher IDs. Keep the row
    # and its metadata even if its target disappears; the timeline reports a
    # missing-target hole. Unannotated legacy rows have no such evidence and
    # remain ordinary derived rows. Caller rollback covers both sets.
    external = or_(
        AmendmentEffectRow.publisher_effect_id.is_not(None),
        AmendmentEffectRow.in_force_date.is_not(None),
        AmendmentEffectRow.applied.is_not(None),
    )
    retained = list(
        (
            await session.execute(
                select(AmendmentEffectRow).where(AmendmentEffectRow.version_id == vid, external)
            )
        ).scalars()
    )
    retained_keys = {_effect_identity(row) for row in retained}
    amendment_effects = [
        row for row in amendment_effects if _effect_identity(row) not in retained_keys
    ]
    await session.execute(
        delete(AmendmentEffectRow).where(AmendmentEffectRow.version_id == vid, ~external)
    )
    await session.execute(delete(LifecycleEventRow).where(LifecycleEventRow.version_id == vid))
    await session.execute(delete(Provision).where(Provision.version_id == vid))
    await session.execute(delete(Section).where(Section.version_id == vid))
    # The comparator cache keys on version_id (unchanged by in-place repair) and
    # only upserts misses, so it would keep serving pre-repair vectors, drop it
    # so the next compare re-warms from the repaired text.
    await session.execute(
        delete(VersionUnitEmbedding).where(VersionUnitEmbedding.version_id == vid)
    )
    await session.flush()

    await _persist_derived_rows(
        session, sections, provisions, cross_refs, amendment_effects, lifecycle_events
    )
    # Table rows and the codes read off them derive from the same AKN. Left to
    # the ingest step alone, a repair or a re-OCR would leave removed rows
    # searchable and new ones absent.
    from codify.storage.annex_rows import index_version_annex_rows
    from codify.storage.goods_codes import index_version_goods_codes

    await index_version_annex_rows(session, version_id, akn_xml)  # noqa: RUF006
    await index_version_goods_codes(session, version_id, akn_xml)
    version.embedded_at = None


async def lock_and_count_dependents(
    session: AsyncSession, work_uri: str | None = None, *, law_id: uuid.UUID | None = None
) -> tuple[int, int]:
    """Lock the law (by work URI or id) and its versions, then count translations
    and lens runs. A dependent references a version, so the version locks are
    what hold a concurrent insert until the caller's delete commits."""
    where = "l.id = :key" if law_id is not None else "l.frbr_work_uri = :key"
    key: object = law_id if law_id is not None else work_uri
    # `where` is one of two literals above, never caller input.
    lock_law = f"SELECT l.id FROM laws l WHERE {where} FOR UPDATE"  # noqa: S608
    lock_versions = (
        f"SELECT v.id FROM versions v JOIN laws l ON l.id = v.law_id WHERE {where} FOR UPDATE OF v"  # noqa: S608
    )
    count = (
        "SELECT count(DISTINCT t.id) AS translations, count(DISTINCT lr.id) AS lens_runs "  # noqa: S608
        "FROM laws l JOIN versions v ON v.law_id = l.id "
        "LEFT JOIN versions t ON t.parent_version_id = v.id "
        f"LEFT JOIN lens_runs lr ON lr.version_id = v.id WHERE {where}"
    )
    await session.execute(text(lock_law), {"key": key})
    await session.execute(text(lock_versions), {"key": key})
    row = (await session.execute(text(count), {"key": key})).one()
    return int(row.translations), int(row.lens_runs)


async def supersede_document(
    session: AsyncSession,
    doc: Document,
    *,
    jurisdiction_code: str,
    law_title: str,
    doctype: str = "act",
    year: int | None = None,
    number: str | None = None,
    akn_xml: str = "",
    parent_version_id: uuid.UUID | None = None,
    source_sha256: str | None = None,
    ocr_source: str | None = None,
    ocr_model: str | None = None,
    source_akn_sha256: str | None = None,
    ingest_run_id: uuid.UUID | None = None,
    selected_version_id: uuid.UUID | None = None,
    source_text: str | None = None,
    source_legibility: float | None = None,
    gazette: dict[str, Any] | None = None,
) -> SaveOutcome:
    """Replace any existing law at ``doc.frbr_work_uri`` with this document, the
    sanctioned re-ingest and correction path.

    ``save_document`` is idempotent on ``expression_uri`` and ``versions`` rows
    are immutable, so a correction cannot update in place: it deletes then saves,
    superseding the expression with no prior-version history kept. The law delete
    cascades through every child FK on its own. Caller commits.
    """
    # Lock by the key, then read: locking a row id read outside the lock locks
    # nothing when another supersede replaced the law in between, and every
    # query after it would address a law id that no longer exists.
    if selected_version_id is not None:
        await lock_and_count_dependents(session, doc.frbr_work_uri)
    existing = (
        await session.execute(select(Law).where(Law.frbr_work_uri == doc.frbr_work_uri))
    ).scalar_one_or_none()
    if existing is not None and (source_sha256 is not None or ingest_run_id is not None):
        # Superseding with the source already held is a no-op, which is what
        # makes the path repeatable: a step result is recorded after its body
        # returns, so a crash before that replays the write and would otherwise
        # delete and rebuild the law under new ids. A real correction carries
        # different bytes, so a different hash, and still supersedes.
        repeat = (
            await session.execute(
                select(Version.id)
                .where(
                    Version.law_id == existing.id,
                    Version.language == doc.language,
                    # A hashless write leaves the run id as its only mark.
                    Version.source_sha256 == source_sha256 if source_sha256 is not None else true(),
                    # Only this run's own write is a repeat; any earlier row is
                    # recovery and is replaced, halted or not.
                    # This run's own write is a replay. With a selected row the
                    # compare below decides the rest, and says which case it was.
                    Version.ingest_run_id == ingest_run_id
                    if ingest_run_id
                    else (false() if selected_version_id else Version.structure_halt.is_(None)),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if repeat is not None:
            logger.info(
                "supersede_skipped_identical_source",
                frbr_work_uri=doc.frbr_work_uri,
                law_id=str(existing.id),
            )
            existing = None  # nothing to replace; fall through to the save path
    # Compare and replace: the caller selected a row, possibly minutes ago in a
    # preview. Deleting a law on a stale snapshot destroys whatever replaced or
    # recovered it since, so the row is re-read here, inside the write.
    if existing is not None and selected_version_id is not None:
        # The latest row for this law and language by the resolver's own ordering,
        # not the latest sharing the selected row's bytes: a clean re-ingest from
        # different bytes leaves the refused row matching, and the delete would
        # take the newer clean version with the law.
        # The selected row's own language, not the parse's: the PDF lane derives
        # language from the document, so a detection that moved would compare a
        # language the selection never named and find nothing to replace.
        selected_row = (
            await session.execute(select(Version.language).where(Version.id == selected_version_id))
        ).first()
        if selected_row is None:
            # Falling back to the parse's language would compare against whatever
            # the replacement law holds and name an unrelated version, which the
            # caller then embeds as this run's result.
            logger.info(
                "supersede_skipped_selection_gone",
                frbr_work_uri=doc.frbr_work_uri,
                selected=str(selected_version_id),
            )
            return SaveOutcome(None, stored=False, reason="superseded_elsewhere")
        selected_language = selected_row[0]
        current = (
            await session.execute(
                select(Version.id, Version.structure_halt)
                .where(Version.law_id == existing.id, Version.language == selected_language)
                .order_by(text(RECOVERY_ORDER_SQL))
                .limit(1)
            )
        ).first()
        if current is None or current[0] != selected_version_id:
            logger.info(
                "supersede_skipped_superseded_elsewhere",
                frbr_work_uri=doc.frbr_work_uri,
                selected=str(selected_version_id),
            )
            # `current[0] if current else ...` would hand back the selected id when
            # the language is gone, and the caller loads that row and fails on a
            # version that no longer exists. There is nothing to name, so name none.
            return SaveOutcome(
                current[0] if current else None,
                stored=False,
                reason="superseded_elsewhere",
            )
        if current[1] is None:
            logger.info(
                "supersede_skipped_halt_cleared",
                frbr_work_uri=doc.frbr_work_uri,
                version_id=str(selected_version_id),
            )
            return SaveOutcome(current[0], stored=False, reason="halt_cleared")
    if existing is not None:
        # The delete is the whole law and cascades. Checking the jurisdiction
        # first matters more here than on the save path: reusing a foreign row
        # appends, deleting one destroys.
        jurisdiction = await get_or_create_jurisdiction(session, jurisdiction_code)
        if existing.jurisdiction_id != jurisdiction.id:
            raise ForeignJurisdictionCollision(doc.frbr_work_uri, jurisdiction_code)
        await session.execute(delete(Law).where(Law.id == existing.id))
        await session.flush()
        logger.info(
            "superseded_document",
            frbr_work_uri=doc.frbr_work_uri,
            prior_law_id=str(existing.id),
        )
    return await save_document_reporting(
        session,
        doc,
        jurisdiction_code=jurisdiction_code,
        law_title=law_title,
        doctype=doctype,
        year=year,
        number=number,
        akn_xml=akn_xml,
        parent_version_id=parent_version_id,
        source_sha256=source_sha256,
        ocr_source=ocr_source,
        ocr_model=ocr_model,
        source_akn_sha256=source_akn_sha256,
        source_text=source_text,
        source_legibility=source_legibility,
        gazette=gazette,
        ingest_run_id=ingest_run_id,
    )


async def get_document(
    session: AsyncSession,
    law_id: uuid.UUID,
    version_id: uuid.UUID | None = None,
) -> Document | None:
    """Reconstruct a Document for the given law (latest version if unset)."""
    if version_id is None:
        version = (
            await session.execute(
                select(Version)
                .where(Version.law_id == law_id)
                .order_by(Version.expression_date.desc(), Version.ingested_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    else:
        version = (
            await session.execute(select(Version).where(Version.id == version_id))
        ).scalar_one_or_none()
    if version is None:
        return None

    # Trust version.law_id over the caller-supplied law_id, they may diverge
    # if a wrong (law_id, version_id) pair is passed.
    law = (await session.execute(select(Law).where(Law.id == version.law_id))).scalar_one()

    sections = list(
        (
            await session.execute(
                select(Section).where(Section.version_id == version.id).order_by(Section.position)
            )
        ).scalars()
    )
    provisions = list(
        (
            await session.execute(
                select(Provision)
                .where(Provision.version_id == version.id)
                .order_by(Provision.position)
            )
        ).scalars()
    )
    if not provisions:
        cross_refs: list[CrossReferenceRow] = []
    else:
        provision_ids = [p.id for p in provisions]
        cross_refs = list(
            (
                await session.execute(
                    select(CrossReferenceRow).where(
                        CrossReferenceRow.source_provision_id.in_(provision_ids)
                    )
                )
            ).scalars()
        )

    amendment_effects = list(
        (
            await session.execute(
                select(AmendmentEffectRow).where(AmendmentEffectRow.version_id == version.id)
            )
        ).scalars()
    )
    lifecycle_events = list(
        (
            await session.execute(
                select(LifecycleEventRow)
                .where(LifecycleEventRow.version_id == version.id)
                .order_by(LifecycleEventRow.event_date)
            )
        ).scalars()
    )

    return rows_to_document(
        version,
        sections,
        provisions,
        cross_refs,
        frbr_work_uri=law.frbr_work_uri,
        amendment_effects=amendment_effects,
        lifecycle_events=lifecycle_events,
    )


__all__ = ["get_document", "save_document", "supersede_document"]
