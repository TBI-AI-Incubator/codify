"""Goods-nomenclature extraction: header-typed columns and anchored prose."""

from __future__ import annotations

import pytest

from codify.pipeline.enrich.goods_codes import (
    cell_codes,
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


def test_a_cell_of_code_and_description_lines_yields_each_code() -> None:
    assert [c[0] for c in cell_codes("99991000 – Widgets 99992000 – Gadgets")] == [
        "99991000",
        "99992000",
    ]


def test_codes_after_except_are_carved_out() -> None:
    """A measure naming chapter 99 "except" a heading does not cover that heading."""
    found = cell_codes("99 – Widgets of all kinds Except: 9901 2 – Spare widgets 9902 – Scrap")
    assert [(c[0], c[2]) for c in found] == [("99", False), ("99012", True), ("9902", True)]
    assert found[0][3] == "99 – Widgets of all kinds"


def test_lines_run_together_in_stored_text_still_split() -> None:
    found = cell_codes("99 –WidgetsExcept:9901 2 – Spare widgets99021000 – Scrap")
    assert [(c[0], c[2]) for c in found] == [("99", False), ("99012", True), ("99021000", True)]


def test_other_than_qualifies_a_description_and_carves_nothing_out() -> None:
    found = cell_codes("99011000 – Widgets, other than blue 99012000 – Blue widgets")
    assert [c[2] for c in found] == [False, False]


@pytest.mark.parametrize("text", ["PM10 – particles", "Carbon dioxide", "Article 5 – scope"])
def test_a_number_in_words_is_not_a_code(text: str) -> None:
    assert cell_codes(text) == []


def test_a_list_of_codes_yields_each() -> None:
    assert [c[0] for c in cell_codes("9901, 9902 and 9904")] == ["9901", "9902", "9904"]


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("9901 00 00 9902 10 00 – Widgets", ["99021000"]),
        ("9901 9902 – Widgets", ["9902"]),
        ("31.12.2025 – Expiry", []),
    ],
)
def test_neighbouring_numbers_never_merge_into_one_code(text: str, codes: list[str]) -> None:
    assert [c[0] for c in cell_codes(text)] == codes


def test_a_heading_named_in_a_description_is_not_a_listed_code() -> None:
    found = cell_codes("9908 – Parts of the widgets of headings 9901 to 9905; 9909 – Trucks")
    assert [c[0] for c in found] == ["9908", "9909"]


def test_a_range_yields_no_code_rather_than_its_last_end() -> None:
    assert cell_codes("9901 to 9905 – Widgets") == []


def test_ex_marks_an_entry_partial() -> None:
    assert [(c[0], c[1]) for c in cell_codes("Ex 9901 10 00 – Other")] == [("99011000", True)]


def test_a_word_ending_in_ex_does_not_mark_the_next_code_partial() -> None:
    found = cell_codes("99011000 – Latex99019000 – Other")
    assert [(c[0], c[1]) for c in found] == [("99011000", False), ("99019000", False)]


def test_a_carve_out_ends_at_a_code_outside_what_it_narrows() -> None:
    found = cell_codes("99 – Widgets Except: 9901 – Spare; 9902 – Scrap 9801 – Gadgets")
    assert [(c[0], c[2]) for c in found] == [
        ("99", False),
        ("9901", True),
        ("9902", True),
        ("9801", False),
    ]


def test_a_range_after_except_still_opens_the_carve_out() -> None:
    found = cell_codes("99 – Widgets Except: 9902 to 9904 – Spare 9905 – Scrap")
    assert [(c[0], c[2]) for c in found] == [("99", False), ("9905", True)]


def test_a_carve_out_with_nothing_before_it_ends_at_another_chapter() -> None:
    found = cell_codes("All of chapter 99, except: 9902 – Spare; 9904 – Waste 9801 – Gadgets")
    assert [(c[0], c[2]) for c in found] == [("9902", True), ("9904", True), ("9801", False)]


def test_a_long_run_together_cell_reads_in_linear_time() -> None:
    import time

    text = "99 –WidgetsExcept:" + "9901 2 – Spare widgets99021000 – Scrap" * 3000
    began = time.perf_counter()
    assert len(cell_codes(text)) == 6001
    assert time.perf_counter() - began < 2


def test_excluding_in_a_description_carves_nothing_out() -> None:
    found = cell_codes("9901 – Alloy excluding 10 % copper 9902 – Scrap")
    assert [c[2] for c in found] == [False, False]


def test_a_multi_line_cell_under_a_code_header_reaches_extraction() -> None:
    doc = _doc(
        '<table eId="t1"><tr eId="t1__tr_1"><th><p>CN code</p></th></tr>'
        '<tr eId="t1__tr_2"><td><p>99 – Widgets</p><p>Except:</p>'
        "<p>9901 – Spare widgets</p></td></tr></table>"
    )
    codes = extract_goods_codes(doc)
    assert [(c.code, c.excluded, c.eid) for c in codes] == [
        ("99", False, "t1__tr_2"),
        ("9901", True, "t1__tr_2"),
    ]


@pytest.mark.parametrize("marker", ["EXCEPT:", "EXCLUDING"])
def test_carve_out_markers_match_in_any_case(marker: str) -> None:
    found = cell_codes(f"99 – Widgets {marker} 9901 – Spare widgets")
    assert [(c[0], c[2]) for c in found] == [("99", False), ("9901", True)]


def test_an_uppercase_ex_marks_an_entry_partial() -> None:
    assert [(c[0], c[1]) for c in cell_codes("EX 9901 10 00 – Other")] == [("99011000", True)]
