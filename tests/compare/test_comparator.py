"""End-to-end comparator with mocked LLM and embedding client."""

from __future__ import annotations

from datetime import date
from typing import cast
from unittest.mock import AsyncMock

import pytest

from codify.akn.document import Document
from codify.akn.elements import Article, Chapter, Section
from codify.compare import compare
from codify.compare.aligner import Candidate
from codify.compare.comparator import _resolve_citations
from codify.compare.types import LLMCitation
from codify.core.llm import LLMClient
from codify.embed.client import EmbeddingClient


def test_resolve_citations_disambiguates_colliding_eids_by_quote() -> None:
    # Two corpus laws share eId "art_5"; the cited quote picks the right law.
    a = Candidate(
        provision=Article(akn_eid="art_5", akn_type="article", position=0, text="alpha clause"),
        score=0.9,
        frbr="/akn/al/act/2008/9901",
    )
    b = Candidate(
        provision=Article(akn_eid="art_5", akn_type="article", position=0, text="beta clause"),
        score=0.8,
        frbr="/akn/al/act/2007/9723",
    )
    cites = _resolve_citations(
        [LLMCitation(akn_eid="art_5", quote="beta clause")], [a, b], "/akn/al/act/2008/9901"
    )
    assert cites[0].frbr_uri == "/akn/al/act/2007/9723"


def test_resolve_citations_falls_back_to_top_score_on_ambiguous_quote() -> None:
    a = Candidate(
        provision=Article(akn_eid="art_5", akn_type="article", position=0, text="alpha"),
        score=0.9,
        frbr="/akn/al/act/2008/9901",
    )
    b = Candidate(
        provision=Article(akn_eid="art_5", akn_type="article", position=0, text="beta"),
        score=0.8,
        frbr="/akn/al/act/2007/9723",
    )
    # Quote matches neither text → fall back to the highest-scoring candidate.
    cites = _resolve_citations(
        [LLMCitation(akn_eid="art_5", quote="unrelated")], [a, b], "/akn/al/act/2008/9901"
    )
    assert cites[0].frbr_uri == "/akn/al/act/2008/9901"


def _embedding_client(directive_count: int, domestic_count: int) -> AsyncMock:
    client = AsyncMock()

    async def _embed_documents(items: list[tuple[str, str]]) -> list[list[float]]:
        return [_one_hot(i, dim=8) for i in range(len(items))]

    client.embed_documents = AsyncMock(side_effect=_embed_documents)
    _ = directive_count, domestic_count
    return client


def _one_hot(idx: int, *, dim: int) -> list[float]:
    return [1.0 if i == idx else 0.0 for i in range(dim)]


def _llm_answers(
    verdict: str,
    *,
    akn_eid: str | None = None,
    confidence: float = 0.85,
    actionable: bool = True,
    provision_kind: str = "obligation",
    clause_method: str = "normal",
) -> dict[str, object]:
    citations: list[dict[str, str]] = [{"akn_eid": akn_eid, "quote": "match"}] if akn_eid else []
    return {
        "verdict": verdict,
        "confidence": confidence,
        "note": f"verdict={verdict}",
        "citations": citations,
        "actionable": actionable,
        "provision_kind": provision_kind,
        "clause_method": clause_method,
    }


async def test_compare_produces_one_result_per_directive_provision(
    directive_doc: Document, domestic_doc: Document
) -> None:
    embedding = _embedding_client(2, 3)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(
        side_effect=[
            _llm_answers("aligned", akn_eid="art_a"),
            _llm_answers("partial", akn_eid="art_b"),
        ]
    )

    report = await compare(
        directive_doc,
        domestic_doc,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
        confidence_threshold=0.7,
    )

    assert len(report.results) == 2
    eids = [r.directive_eid for r in report.results]
    assert eids == ["art_1", "art_2"]
    assert report.summary.total == 2
    assert report.summary.aligned == 1
    assert report.summary.partial == 1
    assert report.summary.gap == 0


async def test_non_actionable_verdict_excluded_from_gap_headline(
    directive_doc: Document, domestic_doc: Document
) -> None:
    embedding = _embedding_client(2, 3)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(
        side_effect=[
            _llm_answers("aligned", akn_eid="art_a"),
            _llm_answers("gap", actionable=False, provision_kind="definition"),
        ]
    )

    report = await compare(
        directive_doc,
        domestic_doc,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
    )

    # The non-actionable "gap" lands in the N/A bucket, not the gap headline.
    assert report.summary.total == 1
    assert report.summary.aligned == 1
    assert report.summary.gap == 0
    assert report.summary.na == 1
    defn = next(r for r in report.results if r.directive_eid == "art_2")
    assert defn.actionable is False
    assert defn.provision_kind == "definition"


async def test_structural_heading_short_circuits_without_llm() -> None:
    directive = Document(
        frbr_work_uri="/akn/eu/directive/2016/943",
        frbr_expression_uri="/akn/eu/directive/2016/943/eng@2016-06-08",
        language="eng",
        expression_date=date(2016, 6, 8),
        body=[
            Chapter(
                akn_eid="chp_1",
                akn_type="chapter",
                position=0,
                heading="General provisions",
                children=[
                    Section(akn_eid="sec_1", akn_type="section", position=0, heading="Section I"),
                    Article(
                        akn_eid="art_1",
                        akn_type="article",
                        position=1,
                        heading="Scope",
                        text="Member States shall ensure protection of trade secrets.",
                    ),
                ],
            )
        ],
    )
    domestic = Document(
        frbr_work_uri="/akn/al/act/2018/35",
        frbr_expression_uri="/akn/al/act/2018/35/sqi@2018-04-15",
        language="sqi",
        expression_date=date(2018, 4, 15),
        body=[
            Article(
                akn_eid="art_a",
                akn_type="article",
                position=0,
                heading="Object",
                text="This law protects trade secrets.",
            )
        ],
    )
    embedding = _embedding_client(2, 1)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(side_effect=[_llm_answers("aligned", akn_eid="art_a")])

    report = await compare(
        directive,
        domestic,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
    )

    # The structural heading is classified without an LLM call.
    assert llm.chat_json.await_count == 1
    sec = next(r for r in report.results if r.directive_eid == "sec_1")
    assert sec.actionable is False
    assert sec.provision_kind == "structural"
    assert sec.clause_method == "na"
    assert report.summary.total == 1
    assert report.summary.na == 1


async def test_clause_method_threads_through_to_alignment(
    directive_doc: Document, domestic_doc: Document
) -> None:
    """The LLM's `clause_method` lands on `ProvisionAlignment` unchanged."""
    embedding = _embedding_client(2, 3)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(
        side_effect=[
            _llm_answers("aligned", akn_eid="art_a", clause_method="optional"),
            _llm_answers("partial", akn_eid="art_b", clause_method="discretionary"),
        ]
    )

    report = await compare(
        directive_doc,
        domestic_doc,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
    )

    by_eid = {r.directive_eid: r for r in report.results}
    assert by_eid["art_1"].clause_method == "optional"
    assert by_eid["art_2"].clause_method == "discretionary"


async def test_compare_drops_hallucinated_citations(
    directive_doc: Document, domestic_doc: Document
) -> None:
    embedding = _embedding_client(2, 3)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(
        side_effect=[
            _llm_answers("aligned", akn_eid="not_a_real_eid"),
            _llm_answers("aligned", akn_eid="art_a"),
        ]
    )

    report = await compare(
        directive_doc,
        domestic_doc,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
    )

    assert report.results[0].citations == []
    assert report.results[1].citations[0].akn_eid == "art_a"
    assert report.results[1].citations[0].frbr_uri == domestic_doc.frbr_expression_uri


async def test_compare_corpus_widens_pool_and_attributes_citation(
    directive_doc: Document, domestic_doc: Document
) -> None:
    # A second domestic law transposes an obligation the primary law misses;
    # corpus-level comparison should let the judge cite it, with the citation
    # attributed to that law's FRBR (not the primary anchor).
    corpus_doc = Document(
        frbr_work_uri="/akn/al/act/2008/9723",
        frbr_expression_uri="/akn/al/act/2008/9723/eng@2008-01-01",
        language="sqi",
        expression_date=date(2008, 1, 1),
        body=[
            Article(
                akn_eid="art_reg",
                akn_type="article",
                position=0,
                heading="Registration",
                text="Trade secret registration rules.",
            )
        ],
    )
    embedding = _embedding_client(2, 4)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(
        side_effect=[
            _llm_answers("aligned", akn_eid="art_reg"),
            _llm_answers("gap", actionable=False, provision_kind="definition"),
        ]
    )

    report = await compare(
        directive_doc,
        domestic_doc,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
        corpus=[(corpus_doc, None)],
    )

    cite = report.results[0].citations[0]
    assert cite.akn_eid == "art_reg"
    assert cite.frbr_uri == corpus_doc.frbr_expression_uri


async def test_compare_flags_review_below_threshold(
    directive_doc: Document, domestic_doc: Document
) -> None:
    embedding = _embedding_client(2, 3)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(
        side_effect=[
            _llm_answers("partial", akn_eid="art_a", confidence=0.4),
            _llm_answers("aligned", akn_eid="art_b", confidence=0.95),
        ]
    )

    report = await compare(
        directive_doc,
        domestic_doc,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
        confidence_threshold=0.7,
    )

    assert report.results[0].needs_review is True
    assert report.results[1].needs_review is False
    assert report.summary.needs_review == 1


async def test_compare_propagates_seed_to_llm(
    directive_doc: Document, domestic_doc: Document
) -> None:
    embedding = _embedding_client(2, 3)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(side_effect=[_llm_answers("gap"), _llm_answers("gap")])

    await compare(
        directive_doc,
        domestic_doc,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
        seed=42,
    )

    for call in llm.chat_json.await_args_list:
        assert call.kwargs["seed"] == 42


async def test_compare_handles_empty_domestic_with_gap_verdicts(
    directive_doc: Document,
) -> None:
    empty_domestic = Document(
        frbr_work_uri="/akn/al/act/2018/35",
        frbr_expression_uri="/akn/al/act/2018/35/eng@2018-04-15",
        language="sqi",
        expression_date=directive_doc.expression_date,
        body=[],
    )
    embedding = _embedding_client(2, 0)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(side_effect=[_llm_answers("gap"), _llm_answers("gap")])

    report = await compare(
        directive_doc,
        empty_domestic,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
    )

    assert report.summary.gap == 2
    assert all(not r.citations for r in report.results)


async def test_compare_rejects_invalid_threshold(
    directive_doc: Document, domestic_doc: Document
) -> None:
    embedding = _embedding_client(2, 3)
    llm = AsyncMock()
    with pytest.raises(ValueError, match="confidence_threshold"):
        await compare(
            directive_doc,
            domestic_doc,
            llm=cast(LLMClient, llm),
            embedding_client=cast(EmbeddingClient, embedding),
            confidence_threshold=1.5,
        )


async def test_compare_threshold_for_callable_overrides_global_default(
    directive_doc: Document, domestic_doc: Document
) -> None:
    # Per-provision threshold via callable: art_1 high (0.95), art_2 low (0.10).
    # LLM returns confidence 0.50 for both → art_1 needs review, art_2 does not.
    embedding = _embedding_client(2, 3)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(
        side_effect=[
            _llm_answers("aligned", confidence=0.50, akn_eid="art_a"),
            _llm_answers("aligned", confidence=0.50, akn_eid="art_a"),
        ]
    )

    report = await compare(
        directive_doc,
        domestic_doc,
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
        threshold_for=lambda eid, _frbr: 0.95 if eid == "art_1" else 0.10,
    )

    by_eid = {r.directive_eid: r for r in report.results}
    assert by_eid["art_1"].needs_review is True
    assert by_eid["art_2"].needs_review is False
