"""LLM-as-judge semantic adequacy scoring, after the deterministic audit and Phase 1
repair have settled. Per provision it scores coverage, fidelity, terminology and
register 0-5, derives pass/warn/fail, and returns issues that re-enter the repair
loop as ``RepairIssue`` with ``judge_*`` codes. Opt-in per ``translate_document``
(``judge_enabled``).
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from codify.core.llm import LLMClient
from codify.translate.anchors import SourceUnit

logger = structlog.get_logger()

_PROMPTS = Path(__file__).parent / "prompts"
JUDGE_SYSTEM = (_PROMPTS / "translate_judge_system.txt").read_text()

JudgeDimension = Literal["coverage", "fidelity", "terminology", "register"]
JudgeOverall = Literal["pass", "warn", "fail"]


class JudgeIssue(BaseModel):
    dimension: JudgeDimension
    detail: str
    source_span: str | None = None
    target_span: str | None = None


class JudgeVerdict(BaseModel):
    eid: str
    coverage: int = Field(ge=0, le=5)
    fidelity: int = Field(ge=0, le=5)
    terminology: int = Field(ge=0, le=5)
    register: int = Field(ge=0, le=5)
    overall: JudgeOverall
    issues: list[JudgeIssue] = Field(default_factory=list)


async def judge_provision(
    *,
    eid: str,
    source_text: str,
    translated_text: str,
    notes_block: str,
    target_language: str,
    llm: LLMClient,
    model: str | None = None,
) -> JudgeVerdict | None:
    """Score one provision. Returns None on LLM failure (the caller
    treats a missing verdict as "not judged", not as clean)."""
    prompt = (
        f"{notes_block}\n\nTarget language: {target_language}\n\n"
        f"Provision eid: {eid}\n\n"
        f"Source:\n{source_text}\n\n"
        f"Translation:\n{translated_text}\n\n"
        "Score this translation and return the JudgeVerdict JSON."
    )
    try:
        return await llm.chat_schema(prompt, JudgeVerdict, system=JUDGE_SYSTEM, model=model)
    except Exception as exc:  # noqa: BLE001, judge is best-effort
        logger.warning(
            "judge_call_failed",
            eid=eid,
            error=f"{type(exc).__name__}: {str(exc)[:160]}",
        )
        return None


def select_sample(
    units: list[SourceUnit],
    *,
    flagged_eids: Iterable[str],
    per_chapter: int = 3,
    cap: int = 20,
    seed: str = "codify",
) -> list[SourceUnit]:
    """Deterministic stratified sample of provisions to send to the judge. Units in
    ``flagged_eids`` come first, truncated at ``cap``, and fill it alone when there are
    enough of them. Otherwise the remaining budget goes on per-chapter stratified
    sampling under the caller's ``seed``, so a retry produces the same sample.
    """
    flagged = set(flagged_eids)
    # Walk-order iteration so the mandatory list is deterministic across
    # runs even for the fully-covered slice case; iterating a set would
    # give hash-order and break the retry-stability guarantee.
    mandatory = [u for u in units if u.akn_eid in flagged]

    if len(mandatory) >= cap:
        return mandatory[:cap]

    # Group remaining (clean) provisions by their nearest chapter. Use
    # depth-1 or top-of-hierarchy grouping to align with Phase 3 batching.
    by_chapter: dict[str, list[SourceUnit]] = {}
    _CHAPTER_KINDS = frozenset(
        {"book", "tome", "part", "subpart", "title", "subtitle", "chapter", "subchapter"}
    )
    current_chapter = ""
    for u in units:
        if (u.kind or "").lower() in _CHAPTER_KINDS:
            current_chapter = u.akn_eid or u.kind
            continue
        if u.akn_eid not in flagged:
            by_chapter.setdefault(current_chapter, []).append(u)

    budget = cap - len(mandatory)
    # random.Random accepts a string seed directly; sha256 rehashing is
    # redundant. Keeping the explicit seed value stable per run is what
    # gives determinism.
    rng = random.Random(seed)  # noqa: S311
    picks: list[SourceUnit] = []
    picks_per_chapter: dict[str, int] = {}
    chapter_keys = sorted(by_chapter.keys())
    while budget > 0:
        added_this_round = False
        for k in chapter_keys:
            if budget <= 0:
                break
            bucket = by_chapter.get(k) or []
            if not bucket or picks_per_chapter.get(k, 0) >= per_chapter:
                continue
            idx = rng.randrange(0, len(bucket))
            picks.append(bucket.pop(idx))
            picks_per_chapter[k] = picks_per_chapter.get(k, 0) + 1
            budget -= 1
            added_this_round = True
        if not added_this_round:
            break
    return mandatory + picks


def issues_to_repair_details(issues: list[JudgeIssue]) -> list[tuple[str, str]]:
    """Convert judge issues to ``(code, detail)`` pairs the repair loop
    can consume. ``code`` is ``judge_<dimension>``."""
    out: list[tuple[str, str]] = []
    for i in issues:
        detail = i.detail
        if i.source_span:
            detail = f"{detail} [source: {i.source_span!r}]"
        if i.target_span:
            detail = f"{detail} [target: {i.target_span!r}]"
        out.append((f"judge_{i.dimension}", detail))
    return out


__all__ = [
    "JUDGE_SYSTEM",
    "JudgeDimension",
    "JudgeIssue",
    "JudgeOverall",
    "JudgeVerdict",
    "issues_to_repair_details",
    "judge_provision",
    "select_sample",
]
