"""Version lookups + lineage + amendment-from-suggestion."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from types import EllipsisType
from typing import Any

from lxml import etree
from sqlalchemy import ARRAY, bindparam, func, or_, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from codify.akn.elements import ElementBase
from codify.storage.models import Jurisdiction, Law, Version
from codify.storage.schema_gate import gate_akn


# Recovery replaces a whole lineage, and the target is its source expression, so
# parentage leads: a translation or amendment takes today's date while the source
# keeps the legal one. The selector and the write compare share this one ordering,
# or they resolve to different rows and the write stores nothing.
def recovery_order_sql(prefix: str = "", *, id_column: str = "id") -> str:
    """The ordering, for a caller that must qualify or rename its columns. Restating
    the terms instead is what let the selector and the write resolve to different
    rows, so every site builds them here."""
    q = f"{prefix}." if prefix else ""
    return (
        f"({q}parent_version_id IS NULL) DESC, {q}expression_date DESC, "
        f"{q}ingested_at DESC, {q}{id_column} DESC"
    )


RECOVERY_ORDER_SQL = recovery_order_sql()

# Only leaf kinds carry literal `.text` an amendment is allowed to patch.
_LEAF_KINDS = frozenset({"article", "paragraph", "subparagraph", "point"})


async def get_version(session: AsyncSession, version_id: uuid.UUID) -> Version | None:
    result = await session.execute(select(Version).where(Version.id == version_id))
    return result.scalar_one_or_none()


async def count_versions_for_law(session: AsyncSession, law_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(select(func.count(Version.id)).where(Version.law_id == law_id))
        ).scalar_one()
    )


async def latest_version_for_law(
    session: AsyncSession, law_id: uuid.UUID, language: str | None = None
) -> Version | None:
    """Latest version by `expression_date DESC, ingested_at DESC`, preferring
    `language` and falling back to the original. A requested language narrows which
    expression answers, never whether the law answers: filtering on it returned
    nothing for an untranslated law, indistinguishable from the law being absent.
    """
    stmt = select(Version).where(Version.law_id == law_id)
    order = [Version.expression_date.desc(), Version.ingested_at.desc()]
    if language:
        stmt = stmt.where(or_(Version.language == language, Version.parent_version_id.is_(None)))
        order.insert(0, (Version.language == language).desc())
    else:
        stmt = stmt.where(Version.parent_version_id.is_(None))
    result = await session.execute(stmt.order_by(*order).limit(1))
    return result.scalar_one_or_none()


_VERSION_LINEAGE_DEPTH = 100


async def version_lineage(session: AsyncSession, version_id: uuid.UUID) -> list[Version]:
    """Ancestor chain, current first. Capped at 100."""
    rows = (
        (
            await session.execute(
                text(
                    f"""
                WITH RECURSIVE lineage(id, parent_version_id, depth) AS (
                    SELECT id, parent_version_id, 0 FROM versions WHERE id = :start
                    UNION ALL
                    SELECT v.id, v.parent_version_id, l.depth + 1
                    FROM versions v JOIN lineage l ON v.id = l.parent_version_id
                    WHERE l.depth < {_VERSION_LINEAGE_DEPTH}
                )
                SELECT v.* FROM versions v JOIN lineage l ON l.id = v.id
                ORDER BY l.depth ASC
                """  # noqa: S608
                ),
                {"start": version_id},
            )
        )
        .mappings()
        .all()
    )
    if len(rows) >= _VERSION_LINEAGE_DEPTH:
        import structlog

        structlog.get_logger().warning(
            "version_lineage_truncated", version_id=str(version_id), cap=_VERSION_LINEAGE_DEPTH
        )
    return [Version(**dict(r)) for r in rows]


async def list_versions(
    session: AsyncSession,
    law_id: uuid.UUID,
    *,
    limit: int = 100,
    cursor: uuid.UUID | None = None,
    with_akn: bool = True,
) -> tuple[list[Version], uuid.UUID | None]:
    """Cursor-paginated. Order: expression_date DESC, ingested_at DESC, id ASC.
    `with_akn=False` leaves the document body unloaded for a metadata listing."""
    stmt = (
        select(Version)
        .options(*([] if with_akn else [defer(Version.akn_xml)]))
        .where(Version.law_id == law_id)
        .order_by(
            Version.expression_date.desc(),
            Version.ingested_at.desc(),
            Version.id.asc(),
        )
        .limit(limit + 1)
    )
    if cursor is not None:
        # Scope the cursor lookup to this law: a stale or malicious cursor
        # from a different law shouldn't shift the window.
        cursor_row = (
            await session.execute(
                select(Version.expression_date, Version.ingested_at, Version.id).where(
                    Version.id == cursor, Version.law_id == law_id
                )
            )
        ).first()
        if cursor_row is None:
            # Stale cursor: return empty rather than restart at page 1.
            return [], None
        cur_date, cur_ingested, cur_id = cursor_row
        stmt = stmt.where(
            (Version.expression_date < cur_date)
            | ((Version.expression_date == cur_date) & (Version.ingested_at < cur_ingested))
            | (
                (Version.expression_date == cur_date)
                & (Version.ingested_at == cur_ingested)
                & (Version.id > cur_id)
            )
        )
    rows = list((await session.execute(stmt)).scalars().all())
    page_rows = rows[:limit]
    next_cursor = page_rows[-1].id if len(rows) > limit else None
    return page_rows, next_cursor


# A requested language is a preference, not a filter: matching on it alone
# returned nothing for an untranslated law rather than its original text. The
# membership clause admits both and the DISTINCT ON below chooses.
def _language_clause(language: str | None, prefix: str = "v.") -> str:
    if not language:
        return f"{prefix}parent_version_id IS NULL"
    return f"({prefix}language = :language OR {prefix}parent_version_id IS NULL)"


def _language_preference(language: str | None, prefix: str = "v.") -> str:
    """DISTINCT ON tie-break: the requested language wins, the original answers
    when it is absent. Empty when no language was asked for."""
    return f"({prefix}language = :language) DESC," if language else ""


_VERSIONS_OF_DOCTYPE_SQL = text(
    """
    SELECT v.id FROM versions v
    JOIN laws l ON l.id = v.law_id
    WHERE v.id = ANY(:version_ids) AND l.doctype = :doctype
    """
).bindparams(bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))))


async def versions_of_doctype(
    session: AsyncSession, version_ids: list[uuid.UUID], doctype: str
) -> list[uuid.UUID]:
    """Those of `version_ids` whose law is of `doctype`.

    For the scopes that name a version or a law outright, where the doctype
    cannot be pushed into the resolution the way it is for a jurisdiction.
    """
    if not version_ids:
        return []
    result = await session.execute(
        _VERSIONS_OF_DOCTYPE_SQL, {"version_ids": version_ids, "doctype": doctype}
    )
    return [row[0] for row in result.all()]


async def latest_versions_for_jurisdiction(
    session: AsyncSession,
    jurisdiction_code: str,
    language: str | None = None,
    doctype: str | None = None,
) -> list[uuid.UUID]:
    """Latest version_id per law in the jurisdiction, preferring `language` and
    falling back to the original. `doctype` narrows here rather than after
    retrieval: sieving the results instead draws the pool from every doctype, so
    one thinly represented in the top ranks reads as absent from the corpus.
    """
    doctype_clause = "AND l.doctype = :doctype" if doctype else ""
    result = await session.execute(
        text(
            f"""
            SELECT DISTINCT ON (v.law_id) v.id
            FROM versions v
            JOIN laws l ON l.id = v.law_id
            JOIN jurisdictions j ON j.id = l.jurisdiction_id
            WHERE j.code = :code AND {_language_clause(language)}
            {doctype_clause}
            ORDER BY v.law_id, {_language_preference(language)} v.expression_date DESC,
                     v.ingested_at DESC, v.id ASC
            """  # noqa: S608
        ),
        {
            "code": jurisdiction_code,
            **({"language": language} if language else {}),
            **({"doctype": doctype} if doctype else {}),
        },
    )
    return [row[0] for row in result.all()]


# Coverage needs one matching embedding per version, not every embedding in
# the corpus. The embeddings carry their version and jurisdiction, so the
# probe is one partition's version index, not a walk through provisions.
_COUNT_EMBEDDED_SQL = text(
    """
    SELECT count(*)
    FROM (SELECT DISTINCT unnest(:version_ids) AS id) scoped
    JOIN versions v ON v.id = scoped.id
    JOIN laws l ON l.id = v.law_id
    WHERE EXISTS (
      SELECT 1 FROM provision_embeddings e
      WHERE e.jurisdiction_id = l.jurisdiction_id
        AND e.version_id = scoped.id
        AND (
          e.model_id = :model_id
          OR e.model_id = CAST(:fallback_model_id AS text)
        )
    )
    """
).bindparams(bindparam("version_ids", type_=ARRAY(PG_UUID(as_uuid=True))))


async def count_embedded_versions(
    session: AsyncSession,
    version_ids: Sequence[uuid.UUID],
    model_id: str,
    fallback_model_id: str | None = None,
) -> int:
    """How many of these versions the dense arm can see. Counted against
    `provision_embeddings` for the model in use, not `versions.embedded_at`, which
    records that embedding finished without saying which model produced it. An
    array parameter, because a corpus-wide IN list exceeds the bind limit.
    """
    if not version_ids:
        return 0
    result = await session.execute(
        _COUNT_EMBEDDED_SQL,
        {
            "version_ids": list(version_ids),
            "model_id": model_id,
            "fallback_model_id": fallback_model_id,
        },
    )
    return int(result.scalar_one())


async def latest_versions_global(
    session: AsyncSession,
    language: str | None = None,
    jurisdictions: tuple[str, ...] | None = None,
    from_year: int | None = None,
    to_year: int | None = None,
    doctype: str | None = None,
) -> list[uuid.UUID]:
    """Latest version_id per law; a `None` filter is unbounded.

    `doctype` narrows here for the same reason it does in the per-jurisdiction
    resolver: sieving after retrieval reports a thinly-ranked doctype as absent.
    """
    params: dict[str, object] = {}
    if language:
        params["language"] = language
    wheres: list[str] = []
    if jurisdictions is not None:
        if not jurisdictions:
            return []
        wheres.append("AND j.code IN :jurisdictions")
        params["jurisdictions"] = tuple(jurisdictions)
    if from_year is not None:
        wheres.append("AND (l.year IS NULL OR l.year >= :from_year)")
        params["from_year"] = from_year
    if to_year is not None:
        wheres.append("AND (l.year IS NULL OR l.year <= :to_year)")
        params["to_year"] = to_year
    if doctype:
        wheres.append("AND l.doctype = :doctype")
        params["doctype"] = doctype
    law_join = "JOIN laws l ON l.id = v.law_id JOIN jurisdictions j ON j.id = l.jurisdiction_id"
    stmt = text(
        f"""
        SELECT DISTINCT ON (v.law_id) v.id
        FROM versions v
        {law_join}
        WHERE {_language_clause(language, prefix="v.")}
        {" ".join(wheres)}
        ORDER BY v.law_id, {_language_preference(language)} v.expression_date DESC,
                 v.ingested_at DESC, v.id ASC
        """  # noqa: S608
    )
    binds: list[Any] = [
        bindparam(name, expanding=True) for name in ("jurisdictions",) if name in params
    ]
    if binds:
        stmt = stmt.bindparams(*binds)
    result = await session.execute(stmt, params)
    return [row[0] for row in result.all()]


def _restore_non_body(source_xml: str, emitted_xml: str) -> str:
    """Put back the document children `to_akn` does not model: `<preface>`,
    `<preamble>`, `<conclusions>` and `<coverPage>`, which are absent from the
    domain model so an emit drops them. The emitted `<meta>` is kept instead,
    because the caller has just rewritten the FRBR expression URI in it.
    """
    src_root = etree.fromstring(source_xml.encode("utf-8"))
    out_root = etree.fromstring(emitted_xml.encode("utf-8"))
    src_doc = next(iter(src_root), None)
    out_doc = next(iter(out_root), None)
    if src_doc is None or out_doc is None:
        return emitted_xml

    def _names(doc: Any) -> list[str]:
        return [etree.QName(c).localname for c in doc]

    emitted = set(_names(out_doc))
    missing = [c for c in src_doc if etree.QName(c).localname not in emitted | {"meta"}]
    if not missing:
        # Nothing to put back, so do not re-serialise: the round-trip would
        # drop the XML declaration and change bytes for no reason.
        return emitted_xml

    body = next((c for c in out_doc if etree.QName(c).localname == "body"), None)
    anchor = body
    for child in missing:
        name = etree.QName(child).localname
        # Front matter precedes the body, back matter follows it; AKN element
        # order is fixed, so an append would put `<preface>` after `<body>`.
        if body is not None and name in ("coverPage", "preface", "preamble"):
            body.addprevious(child)
        elif anchor is not None:
            # Each back-matter element follows the one before it. Inserting
            # them all after `<body>` would reverse conclusions and attachments.
            anchor.addnext(child)
            anchor = child
        else:
            out_doc.append(child)
    rendered: str = etree.tostring(out_root, encoding="unicode")
    return rendered


def _walk_patch(node: Any, target_eid: str, new_text: str) -> bool:
    """Mutate the first descendant whose `akn_eid` matches; return True if found.

    Raises ValueError when the match is a structural element (Title / Chapter /
    Section) rather than a text-carrying leaf: patching those loses data in `to_akn`.
    """
    if isinstance(node, ElementBase) and node.akn_eid == target_eid:
        if node.kind not in _LEAF_KINDS:
            raise ValueError(
                f"provision_eid {target_eid!r} is a structural element "
                f"({node.kind}); cannot patch text"
            )
        node.text = new_text
        return True
    children = getattr(node, "children", None) or []
    for child in children:
        if _walk_patch(child, target_eid, new_text):
            return True
    return False


async def amend_provision(
    session: AsyncSession,
    *,
    parent_version_id: uuid.UUID,
    finding_id: uuid.UUID,
    provision_eid: str,
    new_text: str,
) -> Version:
    """Create a new Version with `provision_eid` patched to `new_text`, chained via
    `parent_version_id`.

    The new expression_uri is an auto-incremented `.{n}` step off the parent
    (FRBR-spec-aligned; Cobalt / Indigo / Laws.Africa parsers handle it). The
    finding to version linkage lives in a `version.amended_via_finding` C5 event,
    not in the URI.

    Raises:
      LookupError, parent version / law / jurisdiction missing.
      ValueError, provision_eid not present, eid points at a structural
                   element, or a concurrent amendment raced the URI uniqueness.
    """
    from codify.akn.io import parse_akn, to_akn
    from codify.storage.events import Event, save_event
    from codify.storage.repository import save_document

    parent = (
        await session.execute(select(Version).where(Version.id == parent_version_id))
    ).scalar_one_or_none()
    if parent is None:
        raise LookupError(f"parent version {parent_version_id} not found")

    law = (await session.execute(select(Law).where(Law.id == parent.law_id))).scalar_one_or_none()
    if law is None:
        raise LookupError(f"law {parent.law_id} not found for parent version")
    jurisdiction = (
        await session.execute(select(Jurisdiction).where(Jurisdiction.id == law.jurisdiction_id))
    ).scalar_one_or_none()
    if jurisdiction is None:
        raise LookupError(f"jurisdiction {law.jurisdiction_id} not found")

    doc = parse_akn(parent.akn_xml)
    patched = False
    for root in doc.body:
        if _walk_patch(root, provision_eid, new_text):
            patched = True
            break
    if not patched:
        raise ValueError(f"provision eid {provision_eid!r} not present in parent version body")

    # FRBR-spec-aligned `.{n}` step; the unique expression URI is the
    # concurrency safety net.
    child_count = int(
        (
            await session.execute(
                select(func.count(Version.id)).where(Version.parent_version_id == parent_version_id)
            )
        ).scalar_one()
    )
    n = child_count + 1
    doc.frbr_expression_uri = f"{parent.expression_uri}.{n}"

    # `to_akn` emits only the body, so preserve untouched preface, preamble and
    # conclusions from the parent rather than widening the body-shaped model.
    new_xml = _restore_non_body(parent.akn_xml, to_akn(doc))
    # The splice reassembles the complete document; validate it before storage.
    gate_akn(new_xml, where="amend_provision")
    try:
        new_version_id = await save_document(
            session,
            doc,
            jurisdiction_code=jurisdiction.code,
            law_title=law.title,
            doctype=law.doctype,
            year=law.year,
            number=law.number,
            akn_xml=new_xml,
            parent_version_id=parent_version_id,
        )
    except IntegrityError as exc:
        # The unique-on-expression_uri constraint is the concurrency safety net.
        raise ValueError("concurrent amendment; retry") from exc

    new_version = (
        await session.execute(select(Version).where(Version.id == new_version_id))
    ).scalar_one()

    # Link the new Version back to the finding via the events log. The
    # URI no longer carries the finding id; this event is the canonical
    # finding ↔ version linkage.
    await save_event(
        session,
        Event(
            actor_id="system",
            entity_type="version",
            entity_id=new_version.id,
            event_type="version.amended_via_finding",
            payload={
                "finding_id": str(finding_id),
                "parent_version_id": str(parent_version_id),
                "new_version_id": str(new_version.id),
                "provision_eid": provision_eid,
            },
        ),
    )

    return new_version


async def mark_version_embedded(session: AsyncSession, version_id: uuid.UUID) -> None:
    await session.execute(
        text("UPDATE versions SET embedded_at = NOW() WHERE id = :vid"),
        {"vid": version_id},
    )


async def mark_version_acquis_chapter(
    session: AsyncSession, version_id: uuid.UUID, chapter: int
) -> None:
    """EU acquis chapter 1-35; EU directives only."""
    if not 1 <= chapter <= 35:
        raise ValueError(f"acquis_chapter must be 1-35, got {chapter}")
    await session.execute(
        text("UPDATE versions SET acquis_chapter = :ch WHERE id = :vid"),
        {"ch": chapter, "vid": version_id},
    )


async def save_version_source_text(
    session: AsyncSession, version_id: uuid.UUID, source_text: str
) -> None:
    """Retain the text a version was structured from. Idempotent on re-ingest of
    the same version id. Caller commits."""
    await session.execute(
        text(
            "INSERT INTO version_source_texts (version_id, text) VALUES (:vid, :txt) "
            "ON CONFLICT (version_id) DO UPDATE SET text = EXCLUDED.text"
        ),
        {"vid": version_id, "txt": source_text},
    )


async def retain_source(
    session: AsyncSession,
    version_id: uuid.UUID,
    source_text: str | None,
    source_legibility: float | None,
) -> None:
    """Store what a version was structured from, skipping either half the caller
    could not supply. Idempotent, so a re-ingest backfills rather than forking."""
    if source_text is not None:
        await save_version_source_text(session, version_id, source_text)
    if source_legibility is not None:
        await session.execute(
            text("UPDATE versions SET source_legibility = :leg WHERE id = :vid"),
            {"leg": source_legibility, "vid": version_id},
        )


async def get_version_source_text(session: AsyncSession, version_id: uuid.UUID) -> str | None:
    return (
        await session.execute(
            text("SELECT text FROM version_source_texts WHERE version_id = :vid"),
            {"vid": version_id},
        )
    ).scalar_one_or_none()


__all__ = [
    "amend_provision",
    "count_embedded_versions",
    "count_versions_for_law",
    "get_version",
    "get_version_source_text",
    "retain_source",
    "save_version_source_text",
    "latest_version_for_law",
    "latest_versions_for_jurisdiction",
    "latest_versions_global",
    "list_versions",
    "mark_version_acquis_chapter",
    "mark_version_embedded",
    "stale_translation_ids",
    "version_lineage",
]


async def update_repaired_akn(
    session: AsyncSession,
    version_id: uuid.UUID,
    new_akn_xml: str,
    attribution: str,
    *,
    source_akn_sha256: str | None | EllipsisType = ...,
    reviewed_by: str = "",
) -> None:
    """Persist a deterministic-repair update to an existing version. Caller commits.

    ``repaired_at`` and ``repair_attribution`` go in the same UPDATE because the
    0042 trigger rejects an akn_xml change unless both are set. ``source_akn_sha256``
    re-stamps the translation-staleness hash on the force-retranslate path: a string
    sets it, ``None`` clears it to unknown, and the default Ellipsis leaves it alone
    for repair and re-OCR callers.

    ``reviewed_by`` additionally stamps ``reviewed_at`` and means a HUMAN approved
    this write, so it belongs to ``persist_approved_repair`` alone and never to a
    workflow's own judgement. Every bulk pass overwrites the two repair columns, so
    they answer who wrote last and cannot answer whether this expression was
    reviewed; ``reviewed_at`` can, and the 0106 trigger only lets it move forward.
    """
    set_sha = ", source_akn_sha256 = :src_sha" if source_akn_sha256 is not ... else ""
    set_reviewed = ", reviewed_at = NOW(), reviewed_by = :rev_by" if reviewed_by else ""
    stmt = text(
        "UPDATE versions "  # noqa: S608, only the bound-param column list varies
        "SET akn_xml = :akn, "
        "    repaired_at = NOW(), "
        f"    repair_attribution = :attr{set_sha}{set_reviewed} "
        "WHERE id = :vid"
    ).bindparams(
        bindparam("akn", value=new_akn_xml),
        bindparam("attr", value=attribution),
        bindparam("vid", value=version_id),
    )
    if source_akn_sha256 is not ...:
        stmt = stmt.bindparams(bindparam("src_sha", value=source_akn_sha256))
    if reviewed_by:
        stmt = stmt.bindparams(bindparam("rev_by", value=reviewed_by))
    await session.execute(stmt)


async def stale_translation_ids(session: AsyncSession, law_id: uuid.UUID) -> set[uuid.UUID]:
    """Translation version ids whose parent akn_xml no longer hashes to the
    stored source_akn_sha256. Hashing runs in Postgres (pgcrypto, enabled
    since 0001) so multi-MB XML never crosses the wire; a paired test pins
    the digest to hashlib.sha256(xml.encode("utf-8")).hexdigest()."""
    rows = await session.execute(
        text(
            "SELECT c.id FROM versions c "
            "JOIN versions p ON p.id = c.parent_version_id "
            "WHERE c.law_id = :law_id AND c.source_akn_sha256 IS NOT NULL "
            "AND encode(digest(convert_to(p.akn_xml, 'UTF8'), 'sha256'), 'hex') "
            "    <> c.source_akn_sha256"
        ).bindparams(bindparam("law_id", value=law_id))
    )
    return {row[0] for row in rows}
