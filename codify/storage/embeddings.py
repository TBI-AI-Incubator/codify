"""Provision embeddings, upsert, lookup, version-level orchestration."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

import structlog
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import delete, literal, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from codify.quality.sentinels import is_placeholder_only
from codify.storage.models import (
    Law,
    Provision,
    ProvisionEmbedding,
    Section,
    Version,
    VersionUnitEmbedding,
)

if TYPE_CHECKING:
    from codify.embed.client import EmbeddingClient

logger = structlog.get_logger()


async def upsert_embedding(
    session: AsyncSession,
    provision_id: uuid.UUID,
    vector: list[float],
    model_id: str,
) -> ProvisionEmbedding:
    """Upsert via ON CONFLICT (provision_id, model_id, jurisdiction_id). Different
    model_ids coexist. The version and jurisdiction are read from the provision
    in the same statement, never taken from the caller: they route the row to its
    partition and scope every search, so a wrong pair would hide the provision
    from its own scope. A provision that does not exist raises, as the key did."""
    # id supplied explicitly: pg_insert bypasses SQLModel's default_factory.
    scope = (
        select(
            literal(uuid.uuid4(), type_=PG_UUID(as_uuid=True)),
            Provision.id,
            literal(vector, type_=HALFVEC(768)),
            literal(model_id),
            Provision.version_id,
            Law.jurisdiction_id,
        )
        .join(Version, Version.id == Provision.version_id)
        .join(Law, Law.id == Version.law_id)
        .where(Provision.id == provision_id)
    )
    stmt = (
        pg_insert(ProvisionEmbedding)
        .from_select(
            ["id", "provision_id", "embedding", "model_id", "version_id", "jurisdiction_id"],
            scope,
        )
        .on_conflict_do_update(
            index_elements=["provision_id", "model_id", "jurisdiction_id"],
            set_={"embedding": vector},
        )
        .returning(ProvisionEmbedding)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def get_embedding(
    session: AsyncSession, provision_id: uuid.UUID, model_id: str
) -> ProvisionEmbedding | None:
    result = await session.execute(
        select(ProvisionEmbedding).where(
            (ProvisionEmbedding.provision_id == provision_id)
            & (ProvisionEmbedding.model_id == model_id)
        )
    )
    return result.scalar_one_or_none()


PATH_CONTEXT_SUFFIX = "+path"


def embedding_model_id(base: str, *, path_context: bool) -> str:
    """The id vectors of this construction are stamped with.

    Derived in one place: the writer and the backfill selector disagreeing would
    leave a run selecting rows it had already embedded, for ever.
    """
    base = base.removesuffix(PATH_CONTEXT_SUFFIX)
    return f"{base}{PATH_CONTEXT_SUFFIX}" if path_context else base


async def embed_version_provisions(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    client: EmbeddingClient,
    model_id: str | None = None,
    path_context: bool = False,
) -> int:
    """Read provisions for a version, embed them, upsert. Returns rows written.

    `path_context` puts the act's title before the section's, which is what a
    provision otherwise never states: measured at 0.53 to 0.85 on reference
    resolution. Vectors of each construction carry their own model id and sit
    side by side, so the choice is reversible without a migration.
    """
    model_id = model_id or embedding_model_id(client.model, path_context=path_context)
    stmt = (
        select(Provision, Section, Law.title)
        .outerjoin(Section, Provision.section_id == Section.id)
        .join(Version, Version.id == Provision.version_id)
        .join(Law, Law.id == Version.law_id)
        .where(Provision.version_id == version_id)
        .order_by(Provision.position)
    )
    rows = (await session.execute(stmt)).all()
    # A marker for an image nobody transcribed is not law, and embedded it clusters
    # tightly enough to answer a query with a filename. The provision row itself
    # stays; the acquisition gap that produced it is a separate matter.
    skipped = [r for r in rows if r[0].excluded_from_pool or is_placeholder_only(r[0].text)]
    rows = [r for r in rows if not (r[0].excluded_from_pool or is_placeholder_only(r[0].text))]
    if skipped:
        # The vector goes, not just this pass: one written before the guard existed
        # would otherwise stay in the index with nothing to revisit it.
        await session.execute(
            delete(ProvisionEmbedding).where(
                ProvisionEmbedding.provision_id.in_([r[0].id for r in skipped])
            )
        )
        logger.info("embed_skipped_placeholders", version_id=str(version_id), skipped=len(skipped))
    if not rows:
        return 0

    items = [
        (_context(law_title, section, path_context=path_context), provision.text)
        for provision, section, law_title in rows
    ]
    vectors = await client.embed_documents(items)

    for (provision, _section, _law), vector in zip(rows, vectors, strict=True):
        await upsert_embedding(session, provision.id, vector, model_id)

    return len(rows)


def _context(law_title: str | None, section: Section | None, *, path_context: bool) -> str:
    """What precedes the provision's own words in the embedded text."""
    heading = (section.title if section else None) or ""
    if not path_context:
        return heading
    return " > ".join(part for part in (law_title or "", heading) if part)


async def embed_and_stamp(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    client: EmbeddingClient,
    path_context: bool,
) -> int:
    """Embed a version and stamp it, or neither. Returns rows written.

    One path: eight copies of that guard drift apart.
    """
    from codify.storage.versions import mark_version_embedded

    count = await embed_version_provisions(
        session, version_id, client=client, path_context=path_context
    )
    # A version whose every provision is a non-transcription marker embeds nothing
    # and is still finished: without the stamp it stays unembedded forever and
    # anything gating on `embedded_at` re-runs it on every pass.
    if count or await _has_only_placeholders(session, version_id):
        await mark_version_embedded(session, version_id)
    return count


async def _has_only_placeholders(session: AsyncSession, version_id: uuid.UUID) -> bool:
    """Whether the version has provisions and not one of them is embeddable,
    by the same test the embed step applies: the exclusion flag or the marker."""
    rows = (
        await session.execute(
            select(Provision.text, Provision.excluded_from_pool).where(
                Provision.version_id == version_id
            )
        )
    ).all()
    return bool(rows) and all(excluded or is_placeholder_only(t) for t, excluded in rows)


async def upsert_unit_embedding(
    session: AsyncSession,
    version_id: uuid.UUID,
    akn_eid: str,
    vector: list[float],
    model_id: str,
    text_sha256: str | None = None,
) -> None:
    """Upsert a (version_id, akn_eid, model_id) → embedding row, recording the
    hash of the text it embeds so a later fold change misses rather than hits."""
    stmt = (
        pg_insert(VersionUnitEmbedding)
        .values(
            id=uuid.uuid4(),
            version_id=version_id,
            akn_eid=akn_eid,
            embedding=vector,
            model_id=model_id,
            text_sha256=text_sha256,
        )
        .on_conflict_do_update(
            index_elements=["version_id", "akn_eid", "model_id"],
            set_={"embedding": vector, "text_sha256": text_sha256},
        )
    )
    await session.execute(stmt)


def _text_digest(title: str, text: str) -> str:
    return hashlib.sha256(f"{title}\n{text}".encode()).hexdigest()


async def embed_provisions_cached(
    session: AsyncSession,
    version_id: uuid.UUID,
    items: list[tuple[str, str, str]],
    *,
    client: EmbeddingClient,
    model_id: str | None = None,
) -> list[list[float]]:
    """Embed a version's assessable units, reading from
    ``version_unit_embeddings`` where present and upserting misses.

    ``items`` are ``(akn_eid, title, text)`` in document order; the
    return is the vector list in the same order. Empty/duplicate eIds
    are embedded fresh and logged, caching would collide on the key.
    """
    model_id = model_id or client.model
    if not items:
        return []

    eids = [eid for eid, _, _ in items]
    seen: set[str] = set()
    cacheable: set[int] = set()
    uncacheable: list[tuple[int, str]] = []
    for i, eid in enumerate(eids):
        if not eid:
            uncacheable.append((i, "empty"))
        elif eid in seen:
            uncacheable.append((i, "duplicate"))
        else:
            seen.add(eid)
            cacheable.add(i)
    if uncacheable:
        reasons = {r for _, r in uncacheable}
        logger.warning(
            "embed_cache_skipped_units",
            version_id=str(version_id),
            count=len(uncacheable),
            reasons=sorted(reasons),
            sample_eids=[eids[i] or "<empty>" for i, _ in uncacheable[:5]],
        )

    cacheable_eids = [eids[i] for i in cacheable]
    # A hit must match the text as folded today; a stale hash is a miss and is overwritten.
    digests = {eids[i]: _text_digest(items[i][1], items[i][2]) for i in cacheable}
    cached: dict[str, list[float]] = {}
    if cacheable_eids:
        rows = (
            await session.execute(
                select(
                    VersionUnitEmbedding.akn_eid,
                    VersionUnitEmbedding.embedding,
                    VersionUnitEmbedding.text_sha256,
                ).where(
                    VersionUnitEmbedding.version_id == version_id,
                    VersionUnitEmbedding.akn_eid.in_(cacheable_eids),
                    VersionUnitEmbedding.model_id == model_id,
                )
            )
        ).all()
        cached = {
            eid: (vec.to_list() if hasattr(vec, "to_list") else list(vec))
            for eid, vec, digest in rows
            if digest == digests[eid]
        }

    # `miss_idx` MUST be in document order so the embed-call result aligns
    # by position with eIds for the upsert + output re-interleave.
    miss_idx = [i for i, eid in enumerate(eids) if (i not in cacheable) or (eid not in cached)]
    fresh: list[list[float]] = []
    if miss_idx:
        fresh = await client.embed_documents([(items[i][1], items[i][2]) for i in miss_idx])
        # Sort the upsert order by eId so two concurrent comparator runs
        # against the same version acquire row locks in matching order
        # and don't deadlock under ON CONFLICT.
        to_upsert = sorted(
            ((eids[i], vec) for i, vec in zip(miss_idx, fresh, strict=True) if i in cacheable),
            key=lambda pair: pair[0],
        )
        for eid, vec in to_upsert:
            await upsert_unit_embedding(session, version_id, eid, vec, model_id, digests[eid])
        await session.flush()

    out: list[list[float]] = []
    fresh_iter = iter(fresh)
    for i, eid in enumerate(eids):
        if i in cacheable and eid in cached:
            out.append(cached[eid])
        else:
            out.append(next(fresh_iter))
    return out


__all__ = [
    "embed_and_stamp",
    "embed_provisions_cached",
    "embed_version_provisions",
    "get_embedding",
    "upsert_embedding",
    "upsert_unit_embedding",
]
