"""Resolver: dedupe hallucinated eIds, attach similarity, floor on empty gap."""

from __future__ import annotations

from codify.akn.elements import Article
from codify.compare.aligner import Candidate
from codify.compare.comparator import _resolve_rejected_candidates
from codify.compare.types import LLMCitation, LLMRejectedCandidate, LLMVerdict


def _candidate(eid: str, score: float) -> Candidate:
    return Candidate(
        provision=Article(akn_eid=eid, akn_type="article", position=1, heading=f"H {eid}"),
        score=score,
        frbr="/akn/al/act/2020/162",
    )


def _verdict(
    *,
    verdict: str,
    rejected: list[LLMRejectedCandidate] | None = None,
) -> LLMVerdict:
    return LLMVerdict(
        verdict=verdict,  # type: ignore[arg-type]
        confidence=0.7,
        note="…",
        citations=[LLMCitation(akn_eid="x", quote="…")] if verdict == "aligned" else [],
        rejected_candidates=rejected or [],
    )


def test_drops_hallucinated_eids_keeps_valid() -> None:
    cands = [_candidate("art_1", 0.74), _candidate("art_2", 0.61)]
    verdict = _verdict(
        verdict="gap",
        rejected=[
            LLMRejectedCandidate(akn_eid="art_1", reason="addresses general scrutiny only"),
            LLMRejectedCandidate(akn_eid="art_BOGUS", reason="invented"),
        ],
    )
    resolved = _resolve_rejected_candidates(verdict, cands, directive_eid="dir_eid")
    assert [r.akn_eid for r in resolved] == ["art_1"]
    assert resolved[0].similarity == 0.74


def test_floors_to_top3_stubs_on_empty_gap() -> None:
    cands = [
        _candidate("art_1", 0.74),
        _candidate("art_2", 0.61),
        _candidate("art_3", 0.55),
        _candidate("art_4", 0.42),
    ]
    verdict = _verdict(verdict="gap", rejected=[])
    resolved = _resolve_rejected_candidates(verdict, cands, directive_eid="dir_eid")
    assert [r.akn_eid for r in resolved] == ["art_1", "art_2", "art_3"]
    assert all(r.reason == "(LLM declined to articulate)" for r in resolved)
    assert resolved[0].similarity == 0.74


def test_aligned_verdict_strips_rejected_candidates() -> None:
    cands = [_candidate("art_1", 0.74)]
    verdict = _verdict(
        verdict="aligned",
        rejected=[LLMRejectedCandidate(akn_eid="art_1", reason="should not appear")],
    )
    resolved = _resolve_rejected_candidates(verdict, cands, directive_eid="dir_eid")
    assert resolved == []


def test_partial_verdict_strips_rejected_candidates() -> None:
    cands = [_candidate("art_1", 0.74)]
    verdict = _verdict(
        verdict="partial",
        rejected=[LLMRejectedCandidate(akn_eid="art_1", reason="should not appear")],
    )
    resolved = _resolve_rejected_candidates(verdict, cands, directive_eid="dir_eid")
    assert resolved == []


def test_gap_with_zero_candidates_returns_empty() -> None:
    verdict = _verdict(verdict="gap", rejected=[])
    resolved = _resolve_rejected_candidates(verdict, [], directive_eid="dir_eid")
    assert resolved == []


def test_accepts_negative_similarity() -> None:
    """Cosine similarity is in [-1, 1]; a negative top-K score must not crash."""
    cands = [_candidate("art_1", -0.12), _candidate("art_2", 0.41)]
    verdict = _verdict(
        verdict="gap",
        rejected=[LLMRejectedCandidate(akn_eid="art_1", reason="scope mismatch")],
    )
    resolved = _resolve_rejected_candidates(verdict, cands, directive_eid="dir")
    assert resolved[0].similarity == -0.12


def test_truncates_verbose_reason_instead_of_crashing() -> None:
    """A long LLM reason must persist truncated, never raise ValidationError."""
    cands = [_candidate("art_1", 0.5)]
    verdict = _verdict(
        verdict="gap",
        rejected=[LLMRejectedCandidate(akn_eid="art_1", reason="X" * 800)],
    )
    resolved = _resolve_rejected_candidates(verdict, cands, directive_eid="dir")
    assert len(resolved) == 1
    assert len(resolved[0].reason) == 400


def test_similarities_join_from_candidate_scores() -> None:
    cands = [_candidate("art_1", 0.82), _candidate("art_2", 0.41)]
    verdict = _verdict(
        verdict="gap",
        rejected=[
            LLMRejectedCandidate(akn_eid="art_2", reason="too narrow"),
            LLMRejectedCandidate(akn_eid="art_1", reason="scope mismatch"),
        ],
    )
    resolved = _resolve_rejected_candidates(verdict, cands, directive_eid="dir_eid")
    by_eid = {r.akn_eid: r.similarity for r in resolved}
    assert by_eid == {"art_1": 0.82, "art_2": 0.41}
