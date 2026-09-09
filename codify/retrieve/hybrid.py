"""Hybrid retrieval over a scoped version set."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from codify.embed.client import EmbeddingClient
from codify.storage.retrieval import RRF_K, hybrid_search
from codify.storage.versions import (
    latest_version_for_law,
    latest_versions_for_jurisdiction,
    versions_of_doctype,
)


@dataclass(frozen=True)
class ProvisionMatch:
    provision_id: uuid.UUID
    rrf_score: float
    text: str
    akn_eid: str


async def resolve_scope_versions(
    session: AsyncSession,
    *,
    version_id: uuid.UUID | None = None,
    law_id: uuid.UUID | None = None,
    jurisdiction_code: str | None = None,
    language: str | None = None,
    doctype: str | None = None,
) -> list[uuid.UUID]:
    """Which versions a scope covers. Exactly one scope required.

    Extracted so a caller that orders by law metadata rather than relevance works
    from the same set as the relevance path; two copies of this would drift, and
    the doctype rule below is the kind of thing that drifts silently.
    """
    filters = [version_id, law_id, jurisdiction_code]
    if sum(f is not None for f in filters) != 1:
        raise ValueError("retrieve requires exactly one of version_id, law_id, jurisdiction_code")

    if version_id is not None:
        version_ids: list[uuid.UUID] = [version_id]
    elif law_id is not None:
        version = await latest_version_for_law(session, law_id, language=language)
        version_ids = [version.id] if version else []
    else:
        assert jurisdiction_code is not None  # validated above
        version_ids = await latest_versions_for_jurisdiction(
            session, jurisdiction_code, language=language, doctype=doctype
        )
        doctype = None  # already applied, in SQL, at resolution
    # The jurisdiction branch narrows by doctype while resolving. The other two
    # cannot, so the same constraint is applied to the resolved set here rather
    # than left unenforced: a law scoped by id and filtered to a doctype it is
    # not must return nothing, not its own provisions.
    if version_ids and doctype:
        version_ids = await versions_of_doctype(session, version_ids, doctype)
    return version_ids


async def retrieve(
    session: AsyncSession,
    query: str,
    *,
    embedding_client: EmbeddingClient,
    jurisdiction_code: str | None = None,
    law_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    language: str | None = None,
    k: int = 10,
    candidate_pool: int = 30,
    rrf_k: int = RRF_K,
    model_id: str | None = None,
    fallback_model_id: str | None = None,
    doctype: str | None = None,
    akn_type: str | None = None,
    include_non_normative: bool = False,
) -> list[ProvisionMatch]:
    """Hybrid dense + BM25 retrieval. Exactly one scope required. `language`
    prefers that expression of each law, falling back to the original where it
    does not exist (default: the original). Normative provisions only unless
    `include_non_normative`; an elucidation is reachable, not ranked with law.

    `doctype` and `akn_type` narrow inside the query rather than after it, so
    the candidate pool is drawn from matching rows instead of being sieved down
    to whatever survived a pool ranked across everything."""
    model_id = model_id or embedding_client.model
    version_ids = await resolve_scope_versions(
        session,
        version_id=version_id,
        law_id=law_id,
        jurisdiction_code=jurisdiction_code,
        language=language,
        doctype=doctype,
    )
    if not version_ids:
        return []

    query_vec = await embedding_client.embed_one(query, task="query")

    rows = await hybrid_search(
        session,
        query_vec=query_vec,
        query_text=query,
        version_ids=version_ids,
        k=k,
        candidate_pool=candidate_pool,
        rrf_k=rrf_k,
        model_id=model_id,
        fallback_model_id=fallback_model_id,
        akn_type=akn_type,
        include_non_normative=include_non_normative,
    )
    return [
        ProvisionMatch(provision_id=pid, akn_eid=eid, text=txt, rrf_score=score)
        for pid, eid, txt, score in rows
    ]
