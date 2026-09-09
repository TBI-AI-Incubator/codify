"""Smoke tests for the EU acquis framework loader."""

from __future__ import annotations

import pytest

from codify.frameworks.acquis import (
    AcquisFramework,
    classify_directive,
    load_acquis_framework,
    threshold_for_chapter,
)


def test_load_returns_thirty_five_chapters() -> None:
    framework = load_acquis_framework()
    assert isinstance(framework, AcquisFramework)
    assert len(framework.chapters) == 35
    assert sorted(c.number for c in framework.chapters) == list(range(1, 36))


def test_classifier_priority_covers_every_chapter() -> None:
    framework = load_acquis_framework()
    priority_set = set(framework.classifier_priority)
    chapter_numbers = {c.number for c in framework.chapters}
    assert priority_set == chapter_numbers
    assert len(framework.classifier_priority) == 35


def test_clusters_include_six_ec_plus_null_bucket() -> None:
    framework = load_acquis_framework()
    ids = {c.id for c in framework.clusters}
    assert ids == {1, 2, 3, 4, 5, 6, None}


def test_every_chapter_has_classifier_labels() -> None:
    framework = load_acquis_framework()
    for chapter in framework.chapters:
        assert chapter.classifier_labels, (
            f"chapter {chapter.number} ({chapter.title}) has no classifier_labels"
        )


def test_every_chapter_has_threshold_in_unit_interval() -> None:
    framework = load_acquis_framework()
    for chapter in framework.chapters:
        assert 0.0 <= chapter.threshold_confidence <= 1.0


def test_chapters_34_and_35_have_null_cluster() -> None:
    framework = load_acquis_framework()
    by_number = {c.number: c for c in framework.chapters}
    assert by_number[34].cluster is None
    assert by_number[35].cluster is None


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["Public procurement"], 5),
        (["Intellectual property"], 7),
        (["Free movement of capital and payments"], 9),  # 9 wins over 4 by priority
        (["Free movement of capital"], 4),
        (["Free movement of goods"], 1),
        (["Energy"], 15),
        (["Statistics"], 18),
        ([], None),
        (["totally unrelated string"], None),
    ],
)
def test_classify_directive_known_cases(labels: list[str], expected: int | None) -> None:
    assert classify_directive(labels) == expected


def test_threshold_defaults_to_zero_seven() -> None:
    # All chapters ship at 0.70 in the YAML; per-chapter tuning is a follow-up
    # that touches the YAML and these tests get updated alongside.
    for chapter_no in range(1, 36):
        assert threshold_for_chapter(chapter_no) == pytest.approx(0.70)


def test_threshold_for_none_falls_back_to_default() -> None:
    assert threshold_for_chapter(None) == pytest.approx(0.70)


def test_threshold_for_unknown_chapter_falls_back_to_default() -> None:
    assert threshold_for_chapter(999) == pytest.approx(0.70)
