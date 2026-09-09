"""Ranking metrics over (ranked provision ids, relevant provision ids).

Pure functions on ids, so they unit-test without a database. `evaluate` is the
entry point the runner calls once per (query, arm).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Share of the relevant set the first `k` results reach."""
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: set[str]) -> float:
    """1/rank of the first relevant result, 0 when none is retrieved."""
    for position, provision_id in enumerate(ranked, start=1):
        if provision_id in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Binary-gain nDCG. Graded relevance would need a judged scale we do not
    have; binary is what a (query, relevant-provisions) gold set supports."""
    if not relevant:
        return 0.0
    gain = sum(
        1.0 / math.log2(position + 1)
        for position, provision_id in enumerate(ranked[:k], start=1)
        if provision_id in relevant
    )
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, min(len(relevant), k) + 1))
    return gain / ideal if ideal else 0.0


def evaluate(ranked: Sequence[str], relevant: set[str]) -> dict[str, float]:
    """The four numbers #590 asks for, for one query against one arm."""
    return {
        "recall@10": recall_at_k(ranked, relevant, 10),
        "recall@50": recall_at_k(ranked, relevant, 50),
        "ndcg@10": ndcg_at_k(ranked, relevant, 10),
        "mrr": reciprocal_rank(ranked, relevant),
    }


def mean_scores(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Macro-average over queries. Macro not micro: a language with few
    queries should not be drowned by one with many."""
    if not rows:
        return {name: 0.0 for name in ("recall@10", "recall@50", "ndcg@10", "mrr")}
    keys = ("recall@10", "recall@50", "ndcg@10", "mrr")
    return {key: sum(float(r[key]) for r in rows) / len(rows) for key in keys}


__all__ = ["evaluate", "mean_scores", "ndcg_at_k", "recall_at_k", "reciprocal_rank"]
