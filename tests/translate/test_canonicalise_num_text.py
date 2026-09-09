"""Unit tests for the write-time num-canonicalisation pass (Latin default).

`_canonicalise_num_text(root, None)` runs on the translated AKN tree and
folds every `<num>` text into the Latin canonical form:

- Arabic-Indic digits: `٥` becomes `5`.
- Abjad enumerators with a `.`/`-` separator: `أ.` becomes `a.`.
- Bare abjad letters: `أ` becomes `a`.
- Arabic ordinal words on structural containers: `الأول` becomes `1`,
  `الثاني عشر` becomes `12`. Feminine forms map to the same integer as
  the masculine so `الأولى` / `الأول` both emit `1`. Tatweel between
  letters is stripped before lookup so `الـأول` still matches.
"""

from __future__ import annotations

from lxml import etree

from codify.akn import AKN_NS
from codify.translate.write import _canonicalise_num_text


def _tree_with_nums(*nums: str) -> etree._Element:
    root = etree.Element(f"{{{AKN_NS}}}body")
    for num_text in nums:
        num = etree.SubElement(root, f"{{{AKN_NS}}}num")
        num.text = num_text
    return root


def _num_texts(root: etree._Element) -> list[str]:
    return [n.text or "" for n in root.iter(f"{{{AKN_NS}}}num")]


def test_bare_arabic_ordinal_folds_to_digit() -> None:
    root = _tree_with_nums("الأول", "الثاني", "العاشر")
    _canonicalise_num_text(root, None)
    assert _num_texts(root) == ["1", "2", "10"]


def test_compound_ordinal_folds_to_digit() -> None:
    root = _tree_with_nums("الحادي عشر", "الثاني عشر", "العشرون")
    _canonicalise_num_text(root, None)
    assert _num_texts(root) == ["11", "12", "20"]


def test_tatweel_variant_of_ordinal_still_folds() -> None:
    root = _tree_with_nums("الـأول")
    _canonicalise_num_text(root, None)
    assert _num_texts(root) == ["1"]


def test_arabic_indic_digits_still_fold() -> None:
    root = _tree_with_nums("٥", "١٢", "١")
    _canonicalise_num_text(root, None)
    assert _num_texts(root) == ["5", "12", "1"]


def test_abjad_enumerators_still_fold() -> None:
    root = _tree_with_nums("أ.", "ب-", "ج", "ه.")
    _canonicalise_num_text(root, None)
    assert _num_texts(root) == ["a.", "b-", "c", "e."]


def test_latin_text_passes_through() -> None:
    root = _tree_with_nums("1", "Article 4", "IV")
    _canonicalise_num_text(root, None)
    assert _num_texts(root) == ["1", "Article 4", "IV"]


def test_ordinal_wins_over_abjad_first_char() -> None:
    """`الأول` starts with `ا` which is in the abjad table (`a`); the
    ordinal branch runs first so the full ordinal wins the fold."""
    root = _tree_with_nums("الأول")
    _canonicalise_num_text(root, None)
    assert _num_texts(root) == ["1"]
