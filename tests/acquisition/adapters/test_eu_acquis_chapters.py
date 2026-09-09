"""EU acquis-chapter classifier, directory-label → chapter mapping."""

from __future__ import annotations

from codify.acquisition.adapters.eu.acquis_chapters import (
    CHAPTER_NAMES,
    classify,
    name_for,
)


def test_empty_labels_returns_none() -> None:
    assert classify([]) is None


def test_unmatched_label_returns_none() -> None:
    assert classify(["totally unrelated topic"]) is None


def test_competition_label_resolves_chapter_8() -> None:
    assert classify(["Competition"]) == 8


def test_environment_label_resolves_chapter_27() -> None:
    assert classify(["Environment"]) == 27


def test_consumer_protection_label_resolves_chapter_28() -> None:
    assert classify(["Consumer protection"]) == 28


def test_intellectual_property_resolves_chapter_7() -> None:
    assert classify(["Intellectual property law"]) == 7


def test_public_procurement_resolves_chapter_5() -> None:
    assert classify(["Public procurement"]) == 5


def test_specific_match_wins_over_general() -> None:
    """Trade Secrets directive carries both 'Intellectual property law' and
    'Other economic and commercial provisions', IP wins."""
    assert classify(["Other economic and commercial provisions", "Intellectual property law"]) == 7


def test_case_insensitive() -> None:
    assert classify(["INTELLECTUAL PROPERTY LAW"]) == 7
    assert classify(["intellectual property law"]) == 7


def test_chapter_names_cover_all_35() -> None:
    assert set(CHAPTER_NAMES.keys()) == set(range(1, 36))


def test_name_for_known_chapter() -> None:
    # Titles now flow from the canonical YAML at `data/frameworks/eu-acquis.yaml`,
    # which sources the SPA's longer formal labels (e.g. "Intellectual property law"
    # not the pre-refactor const's shortened "Intellectual property").
    assert name_for(7) == "Intellectual property law"
    assert name_for(23) == "Judiciary and fundamental rights"


def test_name_for_unknown_returns_none() -> None:
    assert name_for(99) is None
