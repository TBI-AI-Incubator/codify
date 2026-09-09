"""Compliance comparator, directive vs domestic, provision-level alignment."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import structlog
from langfuse import get_client
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codify.akn.document import Document
from codify.akn.elements import BodyElement
from codify.akn.frbr import country_of
from codify.compare.aligner import (
    Candidate,
    embed_provisions,
    select_candidates,
)
from codify.compare.prompts import SYSTEM_PROMPT, build_user_prompt, injection_signal
from codify.compare.scaffold import (
    effective_headings,
    is_excluded,
    is_structural,
    iter_assessable,
    markers_for,
    provision_text,
    summarise,
)
from codify.compare.types import (
    Citation,
    ComparisonReport,
    LLMCitation,
    LLMVerdict,
    ProvisionAlignment,
    RejectedCandidate,
)
from codify.compare.validation import chat_json_validated
from codify.core.i18n import with_response_language
from codify.core.llm import LLMClient
from codify.core.tracing import (
    attach_trace_attribution,
    get_current_actor,
    get_current_release,
    get_current_session,
    langfuse_trace_context,
)
from codify.embed.client import EmbeddingClient
from codify.observability import ProgressEvent, with_progress

logger = structlog.get_logger()


def _country_from_frbr(frbr_uri: str) -> str:
    """Extract the country segment from /akn/{country}/...; 'unknown' on malformed."""
    country = country_of(frbr_uri, default="unknown")
    if country == "unknown":
        logger.warning("frbr_uri_country_parse_failed", uri=frbr_uri)
    return country


async def _embed_group(
    provisions: list[BodyElement],
    *,
    embedding_client: EmbeddingClient,
    session_factory: async_sessionmaker[AsyncSession] | None,
    version_id: uuid.UUID | None,
    markers: tuple[str, ...] | None = None,
) -> list[list[float]]:
    """Embed one provision group, via the version_unit_embeddings cache when a
    session_factory + version id are supplied; otherwise compute fresh."""
    if session_factory is None or version_id is None:
        return await embed_provisions(
            provisions, embedding_client=embedding_client, markers=markers
        )

    from codify.storage.embeddings import embed_provisions_cached

    items = [(p.akn_eid or "", p.heading or "", provision_text(p, markers)) for p in provisions]
    async with session_factory() as s:
        vecs = await embed_provisions_cached(s, version_id, items, client=embedding_client)
        await s.commit()
    return vecs


async def warm_comparator_cache(
    session: AsyncSession, version_id: uuid.UUID, *, client: EmbeddingClient
) -> int:
    """Prime version_unit_embeddings so the first compare is a DB read, not an inline embed."""
    from codify.akn import parse_akn
    from codify.storage.embeddings import embed_provisions_cached
    from codify.storage.versions import get_version

    ver = await get_version(session, version_id)
    if ver is None:
        return 0
    doc = parse_akn(ver.akn_xml.encode("utf-8"))
    markers = markers_for(doc)
    items = [
        (el.akn_eid or "", el.heading or "", provision_text(el, markers))
        for el in iter_assessable(doc)
        if not is_excluded(el, doc)
    ]
    if not items:
        return 0
    await embed_provisions_cached(session, version_id, items, client=client)
    return len(items)


async def compare(
    directive: Document,
    domestic: Document,
    *,
    llm: LLMClient,
    embedding_client: EmbeddingClient,
    confidence_threshold: float = 0.7,
    threshold_for: Callable[[str, str], float] | None = None,
    seed: int | None = None,
    k_candidates: int = 5,
    llm_concurrency: int = 25,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_alignment: Callable[[ProvisionAlignment], None] | None = None,
    session_factory: "async_sessionmaker[AsyncSession] | None" = None,
    directive_version_id: "uuid.UUID | None" = None,
    domestic_version_id: "uuid.UUID | None" = None,
    corpus: Sequence[tuple[Document, "uuid.UUID | None"]] = (),
    response_language: str | None = None,
) -> ComparisonReport:
    """Compare a directive against domestic law; ``seed`` makes runs reproducible.

    `corpus` widens the candidate pool with additional domestic laws from the
    same jurisdiction, so an obligation transposed by *any* of them counts as
    aligned (the EC assesses the whole framework, not a single act). The
    primary `domestic` law remains the report's anchor; citations record which
    law actually covers each provision.

    Embeddings round-trip ``version_unit_embeddings`` when version ids and a
    session_factory are supplied; otherwise computed fresh.
    """
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must be in [0, 1]")
    if k_candidates <= 0:
        raise ValueError("k_candidates must be positive")
    if llm_concurrency <= 0:
        raise ValueError("llm_concurrency must be positive")
    resolved_threshold: Callable[[str, str], float] = threshold_for or (
        lambda _eid, _frbr: confidence_threshold
    )

    directive_provisions = [p for p in iter_assessable(directive) if not is_excluded(p, directive)]
    directive_headings = effective_headings(directive)

    domestic_groups: list[tuple[Document, uuid.UUID | None]] = [
        # Keep source-law provenance for judging and citation resolution.
        (domestic, domestic_version_id),
        *corpus,
    ]
    domestic_provisions: list[BodyElement] = []
    domestic_frbrs: list[str] = []
    group_provisions: list[list[BodyElement]] = []
    for doc, _vid in domestic_groups:
        provs = [
            p for p in iter_assessable(doc) if not is_structural(p) and not is_excluded(p, doc)
        ]
        group_provisions.append(provs)
        domestic_provisions.extend(provs)
        domestic_frbrs.extend(doc.frbr_expression_uri for _ in provs)

    logger.info(
        "compare_start",
        directive=directive.frbr_expression_uri,
        domestic=domestic.frbr_expression_uri,
        corpus=[doc.frbr_expression_uri for doc, _ in corpus],
        directive_provisions=len(directive_provisions),
        domestic_provisions=len(domestic_provisions),
        seed=seed,
    )

    langfuse = get_client()
    domestic_country = _country_from_frbr(domestic.frbr_expression_uri)
    with langfuse.start_as_current_observation(
        as_type="span",
        name="compare",
        trace_context=langfuse_trace_context(),
        input={
            "directive": directive.frbr_expression_uri,
            "domestic": domestic.frbr_expression_uri,
            "seed": seed,
            "confidence_threshold": confidence_threshold,
        },
        metadata={"jurisdiction": domestic_country, "pipeline": "compare"},
    ) as root:
        attach_trace_attribution(
            langfuse,
            user_id=get_current_actor(),
            session_id=get_current_session(),
            release=get_current_release(),
            tags=[f"jurisdiction:{domestic_country}", "pipeline:compare"],
        )
        directive_vecs = await _embed_group(
            directive_provisions,
            embedding_client=embedding_client,
            session_factory=session_factory,
            version_id=directive_version_id,
            markers=markers_for(directive),
        )
        # Cache embeddings by version, preserving candidate order for alignment.
        domestic_vecs: list[list[float]] = []
        for provs, (group_doc, vid) in zip(group_provisions, domestic_groups, strict=True):
            domestic_vecs.extend(
                await _embed_group(
                    provs,
                    embedding_client=embedding_client,
                    session_factory=session_factory,
                    version_id=vid,
                    markers=markers_for(group_doc),
                )
            )

        # Stamp streamed alignments with the compare trace for consistent tracing.
        trace_id = langfuse.get_current_trace_id()

        async def _one(pv: tuple[BodyElement, list[float]]) -> ProvisionAlignment:
            directive_provision, dvec = pv
            alignment = await _assess_provision(
                directive_provision=directive_provision,
                directive_heading=directive_headings.get(directive_provision.akn_eid),
                directive_vec=dvec,
                domestic=domestic_provisions,
                domestic_vecs=domestic_vecs,
                domestic_frbrs=domestic_frbrs,
                domestic_frbr_uri=domestic.frbr_expression_uri,
                directive_markers=markers_for(directive),
                domestic_markers=markers_for(domestic),
                directive_frbr_uri=directive.frbr_expression_uri,
                llm=llm,
                k_candidates=k_candidates,
                seed=seed,
                threshold_for=resolved_threshold,
                response_language=response_language,
            )
            if trace_id:
                alignment = alignment.model_copy(update={"langfuse_trace_id": trace_id})
            return alignment

        def _default_progress(ev: ProgressEvent) -> None:
            logger.info("comparator_progress", **dataclasses.asdict(ev))

        progress_cb: Callable[[ProgressEvent], None] = (
            on_progress if on_progress is not None else _default_progress
        )

        pairs = list(zip(directive_provisions, directive_vecs, strict=True))
        results = await with_progress(
            pairs,
            _one,
            name="compare",
            total=len(pairs),
            every_n=10,
            every_s=30.0,
            on_event=progress_cb,
            on_result=on_alignment,
            concurrency=llm_concurrency,
            label_of=lambda pv: pv[0].akn_eid,
        )

        summary = summarise(list(results))
        root.update(output={"summary": summary.model_dump()})
        return ComparisonReport(
            directive_frbr_uri=directive.frbr_expression_uri,
            domestic_frbr_uri=domestic.frbr_expression_uri,
            generated_at=datetime.now(UTC),
            seed=seed,
            confidence_threshold=confidence_threshold,
            results=list(results),
            summary=summary,
        )


async def _assess_provision(
    *,
    directive_provision: BodyElement,
    directive_heading: str | None,
    directive_vec: list[float],
    domestic: list[BodyElement],
    domestic_vecs: list[list[float]],
    domestic_frbrs: list[str],
    domestic_frbr_uri: str,
    directive_frbr_uri: str,
    llm: LLMClient,
    k_candidates: int,
    seed: int | None,
    threshold_for: Callable[[str, str], float],
    response_language: str | None = None,
    directive_markers: tuple[str, ...] | None = None,
    domestic_markers: tuple[str, ...] | None = None,
) -> ProvisionAlignment:
    # Classify structural headings without an LLM call and keep their own label.
    if is_structural(directive_provision):
        return ProvisionAlignment(
            directive_eid=directive_provision.akn_eid,
            directive_heading=directive_provision.heading,
            verdict="gap",
            confidence=1.0,
            note="Structural heading: no transposition obligation.",
            citations=[],
            needs_review=False,
            actionable=False,
            provision_kind="structural",
            clause_method="na",
        )

    langfuse = get_client()
    with langfuse.start_as_current_observation(
        as_type="span",
        name="assess_provision",
        input={"directive_eid": directive_provision.akn_eid},
    ) as span:
        candidates: list[Candidate] = (
            select_candidates(
                directive_vec, domestic, domestic_vecs, k=k_candidates, sources=domestic_frbrs
            )
            if domestic
            else []
        )
        user_prompt = build_user_prompt(
            directive_provision,
            candidates,
            domestic_frbr_uri=domestic_frbr_uri,
            directive_markers=directive_markers,
            domestic_markers=domestic_markers,
        )

        verdict = await chat_json_validated(
            llm,
            user_prompt,
            LLMVerdict,
            system=with_response_language(SYSTEM_PROMPT, response_language),
            seed=seed,
        )
        _enforce_clause_method_contract(verdict, directive_eid=directive_provision.akn_eid)

        citations = _resolve_citations(verdict.citations, candidates, domestic_frbr_uri)
        rejected = _resolve_rejected_candidates(
            verdict, candidates, directive_eid=directive_provision.akn_eid
        )

        span.update(output={"verdict": verdict.verdict, "confidence": verdict.confidence})
        chapter_threshold = threshold_for(directive_provision.akn_eid, directive_frbr_uri)
        return ProvisionAlignment(
            directive_eid=directive_provision.akn_eid,
            directive_heading=directive_heading,
            verdict=verdict.verdict,
            confidence=verdict.confidence,
            note=verdict.note,
            citations=citations,
            needs_review=(verdict.confidence < chapter_threshold or injection_signal(verdict)),
            actionable=verdict.actionable,
            provision_kind=verdict.provision_kind,
            clause_method=verdict.clause_method,
            rejected_candidates=rejected,
        )


_REJECTED_FLOOR_REASON = "(LLM declined to articulate)"
_REJECTED_FLOOR_K = 3
_REJECTED_REASON_MAX = 400


def _resolve_rejected_candidates(
    verdict: LLMVerdict,
    candidates: list[Candidate],
    *,
    directive_eid: str,
) -> list[RejectedCandidate]:
    """Validate eIds, attach similarity, floor with stubs on empty gap. Non-gap returns []."""
    if verdict.verdict != "gap":
        return []

    by_eid: dict[str, Candidate] = {c.provision.akn_eid: c for c in candidates}
    resolved: list[RejectedCandidate] = []
    for rc in verdict.rejected_candidates:
        cand = by_eid.get(rc.akn_eid)
        if cand is None:
            logger.warning(
                "comparator_rejected_candidate_eid_unknown",
                directive_eid=directive_eid,
                akn_eid=rc.akn_eid,
            )
            continue
        resolved.append(
            RejectedCandidate(
                akn_eid=rc.akn_eid,
                similarity=cand.score,
                reason=rc.reason[:_REJECTED_REASON_MAX],
            )
        )

    if resolved:
        return resolved

    floor = [
        RejectedCandidate(
            akn_eid=c.provision.akn_eid, similarity=c.score, reason=_REJECTED_FLOOR_REASON
        )
        for c in candidates[:_REJECTED_FLOOR_K]
    ]
    if floor:
        logger.warning(
            "comparator_rejected_candidates_synthesised",
            directive_eid=directive_eid,
            candidate_count=len(candidates),
            floor_size=len(floor),
        )
    return floor


def _enforce_clause_method_contract(verdict: LLMVerdict, *, directive_eid: str) -> None:
    """Two-way contract floor; mutates verdict in place. Pair with prompts.py."""
    if verdict.actionable and verdict.clause_method == "na":
        logger.warning(
            "comparator_clause_method_default_applied",
            directive_eid=directive_eid,
            provision_kind=verdict.provision_kind,
            reason="actionable=true with clause_method=na; coerced to normal",
        )
        verdict.clause_method = "normal"
    elif not verdict.actionable and verdict.clause_method != "na":
        logger.warning(
            "comparator_clause_method_default_applied",
            directive_eid=directive_eid,
            provision_kind=verdict.provision_kind,
            reason=f"actionable=false with clause_method={verdict.clause_method}; coerced to na",
        )
        verdict.clause_method = "na"


def _resolve_citations(
    raw_citations: list[LLMCitation],
    candidates: list[Candidate],
    domestic_frbr_uri: str,
) -> list[Citation]:
    # Resolve against displayed candidates, disambiguating duplicate eIds by quote
    # before falling back to the highest-scoring candidate.
    by_eid: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_eid.setdefault(c.provision.akn_eid, []).append(c)

    out: list[Citation] = []
    for rc in raw_citations:
        matches = by_eid.get(rc.akn_eid)
        if not matches:
            logger.warning("comparator_unresolved_citation", akn_eid=rc.akn_eid)
            continue
        chosen = matches[0]
        if len(matches) > 1:
            quote = (rc.quote or "").strip()[:40]
            chosen = next(
                (c for c in matches if quote and quote in provision_text(c.provision)),
                matches[0],
            )
        out.append(
            Citation(
                frbr_uri=chosen.frbr or domestic_frbr_uri,
                akn_eid=rc.akn_eid,
                quote=rc.quote,
            )
        )
    return out


__all__ = ["compare"]
