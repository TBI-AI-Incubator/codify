"""Tests for the num-canonicalisation backfill helper."""

from __future__ import annotations

from codify.akn import AKN_NS
from codify.translate.recanonicalise_nums import recanonicalise_nums


def _akn(nums: list[str]) -> str:
    inner = "".join(f"<num>{n}</num>" for n in nums)
    return f'<akomaNtoso xmlns="{AKN_NS}"><act name="act">{inner}</act></akomaNtoso>'


def test_hebrew_backfill_flips_arabic_and_latin_to_letter_numerals() -> None:
    """A version translated under the target-blind path shipped Latin
    `<num>` values in a Hebrew delivery. The backfill rewrites both the
    unfolded Arabic input (in case the source AKN was cloned verbatim)
    and the folded Latin (in case the old write pass already ran)."""
    xml = _akn(["الأول", "الثاني عشر", "أ.", "٥", "1", "a."])
    new_xml, changed = recanonicalise_nums(xml, "he")
    assert new_xml is not None
    assert changed == 6
    assert "<num>א</num>" in new_xml
    assert "<num>יב</num>" in new_xml
    assert "<num>א.</num>" in new_xml
    assert "<num>ה</num>" in new_xml


def test_already_canonical_returns_none() -> None:
    """Idempotent: a tree that already matches the target's canonical
    form is a no-op so the caller can skip the write."""
    xml = _akn(["א", "ב", "יב"])
    new_xml, changed = recanonicalise_nums(xml, "he")
    assert new_xml is None
    assert changed == 0


def test_english_target_stays_latin() -> None:
    """The backfill also supports flipping a Hebrew-lettered AKN into a
    Latin one (e.g. to unify a legacy fork). The default target routes
    to Latin, matching the current translator path."""
    xml = _akn(["الأول", "أ."])
    new_xml, changed = recanonicalise_nums(xml, "en")
    assert new_xml is not None
    assert changed == 2
    assert "<num>1</num>" in new_xml
    assert "<num>a.</num>" in new_xml
