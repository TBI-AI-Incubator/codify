"""Goods-nomenclature extraction: header-typed columns and anchored prose."""

from __future__ import annotations

import pytest

from codify.pipeline.enrich.goods_codes import (
    extract_goods_codes,
    normalise,
    system_for_header,
)

AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _doc(body: str) -> str:
    return f'<akomaNtoso xmlns="{AKN}"><act><body>{body}</body></act></akomaNtoso>'


def _table(header: str, value: str) -> str:
    return _doc(
        f'<table eId="t1"><tr eId="t1__tr_1"><th><p>{header}</p></th></tr>'
        f'<tr eId="t1__tr_2"><td eId="t1__tr_2__tc_1"><p>{value}</p></td></tr></table>'
    )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("CN code", "cn"),
        ("Classification (CN code)", "cn"),
        ("Combined Nomenclature (CN) code", "cn"),
        ("HS code", "hs"),
        ("TARIC additional code", "taric"),
        ("Description of goods", None),
        ("Quota Volume", None),
        # Holds an ordinal such as "10", not a nomenclature code.
        ("TARIC sub-division", None),
    ],
)
def test_a_column_is_typed_by_its_header(label: str, expected: str | None) -> None:
    assert system_for_header(label) == expected


@pytest.mark.parametrize(
    ("raw", "digits", "partial"),
    [
        ("84713000", "84713000", False),
        ("8471 30 00", "84713000", False),
        ("8471.30.00", "84713000", False),
        ("ex 8471 30 00", "84713000", True),
        ("ex84713000", "84713000", True),
        ("847130", "847130", False),
    ],
)
def test_surface_forms_normalise_to_the_same_code(raw: str, digits: str, partial: bool) -> None:
    assert normalise(raw) == (digits, partial)


@pytest.mark.parametrize("raw", ["—", "(2)", "10", "S", "8471[.30(.00 + .10)]", "84713000/99"])
def test_a_value_that_is_not_one_code_is_refused(raw: str) -> None:
    """A truncated code reads as authoritative. A missing one can be looked up."""
    assert normalise(raw) is None


def test_ex_is_kept_because_it_changes_what_the_measure_covers() -> None:
    (code,) = extract_goods_codes(_table("CN code", "ex 8471 30 00"))
    assert (code.code, code.partial, code.surface) == ("84713000", True, "ex 8471 30 00")


def test_a_code_is_addressed_by_its_row() -> None:
    """The row is what an amending act replaces, so it is what a code cites."""
    (code,) = extract_goods_codes(_table("CN code", "84713000"))
    assert code.eid == "t1__tr_2"
    assert code.source == "column"


def test_a_code_carries_the_band_its_row_sits_under() -> None:
    """A schedule states the group once, in a row of its own. Read off the raw
    table, every code below it loses the only thing saying who it binds."""
    doc = _doc(
        '<table eId="t1">'
        '<tr eId="t1__tr_1"><th colspan="2"><p>Xanadu</p></th></tr>'
        '<tr eId="t1__tr_2"><th><p>CN code</p></th><th><p>Description</p></th></tr>'
        '<tr eId="t1__tr_3"><td><p>25070080</p></td><td><p>Calcined clay</p></td></tr>'
        "</table>"
    )
    (code,) = extract_goods_codes(doc)
    assert code.code == "25070080"
    assert code.lineage == ("Xanadu",)


def test_an_untyped_column_yields_nothing() -> None:
    assert extract_goods_codes(_table("Reference number", "84713000")) == []


def test_eight_digits_in_prose_are_not_a_code_without_an_anchor() -> None:
    """The whole point of the detector: a bare eight-digit run matches 824,861
    times across the corpus, most of them dates and quantities."""
    assert extract_goods_codes(_doc("<p>Adopted on 20140317 covering 12345678 units.</p>")) == []


def test_an_anchor_phrase_makes_the_same_digits_a_code() -> None:
    (code,) = extract_goods_codes(_doc("<p>goods falling within CN code 8471 30 00 apply</p>"))
    assert (code.system, code.code, code.source) == ("cn", "84713000", "prose")


def test_the_anchor_selects_the_system() -> None:
    codes = extract_goods_codes(_doc("<p>TARIC code 7019610081 and HS heading 847130</p>"))
    assert {(c.system, c.code) for c in codes} == {("taric", "7019610081"), ("hs", "847130")}


def test_a_code_beyond_the_anchor_window_is_not_claimed() -> None:
    far = "word " * 40
    assert extract_goods_codes(_doc(f"<p>CN code applies. {far} 84713000</p>")) == []


def test_the_same_code_in_one_place_is_reported_once() -> None:
    doc = _doc("<p>CN code 84713000, CN code 84713000</p>")
    assert len(extract_goods_codes(doc)) == 1
