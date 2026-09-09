"""Metric arithmetic on invented verdicts, not comparator accuracy evidence."""

from collections import defaultdict
from typing import Any

import pytest


def _precision_recall_by_verdict(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for row in rows:
        model = row.get("comparator_verdict")
        truth = row.get("judge_verdict")
        if model == truth:
            counts[truth]["tp"] += 1
        else:
            counts[truth]["fn"] += 1
            if model:
                counts[model]["fp"] += 1
    out: dict[str, dict[str, float]] = {}
    for verdict, c in counts.items():
        denom_p = c["tp"] + c["fp"]
        denom_r = c["tp"] + c["fn"]
        out[verdict] = {
            "precision": c["tp"] / denom_p if denom_p else 0.0,
            "recall": c["tp"] / denom_r if denom_r else 0.0,
            "support": c["tp"] + c["fn"],
        }
    return out


def test_perfect_synthetic_predictions():
    rows = [
        {"comparator_verdict": label, "judge_verdict": label}
        for label in ("aligned", "partial", "gap")
    ]
    assert _precision_recall_by_verdict(rows) == {
        label: {"precision": 1.0, "recall": 1.0, "support": 1}
        for label in ("aligned", "partial", "gap")
    }


def test_confusion_and_abstention_have_distinct_effects():
    rows = [
        {"comparator_verdict": "aligned", "judge_verdict": "aligned"},
        {"comparator_verdict": "aligned", "judge_verdict": "gap"},
        {"comparator_verdict": "gap", "judge_verdict": "gap"},
        {"comparator_verdict": None, "judge_verdict": "gap"},
    ]
    scores = _precision_recall_by_verdict(rows)
    assert scores["aligned"] == {"precision": 0.5, "recall": 1.0, "support": 1}
    assert scores["gap"]["precision"] == 1.0
    assert scores["gap"]["recall"] == pytest.approx(1 / 3)
    assert scores["gap"]["support"] == 3
    assert None not in scores


def test_empty_and_unpredicted_classes_do_not_divide_by_zero():
    assert _precision_recall_by_verdict([]) == {}
    scores = _precision_recall_by_verdict(
        [{"comparator_verdict": "partial", "judge_verdict": "gap"}]
    )
    assert scores["partial"] == {"precision": 0.0, "recall": 0.0, "support": 0}
    assert scores["gap"] == {"precision": 0.0, "recall": 0.0, "support": 1}
