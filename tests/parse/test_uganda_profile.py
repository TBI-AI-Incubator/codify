"""The shipped Uganda profile, held to invented Uganda-style text: sections that
print as "1. Heading" with no keyword, Parts and Divisions above them, closing
schedules, and the revised edition's and Acts Supplement's running heads. None
of the text below is from a real statute."""

from __future__ import annotations

import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import anchor_coverage, build_anchor_regex, scan_anchors
from codify.pipeline.enrich.arabic_normalise import _compile_patterns, strip_ocr_headers_text
from codify.pipeline.enrich.enacting import emit_enacting_formula, split_opening_material
from codify.pipeline.enrich.ocr import (
    PageResult,
    _strip_furniture_lines,
    clean_page_text,
    combine_page_texts,
    furniture_inline_patterns,
    furniture_line_patterns,
)

_ACT = """\
PART I—PRELIMINARY

1. Short title
This Act may be cited as the Village Water Supply Act.

2. Interpretation
In this Act, "levy" means the levy set out in Schedule 1 to this Act.

PART II—WATER BOARDS

DIVISION I—ESTABLISHMENT

3. Establishment of water boards
(1) There is established in every district a water board.
(2) A water board shall consist of five members.

DIVISION II—FINANCE

4. Levy
A water board may charge the levy at the rates in Schedule 1.

5. Accounts
A water board shall keep proper accounts.

SCHEDULES

Schedule 1
Rates of levy, in shillings each month.

Schedule 2
Form of application for a connection.
"""


def _anchors() -> list:
    regex = build_anchor_regex(load_config("ug"), "act")
    return scan_anchors(_ACT, regex, country="ug", doctype="act")


def test_a_bare_numbered_heading_is_a_section() -> None:
    sections = [a for a in _anchors() if a.kind == "section"]
    assert [a.number for a in sections] == ["1", "2", "3", "4", "5"]


def test_parts_divisions_and_sections_nest() -> None:
    eids = [a.akn_eid for a in _anchors() if a.kind != "schedule"]
    assert eids == [
        "part_I",
        "part_I__sec_1",
        "part_I__sec_2",
        "part_II",
        "part_II__dvs_I",
        "part_II__dvs_I__sec_3",
        "part_II__dvs_II",
        "part_II__dvs_II__sec_4",
        "part_II__dvs_II__sec_5",
    ]


def test_closing_schedules_sit_outside_the_parts() -> None:
    schedules = [a for a in _anchors() if a.kind == "schedule"]
    assert [(a.akn_eid, a.depth) for a in schedules] == [("schedule_1", 0), ("schedule_2", 0)]


def test_a_schedule_named_in_prose_is_not_a_container() -> None:
    # Sections 2 and 4 both name Schedule 1; only the closing heading anchors it.
    schedules = [a for a in _anchors() if a.kind == "schedule"]
    assert all(a.char_offset > _ACT.index("SCHEDULES") for a in schedules)


_BODY = "(2) A water board shall consist of five members."

_NEXT = "(3) A member shall hold office for three years."

_HEADS = (
    "4410 Cap. 999.] Village Water Supply Act",
    "4410 Cap. 999. Village Water Supply Act",
    "Village Water Supply Act [Cap. 999. 4411",
    "Act 9          Village Water Supply Act          2031",
    "Act 4 Village Water Supply (Amendment) (No. 2) Act 2032",
    "Village Keepers with Licences Act [Cap. 999. 4411",
    "Act 9 Village Water or Sanitation Act 2031",
    "4412 Cap. 999.] Village Levy Act (Amendment) Act",
    "4412 Cap. 999.]\u00a0Village Water Supply Act",
    "Act\u00a09 Village Water Supply Act",
    "## Village Water Supply Act [Cap. 999. 4411",
)

# Text that must stay on any line of a page, the first and last included.
_TEXT_LINES = (
    "Act 3 of the Water Act",
    "Act 3 amends the Water Act",
    "act 9 village water supply act 2031",
    "4412 cap. 999.] village water supply act",
    "of five members. Village Water Supply Act [Cap. 999. 4411",
    "of five members. Act 9 Village Water Supply Act 2031",
    "Village Water Supply Act [Cap. 999. 4411 of five members.",
    "Act 9 Village Water Supply Act 2031 of five members.",
    "PART II—WATER BOARDS Village Water Supply Act [Cap. 999. 4411",
    "FIRST SCHEDULE Village Water Supply Act [Cap. 999. 4411",
    "(2) The Minister and the Board of the Village Water Supply Act [Cap. 999. 4411",
    "(a) the Minister and the Board of the Village Water Supply Act [Cap. 999. 4411",
    "Act 9 PART II—WATER BOARDS Village Water Supply Act 2031",
    "The Revised Edition 2000 Cap. 999. Village Water Supply Act shall apply.",
    "Under section 101 Cap. 999. Village Water Supply Act applies.",
    "The Board shall appoint the Village Water Supply Act [Cap. 999. 4411",
    "A Board of the Water Act [Cap. 999. 4411",
    "12 Cap. 999. Village Water Supply Act",
    "Village Water Supply Act [Cap. 999. 12",
    "Village Water Supply Act [Cap. 999. 441234",
    "Act 3 Water PART II Act",
)

_TITLES = st.lists(
    st.sampled_from(("Water", "Supply", "(Amendment)", "(No.", "2)", "of", "and", "Boards")),
    max_size=5,
).map(lambda words: " ".join(["Village", *words]))
_PROSE = st.lists(
    st.sampled_from(
        ("the", "board", "shall", "under", "this", "Act", "subject", "to", "Minister", "water")
    ),
    min_size=1,
    max_size=12,
).map(" ".join)
_PARAGRAPHS = st.lists(_PROSE, min_size=1, max_size=4)


def _combined(*texts: str) -> list[str]:
    """Each text as a page through the combining path, as its non-blank lines."""
    furniture = furniture_inline_patterns("ug")
    pages = [
        PageResult(page_number=n, text=text, method="ocr", furniture=furniture)
        for n, text in enumerate(texts, start=1)
    ]
    return [line.strip() for line in combine_page_texts(pages).splitlines() if line.strip()]


class TestRunningHeads:
    def test_revised_edition_heads_go_and_the_body_stays(self) -> None:
        page = (
            f"4410 Cap. 999.] Village Water Supply Act\n{_BODY}\n"
            "Village Water Supply Act [Cap. 999. 4411"
        )
        out = _strip_furniture_lines(page, furniture_line_patterns("ug"))
        assert out == _BODY

    def test_acts_supplement_head_goes_with_or_without_its_year(self) -> None:
        for head in (
            "Act 9          Village Water Supply Act          2031",
            "Act 9 Village Water Supply Act",
            "Act 4 Village Water Supply (Amendment) (No. 2) Act 2032",
        ):
            assert _combined(f"{head}\n{_BODY}") == [_BODY]

    def test_a_body_line_that_starts_with_an_act_number_stays(self) -> None:
        for line in (
            "Act 3 of 2001 is repealed by this Act",
            "Act 3 amends the Water Act",
            "Act 3 of the Water Act",
            "act 9 village water supply act",
        ):
            assert _strip_furniture_lines(line, furniture_line_patterns("ug")) == line

    def test_a_line_with_prose_beside_its_head_stays(self) -> None:
        for line in (
            "4412 Cap. 999.] Village Water Supply Act of five members.",
            "of five members. Village Water Supply Act [Cap. 999. 4411",
        ):
            assert _strip_furniture_lines(line, furniture_line_patterns("ug")) == line

    def test_the_line_set_reads_heads_as_the_page_set_does(self) -> None:
        patterns = furniture_line_patterns("ug")
        for head in _HEADS:
            if not head.startswith(("#", "Act")):
                assert _strip_furniture_lines(head, patterns) == ""
            elif head.startswith("Act"):
                assert _strip_furniture_lines(head, patterns) == head
        for line in _TEXT_LINES:
            assert _strip_furniture_lines(line, patterns) == line

    def test_the_translation_set_is_the_line_set(self) -> None:
        config = load_config("ug")
        assert config.ocr_header_patterns == config.furniture_line_patterns
        compiled = _compile_patterns(config.ocr_header_patterns)
        assert strip_ocr_headers_text("Act 3 of the Water Act", compiled)[1] == 0

    def test_a_head_fused_into_a_sentence_is_scrubbed(self) -> None:
        line = (
            "A water board shall consist 4412 Cap. 999.] Village Water Supply Act of five members."
        )
        out = clean_page_text(line, furniture_inline_patterns("ug"))
        assert "Cap. 999" not in out
        assert out.startswith("A water board shall consist")
        assert out.endswith("of five members.")

    def test_a_fused_head_ends_at_its_first_act(self) -> None:
        for line, kept in (
            (
                "A board shall 4412 Cap. 999.] Village Water Supply Act subject to this Act "
                "appoint five members.",
                "A board shall subject to this Act appoint five members.",
            ),
            (
                "A board shall 4412 Cap. 999.] Village Water Supply Act of the Water Act appoint.",
                "A board shall of the Water Act appoint.",
            ),
            (
                "shall be paid 8216 Cap. 999.] Village Water Supply Act the employer under "
                "this Act shall",
                "shall be paid the employer under this Act shall",
            ),
        ):
            assert clean_page_text(line, furniture_inline_patterns("ug")) == kept

    def test_a_fused_head_with_a_misread_act_takes_no_prose(self) -> None:
        line = (
            "A board 4412 Cap. 999.] Village Water Supply Aet and the Board of the Water Act sat."
        )
        assert "and the Board of the Water Act sat." in clean_page_text(
            line, furniture_inline_patterns("ug")
        )

    def test_a_fused_head_needs_its_number_bracket_and_title(self) -> None:
        for line in (
            "A board 12 Cap. 999.] Water Act sat.",
            "A board 144123 Cap. 999.] Water Act sat.",
            "A board 4412 Cap. 999. Water Act sat.",
            "A board 4412 Cap. 999.] of Water Act sat.",
            "A board 4412 Cap. 999.] A Water Act sat.",
            "A board 4412 Cap. 999.] Water PART Act sat.",
            "A board 4412 Cap. 999.] The Actor sat.",
        ):
            assert clean_page_text(line, furniture_inline_patterns("ug")) == line

    def test_a_fused_head_takes_a_non_breaking_space(self) -> None:
        line = "A board 4412 Cap. 999.]\u00a0Village Water Supply Act sat."
        assert "Cap. 999" not in clean_page_text(line, furniture_inline_patterns("ug"))

    def test_every_head_leaves_the_edges_of_a_page(self) -> None:
        # Page bodies are cleaned with the inline set alone, not the line patterns.
        pages = [f"{head}\n{_BODY}\n{head}" for head in _HEADS]
        assert _combined(*pages) == [_BODY] * len(_HEADS)

    def test_a_head_goes_from_a_page_with_trailing_line_ends(self) -> None:
        head = "Act 9 Village Water Supply Act"
        assert _combined(f"{_BODY}\n{head}\n") == [_BODY]
        assert _combined(f"{head}\r\n{_BODY}\r\n") == [_BODY]

    def test_one_line_goes_from_each_edge(self) -> None:
        # A run of rows at an edge is a schedule more often than it is furniture.
        rows = [
            "Act 12 Village Sanitation Act",
            "Act 13 Village Water Boards Act 2030",
            "Act 14 Village Wells (Amendment) Act 2029",
        ]
        assert _combined("\n".join([*rows, _BODY])) == [*rows[1:], _BODY]
        assert _combined("\n".join([_BODY, *rows])) == [_BODY, *rows[:-1]]

    def test_a_run_of_head_shaped_rows_cleans_at_once(self) -> None:
        # Cleaning runs on the event loop, and re cannot be interrupted, so a
        # pattern that backtracks on this run would hang ingestion.
        rows = "\n".join(f"    Act {n} Village Water Supply Act" for n in range(10))
        started = time.perf_counter()
        clean_page_text(f"{_BODY}\n{rows}\n{_NEXT}", furniture_inline_patterns("ug"))
        assert time.perf_counter() - started < 0.5

    def test_a_head_shaped_line_mid_page_stays(self) -> None:
        for line in ("Act 12 Village Water Supply Act", "Act 3 Schedule to the Water Act"):
            assert _combined(f"{_BODY}\n{line}\n{_NEXT}") == [_BODY, line, _NEXT]

    def test_the_number_under_a_masthead_on_page_one_stays(self) -> None:
        page = "ACTS SUPPLEMENT No. 9\nAct 9 Village Water Supply Act 2031\nTHE VILLAGE WATER ACT"
        assert "Act 9 Village Water Supply Act 2031" in _combined(page)

    def test_text_stays_on_every_line_of_a_page(self) -> None:
        for line in _TEXT_LINES:
            assert _combined(f"{line}\n{_BODY}\n{_NEXT}")[0] == line
            assert _combined(f"{_BODY}\n{line}\n{_NEXT}")[1] == line
            assert _combined(f"{_BODY}\n{_NEXT}\n{line}")[2] == line

    def test_a_match_does_not_cross_a_line_break(self) -> None:
        for text in (
            "Act 3\nVillage Water Supply Act",
            "Act 3 Village Water\nSupply Act 2031",
            "Village Water Supply Act\n[Cap. 999. 4411",
            "4412 Cap. 999.]\nVillage Water Supply Act",
        ):
            assert clean_page_text(text, furniture_inline_patterns("ug")) == text

    @given(before=_PROSE, title=_TITLES, after=_PROSE)
    def test_a_fused_head_takes_nothing_but_itself(
        self, before: str, title: str, after: str
    ) -> None:
        line = f"{before} 4412 Cap. 999.] {title} Act {after}"
        assert clean_page_text(line, furniture_inline_patterns("ug")) == f"{before} {after}"

    @given(title=_TITLES, before=_PARAGRAPHS, after=_PARAGRAPHS)
    def test_a_head_goes_from_the_edges_and_stays_between_them(
        self, title: str, before: list[str], after: list[str]
    ) -> None:
        for head in (f"{title} Act [Cap. 999. 4413", f"Act 9 {title} Act 2031"):
            page = "\n".join([head, *before, head, *after, head])
            assert _combined(page) == [*before, head, *after]

    @given(
        head=st.sampled_from(_HEADS),
        lines=st.lists(st.sampled_from((_BODY, _NEXT, *_TEXT_LINES)), min_size=1, max_size=4),
    )
    def test_cleaning_a_page_twice_changes_nothing(self, head: str, lines: list[str]) -> None:
        furniture = furniture_inline_patterns("ug")
        once = clean_page_text("\n".join([head, *lines, head]), furniture)
        assert clean_page_text(once, furniture) == once


@pytest.mark.parametrize("ending", [":", "—"])
def test_a_printed_enacting_formula_is_not_duplicated(ending: str) -> None:
    formula = f"BE IT ENACTED by Parliament as follows{ending}"
    preface, preamble = split_opening_material(f"Village Water Supply Act\n{formula}", "ug")
    assert preface == "Village Water Supply Act"
    assert preamble == formula
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        f"<act><preamble><p>{preamble}</p></preamble><body/></act></akomaNtoso>"
    )
    assert emit_enacting_formula(xml, "ug", "act", "2031-01-01") == xml


def test_translation_preserves_an_act_citation_without_page_context() -> None:
    line = "Act 12 Village Water Supply Act"
    patterns = _compile_patterns(load_config("ug").ocr_header_patterns)
    assert strip_ocr_headers_text(line, patterns) == (line, 0)
    assert split_opening_material(line, "ug") == (line, None)
    assert _combined(f"{line}\n{_BODY}") == [_BODY]


@pytest.mark.parametrize("number", ["1A", "1B", "1AA", "A1"])
def test_lettered_sections_are_anchored_and_counted(number: str) -> None:
    text = (
        "1. Establishment\nA board is established.\n"
        f"{number}. Records\nThe board keeps records.\n"
        "2. Accounts\nThe board keeps accounts."
    )
    config = load_config("ug")
    anchors = scan_anchors(text, build_anchor_regex(config, "act"), country="ug", doctype="act")
    assert [a.number for a in anchors if a.kind == "section"] == ["1", number, "2"]
    kept = [a for a in anchors if a.number != number]
    coverage = anchor_coverage(text, kept, config, "act", "section")
    assert coverage.expected == frozenset({"1", number, "2"})
    assert coverage.missing == frozenset({number})
    assert coverage.ratio == pytest.approx(2 / 3)
