"""Instruments and rules scanned from PDF: how their articles, page furniture and attachments
reach the anchor scan. The text is invented; the layouts are those these PDFs print."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

import codify.jurisdictions as jurisdictions
from codify.jurisdictions import JurisdictionConfigError
from codify.pipeline.enrich import anchors as anchor_module
from codify.pipeline.enrich.anchors import (
    UK_PROVISIONS_PASS,
    StructuralAnchor,
    _body_end,
    _caption_label,
    _drop_schedule_cross_references,
    build_anchor_regex,
    cached_regex,
    scan_anchors,
    scan_anchors_with_ambiguity,
    uk_layout_held,
)
from codify.pipeline.enrich.ocr import (
    OcrBlock,
    PageDimensions,
    PageLayout,
    PageResult,
    combine_page_texts_with_spans,
)
from codify.pipeline.enrich.region_text import combine_text_for_structure
from codify.pipeline.enrich.regions import classify_page
from codify.pipeline.enrich.structure import AnchorCoverageError, text_to_bluebell_scaffolded
from codify.pipeline.formats import pdf
from tests.config_fixtures import isolated_configs
from tests.parse.test_scan_trace import _EmptyBodies

Declare = Callable[..., None]


def _clear_scan_caches() -> None:
    """The scan memoises what it reads from a config, per country code."""
    for name in dir(anchor_module):
        cached = getattr(anchor_module, name)
        if callable(getattr(cached, "cache_clear", None)):
            cached.cache_clear()


@pytest.fixture
def declare_gb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Declare]:
    """`declare_gb(**fields)` swaps in the shipped config with those fields replaced."""
    with contextlib.ExitStack() as stack:

        def declare(**fields: Any) -> None:
            base = json.loads((jurisdictions.JURISDICTIONS_DIR / "gb" / "config.json").read_text())
            base.update(fields)
            stack.enter_context(isolated_configs(monkeypatch, tmp_path / "declared", {"gb": base}))
            _clear_scan_caches()

        yield declare
    _clear_scan_caches()


_SUBSTITUTION = {"trigger_phrases": ["shall be substituted the following"]}
_INSTRUMENT_ATTACHMENTS = [
    {"caption": "EXPLANATORY NOTE", "normative": False, "prefix": True, "always_opens": True},
    {"caption": "THE SCHEDULE", "prefix": True, "always_opens": True},
    {"caption": "SCHEDULE", "prefix": True, "always_opens": True},
]


def _scan(text: str) -> Any:
    config = jurisdictions.load_config("gb")
    return scan_anchors_with_ambiguity(
        text, build_anchor_regex(config, "nisr"), country="gb", doctype="nisr"
    )


def _sections(anchors: list[StructuralAnchor]) -> list[str | None]:
    return [a.number for a in anchors if a.kind == "section" and not a.quoted_amendment]


def _schedules(anchors: list[StructuralAnchor]) -> list[str | None]:
    return [a.heading for a in anchors if a.kind == "schedule"]


# --- articles ----------------------------------------------------------------------------

_BODY = "A harbour authority must keep a register of widgets in full."
_FORMS = [
    "{n}.—(1) " + _BODY,
    "{n}. —(1) " + _BODY,
    "{n}.-(1) " + _BODY,
    "{n}.-( 1) " + _BODY,
    "**{n}.**—(1) " + _BODY,
]


@pytest.mark.parametrize("form", _FORMS)
def test_an_article_opening_on_its_first_paragraph_is_a_unit(
    form: str, declare_gb: Declare
) -> None:
    """The number and the `(1)` are one marker, however the dash and the spacing print."""
    declare_gb()
    text = "The Minister makes the following Order:\n"
    text += "".join(f"{form.format(n=n)}\n(2) The register is public.\n" for n in (1, 2, 3))
    text += "Dated 1st April 2031.\n"
    assert _sections(_scan(text).anchors) == ["1", "2", "3"]


def test_a_stray_dot_after_the_number_does_not_hide_the_article(declare_gb: Declare) -> None:
    declare_gb()
    text = (
        "The Department makes the following Rules:\n"
        "Citation and commencement\n"
        "1.· These Rules may be cited as the Harbour Rules\n"
        "(Northern Ireland) 2031 and shall come into operation on 1st March 2031.\n"
        "Interpretation\n"
        "2. In these Rules-\n"
        '"the principal Rules" means the Harbour Rules (Northern Ireland) 2030.\n'
        "Application\n"
        "3. The principal Rules shall have effect subject to the amendments set out\n"
        "in the Schedule.\n"
        "Dated 26th January 2031.\n"
    )
    assert _sections(_scan(text).anchors) == ["1", "2", "3"]


def test_a_bold_number_heads_a_unit(declare_gb: Declare) -> None:
    declare_gb()
    text = (
        "**1.** This Act may be cited as the Harbour Act 2031.\n\n"
        "**2.** In this Act, unless the context otherwise requires—\n\n"
        "“the Council” means the Harbour Council;\n\n"
        "“the harbour” means the harbour of the Council.\n\n"
        "**3.** The Council may keep a register of widgets in full\n\n"
        "and shall make the register available for inspection.\n"
    )
    assert _sections(_scan(text).anchors) == ["2", "3"]


# --- page numbers ------------------------------------------------------------------------

_ARTICLES = [
    "1.-(1) These Regulations may be cited as the Widget Regulations.\n(2) They come into force.\n",
    "2.-(1) A widget is registered when entered in the register.\n(2) The register is public.\n",
    "3.-(1) The register must be kept up to date and open to inspection.\n(2) It is free.\n",
    "4.-(1) The authority must publish the register each year in full.\n(2) That is final.\n",
]


def test_a_page_number_beside_a_running_title_is_not_a_unit(declare_gb: Declare) -> None:
    """`842 Social Security` reads as `N Heading`; the articles beside it are 2 and 3."""
    declare_gb()
    a1, a2, a3, a4 = _ARTICLES
    text = f"Made today:\n{a1}{a2}842 Social Security\n{a3}{a4}Dated 1st April 2031.\n"
    scan = _scan(text)
    assert _sections(scan.anchors) == ["1", "2", "3", "4"]
    assert scan.fires["drop_uk_page_numbers"] == -1


def test_a_run_of_page_numbers_falls_together(declare_gb: Declare) -> None:
    """A head on consecutive pages: the one beside an article falls first, then the next."""
    declare_gb()
    a1, a2, _, _ = _ARTICLES
    text = f"Made today:\n1223 Education\n1224 Education\n{a1}{a2}Dated 1st April 2031.\n"
    scan = _scan(text)
    assert _sections(scan.anchors) == ["1", "2"]
    assert scan.fires["drop_uk_page_numbers"] == -2


def test_a_lettered_number_beside_small_ones_is_not_a_unit(declare_gb: Declare) -> None:
    """A grid reference at the start of a line reads as `302944G Heading`."""
    declare_gb()
    a1, a2, _, _ = _ARTICLES
    grid = "302944G Dysynni, which is crossed by the bridge\n"
    text = f"Made today:\n{a1}{a2}{grid}Dated 1st April 2031.\n"
    assert _sections(_scan(text).anchors) == ["1", "2"]


def _act(sections: int, after: str = "") -> str:
    """Sections printed `N Heading`, each with two subsections, then a schedule whose
    numbered paragraphs read as sections of their own. `after` is a line set between."""
    text = "An Act to make provision about harbours.\n"
    for n in range(1, sections + 1):
        text += f"{n} Duty number {n} of the authority\n"
        text += f"(1) The authority must perform duty {n} in full.\n(2) The duty is owed to all.\n"
    text += after
    text += "SCHEDULE 1\nSection 5\nCONSTITUTION OF THE AUTHORITY\n"
    text += "1 The Authority shall consist of a chair and not more\nthan ten other members.\n"
    return text


def test_numbering_that_reaches_its_neighbours_is_not_page_numbers(declare_gb: Declare) -> None:
    """Sections 100 to 120 end beside the schedule's paragraph 1, but they run on from 99."""
    declare_gb()
    scan = _scan(_act(120))
    assert [n for n in _sections(scan.anchors) if n and int(n) > 99] == [
        str(n) for n in range(100, 121)
    ]
    assert scan.fires["drop_uk_page_numbers"] == 0


def test_the_last_section_before_a_restart_is_kept(declare_gb: Declare) -> None:
    """Section 100 follows 99, and the schedule's paragraph 1 after it does not make it
    a page number: it is judged by the larger of the two numbers beside it."""
    declare_gb()
    scan = _scan(_act(100))
    assert "100" in _sections(scan.anchors)
    assert scan.fires["drop_uk_page_numbers"] == 0


def test_a_run_is_judged_by_its_smallest_number(declare_gb: Declare) -> None:
    """Section 100 and a page number printed after it make one run, and the run does not
    tower over 99: only a run all of whose members do is page numbers."""
    declare_gb()
    scan = _scan(_act(100, after="900 Social Security\nThe Authority keeps the register.\n"))
    assert "100" in _sections(scan.anchors)
    assert scan.fires["drop_uk_page_numbers"] == 0


def test_a_page_number_is_judged_by_the_sections_beside_it_in_the_text(
    declare_gb: Declare,
) -> None:
    """Articles opening on `(1)` and sections printed `N Heading` are found apart, but the
    page number is read among the articles around it, not among the sections found with it."""
    declare_gb()
    text = "Made today:\n"
    for n in range(1, 120):
        text += f"{n}.-(1) The authority must keep register {n} in full.\n(2) It is public.\n"
        if n == 60:
            text += "842 Social Security\n"
    text += "120 Final provisions\nThe Minister may revoke this Order by a later order.\n"
    found = _sections(_scan(text).anchors)
    assert "842" not in found
    assert "120" in found


def test_a_longer_run_of_large_numbers_is_numbering(declare_gb: Declare) -> None:
    """Page numbers come a few at a time; four in a row beside small ones are sections."""
    declare_gb()
    a1, a2, _, _ = _ARTICLES
    run = "".join(
        f"{n} Heading number {n} of the scheme\nThe scheme applies to {n}.\n"
        for n in (200, 201, 202, 203)
    )
    text = f"Made today:\n{a1}{a2}{run}Dated 1st April 2031.\n"
    assert _sections(_scan(text).anchors) == ["1", "2", "200", "201", "202", "203"]


_WELSH_MONTHS = [
    "Ionawr",
    "Chwefror",
    "Mawrth",
    "Ebrill",
    "Mai",
    "Mehefin",
    "Gorffennaf",
    "Awst",
    "Medi",
    "Hydref",
    "Tachwedd",
    "Rhagfyr",
]


@pytest.mark.parametrize("month", _WELSH_MONTHS)
def test_a_welsh_date_is_not_a_unit(month: str, declare_gb: Declare) -> None:
    declare_gb()
    a1, a2, _, _ = _ARTICLES
    text = f"Made today:\n{a1}{a2}20 {month} 2014\nDated 1st April 2031.\n"
    assert _sections(_scan(text).anchors) == ["1", "2"]


def _unit(
    number: str,
    source_pass: str = UK_PROVISIONS_PASS,
    *,
    kind: str = "section",
    at: int = 0,
) -> StructuralAnchor:
    return StructuralAnchor(
        kind=kind,
        keyword="",
        number=number,
        char_offset=at,
        line=1,
        matched_text=number,
        source_pass=source_pass,
    )


@pytest.mark.parametrize(
    ("units", "held"),
    [
        ((_unit("1"), _unit("2", at=10)), True),
        ((_unit("2", at=10), _unit("1")), True),
        ((_unit("1"), _unit("2", at=10), _unit("7", at=20)), True),
        ((_unit("7"), _unit("1", at=10), _unit("2", at=20)), False),
        ((_unit("1"),), False),
        ((_unit("2"), _unit("3", at=10)), False),
        ((_unit("1", "regex"), _unit("2", "regex", at=10)), False),
        ((_unit("1"), _unit("2", "regex", at=10)), False),
        ((_unit("1", kind="subsection"), _unit("2", kind="subsection", at=10)), False),
    ],
    ids=[
        "one and two",
        "out of order",
        "a later stray",
        "an earlier stray",
        "one alone",
        "two and three",
        "other pass",
        "mixed passes",
        "subsections",
    ],
)
def test_the_layout_is_held_when_the_uk_scan_read_units_one_and_two(
    units: tuple[StructuralAnchor, ...], held: bool
) -> None:
    """A flattened source yields one stray unit; a held layout yields the first two."""
    assert uk_layout_held("x" * 100, units, "section", "gb") is held


def _front_text(front: str, caption: str) -> tuple[str, list[StructuralAnchor]]:
    """Front matter, two lined provisions, then a schedule whose caption is `caption`."""
    text = f"Made.\n{front}1. First\n2. Second\n" + "x" * 20 + f"\n{caption}\n1. Row\n"
    units = [
        _unit("1", at=text.index("1. First")),
        _unit("2", at=text.index("2. Second")),
        _unit("1", "regex", kind="hcontainer", at=text.index(f"\n{caption}\n") + 1),
    ]
    return text, units


_NOTE = "EXPLANATORY NOTE\nNot part of the Order.\n\n"


@pytest.mark.parametrize(
    ("front", "caption", "held"),
    [
        ("SCHEDULE 1 Fees\n\n", "SCHEDULE 1 Fees", True),
        ("SCHEDULE 1 Fees\n\n", "SCHEDULE 1", True),
        ("SCHEDULE Fees\n\n", "SCHEDULE Fees", True),
        ("SCHEDULE Fees\n\n", "SCHEDULE", False),
        ("THE SCHEDULE TO THE\n\n", "THE SCHEDULE TO THE SCHEME", False),
        (_NOTE, "SCHEDULE 1 Fees", True),
        ("SCHEDULE 1 Fees\n\n", "SCHEDULE 2 Forms", False),
        ("SCHEDULE 1 Fees\n\n", "SCHEDULE Fees", False),
        ("SCHEDULE 1 Fees\n\n", "EXPLANATORY NOTE", False),
    ],
    ids=[
        "contents entry",
        "contents entry, caption without its title",
        "contents entry, unnumbered and printed again",
        "contents entry, unnumbered and printed without its title",
        "a schedule whose caption wraps, then another",
        "cover note",
        "a schedule not printed again",
        "numbered then unnumbered",
        "a schedule the caption below is not",
    ],
)
def test_a_caption_before_the_first_provision_is_front_matter_only_if_a_note_or_printed_again(
    front: str, caption: str, held: bool, declare_gb: Declare
) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text, units = _front_text(front, caption)
    assert uk_layout_held(text, units, "section", "gb") is held


@pytest.mark.parametrize(
    ("line", "label"),
    [
        ("SCHEDULE 1 Fees", ("SCHEDULE", "1", "")),
        ("THE SCHEDULE TO THE SCHEME", ("SCHEDULE", "", "TO THE SCHEME")),
        ("YR ATODLEN 2a Ffioedd", ("ATODLEN", "2A", "")),
        ("SCHEDULE Fees 3", ("SCHEDULE", "", "FEES 3")),
        ("", ("", "", "")),
    ],
)
def test_a_caption_label_is_its_keyword_and_number(line: str, label: tuple[str, str, str]) -> None:
    assert _caption_label(line) == label


@pytest.mark.parametrize(
    "stray",
    [_unit("2", kind="paragraph"), _unit("3", "regex")],
    ids=["a parenthesised number", "a keyword section"],
)
def test_only_a_unit_the_uk_scan_read_as_a_section_is_the_first_provision(
    stray: StructuralAnchor, declare_gb: Declare
) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text, units = _front_text(_NOTE, "SCHEDULE 1 Fees")
    assert uk_layout_held(text, [stray, *units], "section", "gb") is True


def test_with_no_provision_read_the_body_is_sought_from_the_start(declare_gb: Declare) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text = "Made.\nSCHEDULE 1 Fees\n1. Row\n"
    assert _body_end(text, [], "gb") == text.index("SCHEDULE")
    note = "Made.\nEXPLANATORY NOTE\nNot part of the Order.\n"
    assert _body_end(note, [], "gb") == note.index("EXPLANATORY")


def test_an_anchored_caption_before_the_first_provision_is_front_matter_when_printed_again(
    declare_gb: Declare,
) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text, units = _front_text("SCHEDULE 1 Fees\n\n", "SCHEDULE 1 Fees")
    units.append(_unit("1", "regex", kind="hcontainer", at=text.index("SCHEDULE 1 Fees")))
    assert uk_layout_held(text, units, "section", "gb") is True


def test_a_prose_line_that_starts_like_a_caption_is_no_twin(declare_gb: Declare) -> None:
    """A contents entry is front matter only when a caption the scan can stand behind follows."""
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text, units = _front_text("SCHEDULE 1 Fees\n\n", "EXPLANATORY NOTE")
    text += "Schedule 1 sets out the fees.\n"
    units.append(_unit("1", "regex", kind="hcontainer", at=text.index("Schedule 1 sets")))
    units.append(_unit("1", "regex", kind="hcontainer", at=text.index("SCHEDULE 1 Fees")))
    assert uk_layout_held(text, units, "section", "gb") is False


def test_a_schedule_the_scan_anchored_vouches_for_its_contents_entry(declare_gb: Declare) -> None:
    """With no caption declared, the annex scan's own anchor is the caption that follows."""
    declare_gb()
    text, units = _front_text("SCHEDULE 1 Fees\n\n", "SCHEDULE 1 Fees")
    units[2] = _unit("1", "unnumbered_annex", kind="schedule", at=text.rindex("SCHEDULE 1 Fees"))
    units.append(_unit("1", "regex", kind="hcontainer", at=text.index("SCHEDULE 1 Fees")))
    assert uk_layout_held(text, units, "section", "gb") is True


def test_the_first_attachment_after_the_provisions_still_ends_the_body(declare_gb: Declare) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text = "Made.\n1. First\nSCHEDULE 1 Fees\n2. Row\n"
    units = [_unit("1", at=text.index("1. First")), _unit("2", at=text.index("2. Row"))]
    assert uk_layout_held(text, units, "section", "gb") is False


def test_large_article_numbers_in_sequence_are_not_page_numbers(declare_gb: Declare) -> None:
    declare_gb()
    text = "Made today:\n" + "".join(
        f"{n}.-(1) The authority may make arrangements under paragraph {n} of the scheme.\n"
        "(2) The arrangements are public.\n"
        for n in (98, 99, 100, 101, 102)
    )
    scan = _scan(text)
    assert _sections(scan.anchors) == ["98", "99", "100", "101", "102"]
    assert scan.fires["drop_uk_page_numbers"] == 0


def test_a_lettered_article_is_not_a_page_number_candidate(declare_gb: Declare) -> None:
    declare_gb()
    a1, a2, a3, _ = _ARTICLES
    inserted = "2A.-(1) The register may be kept in electronic form.\n(2) It must be legible.\n"
    text = f"Made today:\n{a1}{a2}{inserted}{a3}Dated 1st April 2031.\n"
    assert _sections(_scan(text).anchors) == ["1", "2", "2A", "3"]


def test_a_jump_in_article_numbers_below_the_page_floor_is_kept(declare_gb: Declare) -> None:
    """Articles 3 to 29 lost to the scan: 30 beside 2 is a gap, not a page number."""
    declare_gb()
    a1, a2, _, _ = _ARTICLES
    far = "30.-(1) The register is closed.\n(2) That is final.\n31.-(1) It reopens.\n(2) So.\n"
    text = f"Made today:\n{a1}{a2}{far}Dated 1st April 2031.\n"
    assert _sections(_scan(text).anchors) == ["1", "2", "30", "31"]


# --- quoted regulations ------------------------------------------------------------------

_LEAD_IN = "For regulation {n} there shall be substituted the following regulation{dash}"
# A text layer prints the em dash as a soft hyphen.
_DASHES = ["\u2014", "\u00ad"]


@pytest.mark.parametrize("dash", _DASHES)
def test_a_regulation_quoted_in_a_substitution_is_not_a_unit(
    dash: str, declare_gb: Declare
) -> None:
    """The lead-in ends on a dash and the quoted number is not the next in sequence. The
    quotation is left open, as it is where the closing mark was lost."""
    declare_gb(amendments=_SUBSTITUTION)
    anchors = _scan(_quoted_regulation(dash)).anchors
    assert [a.number for a in anchors if a.kind == "section" and a.quoted_amendment] == ["9"]
    assert _sections(anchors) == ["3", "4", "5"]


def _quoted_regulation(dash: str, opening: str = "") -> str:
    return (
        "The Minister makes the following Regulations:\n"
        f"{opening}"
        "3.-(1) These Regulations may be cited as the Widget Regulations.\n"
        "(2) They come into force at once.\n"
        f"4. {_LEAD_IN.format(n=9, dash=dash)}\n"
        '"Application of the Food Safety Order (Northern Ireland) 1991\n'
        "9.-(1) The following provisions of the Order shall apply to these Regulations.\n"
        "(2) In paragraph (1) the Order means the Food Order 1991.\n"
        "5.-(1) For the Schedule there shall be substituted the Schedule set out below.\n"
        "(2) The Schedule takes effect at once.\n"
        "Dated 1st April 2031.\n"
    )


_COVER_NOTE = (
    "EXPLANATORY NOTE\n(This note is not part of the Regulations)\n"
    "These Regulations register widgets.\n\n"
)
_ONE_AND_TWO = (
    "1.-(1) These Regulations may be cited as the Widget Regulations.\n"
    "(2) They come into force at once.\n"
    "2.-(1) A widget is registered.\n(2) The register is public.\n"
)


def test_a_cover_note_does_not_switch_off_the_marking_of_a_quoted_article(
    declare_gb: Declare,
) -> None:
    declare_gb(amendments=_SUBSTITUTION, attachments=_INSTRUMENT_ATTACHMENTS)
    text = _COVER_NOTE + _quoted_regulation(_DASHES[0], _ONE_AND_TWO)
    anchors = _scan(text).anchors
    assert [a.number for a in anchors if a.kind == "section" and a.quoted_amendment] == ["9"]


def test_an_article_in_sequence_after_a_substitution_lead_in_is_a_unit(
    declare_gb: Declare,
) -> None:
    declare_gb(amendments=_SUBSTITUTION)
    text = (
        "The Minister makes the following Regulations:\n"
        f"3.-(1) {_LEAD_IN.format(n=4, dash=_DASHES[0])}\n"
        "(2) The substituted text is set out in the Schedule.\n"
        "4.-(1) The authority must keep a register of widgets in full.\n"
        "(2) The register is public.\n"
        "5.-(1) These Regulations come into force on the day after they are made.\n"
        "(2) That day is the commencement day.\n"
        "Dated 1st April 2031.\n"
    )
    assert _sections(_scan(text).anchors) == ["3", "4", "5"]


def _amended_by_schedule() -> str:
    body = "".join(
        f"{n}.\u2014(1) Provision {n} applies to every widget in full.\n(2) It applies at once.\n"
        for n in range(1, 6)
    )
    return (
        "The Department makes the following Regulations:\n"
        f"{body}"
        "Sealed with the Official Seal of the Department on 1st March 2031.\n"
        "A senior officer of the Department\n"
        "SCHEDULE\nRegulation 5\nAmendments to the Widget Regulations\n"
        "1.\u2014(1) In regulation 2, for the definition of widget there shall be substituted "
        "the following definition:\n"
        "Interpretation\n"
        "2.\u2014(1) In regulation 6, the word annual shall be omitted.\n"
        "(2) In regulation 8, the word annual shall be omitted.\n"
    )


def test_a_schedule_restarts_the_numbering_the_marking_compares_against(
    declare_gb: Declare,
) -> None:
    """Paragraph 2 of the schedule follows its own paragraph 1, not regulation 5 of the body."""
    declare_gb(
        amendments=_SUBSTITUTION,
        attachments=[{"caption": "SCHEDULE", "prefix": True, "always_opens": True}],
    )
    anchors = _scan(_amended_by_schedule()).anchors
    assert [a.number for a in anchors if a.kind == "section" and a.quoted_amendment] == []
    assert _sections(anchors)[-2:] == ["1", "2"]


def test_a_unit_after_one_the_scan_did_not_read_is_not_a_quotation(declare_gb: Declare) -> None:
    """Regulation 4 ends on a full stop and is read by no pass. Regulation 5 then jumps, and a
    lead-in stands above it, but a regulation and a quotation lie between the two."""
    declare_gb(amendments=_SUBSTITUTION)
    text = (
        "The Department makes the following Regulations:\n"
        "1.\u2014(1) These Regulations may be cited as the Widget Regulations 2031.\n"
        "(2) They come into operation at once.\n"
        "2.\u2014(1) In these Regulations the principal Regulations means those of 2020.\n"
        "(2) Nothing else.\n"
        "3. For regulation 5 of the principal Regulations there shall be substituted the "
        "following regulation:\n"
        "\u201c5. The fee is \u00a310.\u201d.\n"
        "4. Regulation 9 of the principal Regulations is revoked.\n"
        "5.\u2014(1) A widget registered before these Regulations remains registered.\n"
        "(2) That is final.\n"
        "6.\u2014(1) The Department may issue guidance.\n"
        "(2) Guidance is public.\n"
    )
    anchors = _scan(text).anchors
    assert [a.number for a in anchors if a.quoted_amendment] == []
    assert {"5", "6"} <= set(_sections(anchors))


def test_a_regulation_quoted_under_a_heading_line_is_still_a_quotation(
    declare_gb: Declare,
) -> None:
    """A heading line may stand between the lead-in and the quoted regulation."""
    declare_gb(amendments=_SUBSTITUTION)
    text = (
        "The Minister makes the following Regulations:\n"
        "1.\u2014(1) These Regulations may be cited as the Widget Regulations 2031.\n"
        "(2) They come into operation at once.\n"
        "2.\u2014(1) After regulation 2 there shall be substituted the following regulation:\n"
        "Fees\n"
        "7.\u2014(1) The fee for registering a widget is ten pounds.\n"
        "(2) It is payable once.\n"
        "3.\u2014(1) These Regulations come into force at once.\n"
        "(2) That is final.\n"
    )
    anchors = _scan(text).anchors
    assert [a.number for a in anchors if a.kind == "section" and a.quoted_amendment] == ["7"]
    assert _sections(anchors) == ["1", "2", "3"]


# --- attachments -------------------------------------------------------------------------

# A schedule table whose rows are numbered on from the body: read as a table caption unless
# the jurisdiction says its schedules always open an attachment.
_TABLE_SCHEDULE = (
    "The Minister makes the following Order:\n"
    "1.-(1) This Order may be cited as the Zone Order 2031 and takes effect at once.\n"
    "(2) The Zone is the area described in the table below.\n"
    "\n"
    "SCHEDULE 1 Article 2\n"
    "\n"
    "Area designated\n"
    "3. A, B Geodesic line\n"
    "4. B, C Mean high water line\n"
    "Where—\n"
    "“A” is 50° 05′ N and 05° 07′ W.\n"
)


def test_a_caption_declared_always_opening_survives_numbering_that_continues(
    declare_gb: Declare,
) -> None:
    declare_gb(attachments=[{"caption": "SCHEDULE", "prefix": True, "always_opens": True}])
    assert _schedules(_scan(_TABLE_SCHEDULE).anchors) == ["SCHEDULE 1 Article 2"]


def test_the_keyword_anchor_on_an_always_opening_caption_is_dropped(declare_gb: Declare) -> None:
    """The annex scan reads the line as an attachment; the keyword reading would add a
    second, empty container for the same caption. A mention earlier in the text is no twin."""
    declare_gb(attachments=[{"caption": "SCHEDULE", "prefix": True, "always_opens": True}])
    text = _TABLE_SCHEDULE.replace("the table below.", "Schedule 1.")
    anchors = _scan(text).anchors
    caption = text.index("\n\nSCHEDULE 1 Article 2")
    containers = [a for a in anchors if a.kind in ("hcontainer", "schedule")]
    assert [(a.kind, a.char_offset < caption) for a in containers] == [
        ("hcontainer", True),
        ("schedule", False),
    ]


def test_an_undeclared_caption_is_still_read_as_a_table_caption(declare_gb: Declare) -> None:
    """Only a country that declares it is exempt: otherwise the same text reads as a table
    caption."""
    declare_gb(attachments=[{"caption": "SCHEDULE", "prefix": True}])
    assert _schedules(_scan(_TABLE_SCHEDULE).anchors) == []


# --- the structurer's text: furniture is what repeats ------------------------------------

A4 = PageDimensions(dpi=87, width=720, height=1018)


def _block(kind: str, content: str, top: int, x: int = 40) -> OcrBlock:
    return OcrBlock(
        type=kind,
        content=content,
        top_left_x=x,
        top_left_y=top,
        bottom_right_x=x + 200,
        bottom_right_y=top + 18,
    )


def _page(number: int, lines: list[str], blocks: list[OcrBlock]) -> PageResult:
    layout = PageLayout(engine="mistral-ocr-4-0", blocks=blocks, dimensions=A4)
    return PageResult(
        page_number=number, text="\n".join(lines), method="text_extraction", layout=layout
    )


def _structure_text(pages: list[PageResult]) -> str:
    text, spans = combine_page_texts_with_spans(pages)
    regions = {
        p.page_number: classify_page(
            p.layout.blocks, p.layout.dimensions, page_number=p.page_number
        )
        for p in pages
        if p.layout is not None
    }
    return combine_text_for_structure(text, regions, spans, country="gb")


def _instrument_pages() -> list[PageResult]:
    """Three pages: a split head, a caption under header, notes under footer (one wrapped),
    bare page numbers, and body items that open like notes."""
    return [
        _page(
            1,
            [
                "1.-(1) The register must be kept.",
                "(a) for the purposes of the register;",
                "See the Schedule.",
                "841",
            ],
            [
                _block("text", "1.-(1) The register must be kept.", 200),
                _block("text", "(a) for the purposes of the register;", 230),
                _block("text", "See the Schedule.", 260),
                _block("footer", "841", 990),
            ],
        ),
        _page(
            2,
            [
                "842 Social Security No. 148",
                "SCHEDULE 1",
                "(a) the register is kept;",
                "The register records the matters.",
                "(a) S.R. 1987 No. 142",
                "Statutory Rules 1991",
            ],
            [
                _block("header", "842", 78, 40),
                _block("header", "Social Security", 83, 270),
                _block("header", "No. 148", 74, 520),
                _block("header", "SCHEDULE 1", 97),
                _block("text", "(a) the register is kept;", 200),
                _block("text", "The register records the matters.", 230),
                _block("footer", "(a) S.R. 1987 No. 142", 880),
                _block("footer", "Statutory Rules 1991", 990),
            ],
        ),
        _page(
            3,
            [
                "No. 148 Social Security 843",
                "Each entry is dated.",
                "(b) S.R. 1988 No. 77 as amended by",
                "S.R. 1990 No.",
                "12.",
                "",
                "(c) S.R. 1997 No. 31.",
                "Statutory Rules 1991",
            ],
            [
                _block("header", "No. 148", 80, 40),
                _block("header", "Social Security", 76, 270),
                _block("header", "843", 78, 520),
                _block("text", "Each entry is dated.", 200),
                _block("footer", "(b) S.R. 1988 No. 77 as amended by\nS.R. 1990 No.\n12.", 880),
                _block("footer", "(c) S.R. 1997 No. 31.", 930),
                _block("footer", "Statutory Rules 1991", 990),
            ],
        ),
    ]


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


@pytest.fixture
def recurring(declare_gb: Declare) -> None:
    base = json.loads((jurisdictions.JURISDICTIONS_DIR / "gb" / "config.json").read_text())
    declare_gb(structuring={**base["structuring"], "recurring_furniture": True})


@pytest.fixture
def captioned(declare_gb: Declare) -> None:
    """Recurring furniture, with the captions the shipped gb config declares for attachments."""
    base = json.loads((jurisdictions.JURISDICTIONS_DIR / "gb" / "config.json").read_text())
    declare_gb(
        structuring={**base["structuring"], "recurring_furniture": True},
        attachments=_INSTRUMENT_ATTACHMENTS,
    )


def test_a_running_head_split_across_blocks_leaves_the_structure_text(recurring: None) -> None:
    out = _structure_text(_instrument_pages())
    assert "Social Security" not in out
    assert "No. 148" not in out
    assert "The register records the matters." in out


def _split_head_blocks() -> list[OcrBlock]:
    return [
        _block("header", "843", 78, 40),
        _block("header", "Social Security", 83, 270),
        _block("header", "No. 148", 74, 520),
        _block("text", "Text of page two.", 200),
    ]


def test_a_head_printed_whole_on_one_page_and_split_on_another_leaves_both(
    recurring: None,
) -> None:
    """No part of the split head shares enough with the whole one; the joined row does."""
    first = ["842 Social Security No. 148", "The register records the matters."]
    second = ["843 Social Security No. 148", "Text of page two."]
    pages = [
        _page(1, first, [_block("header", first[0], 78), _block("text", first[1], 200)]),
        _page(2, second, _split_head_blocks()),
    ]
    assert _lines(_structure_text(pages)) == [first[1], second[1]]


def test_a_split_head_the_text_layer_prints_in_parts_leaves_in_parts(recurring: None) -> None:
    first = ["842 Social Security No. 148", "The register records the matters."]
    second = ["843", "Social Security", "No. 148", "Text of page two."]
    pages = [
        _page(1, first, [_block("header", first[0], 78), _block("text", first[1], 200)]),
        _page(2, second, _split_head_blocks()),
    ]
    assert _lines(_structure_text(pages)) == [first[1], second[3]]


def _head_page(number: int, top: int, *, middle: str, left: str, right: str) -> PageResult:
    """Three header blocks across the page, then one line of text."""
    body = f"Provisions of the Act {number}."
    return _page(
        number,
        [left, middle, right, body],
        [
            _block("header", left, top, 68),
            _block("header", middle, top, 280),
            _block("header", right, top, 503),
            _block("text", body, 200),
        ],
    )


def _schedule_head_pages() -> list[PageResult]:
    """The head of a schedule's page carries the schedule's number, so its text repeats with
    only the number changed; the caption line is a subset of it and is not the head."""
    pages = [
        _head_page(1, 69, left="No. 100", middle="Pensions", right="411"),
        _head_page(2, 69, left="No. 100", middle="Pensions SCHEDULE 1", right="412 Regulation 2"),
        _head_page(3, 67, left="No. 100", middle="Pensions SCHEDULE 2", right="413 Regulation 3"),
    ]
    pages[1].text = pages[1].text.replace("Pensions SCHEDULE 1", "Pensions\nSCHEDULE 1")
    pages[2].text = pages[2].text.replace("Pensions SCHEDULE 2", "Pensions\nSCHEDULE 2")
    return pages


def test_a_caption_inside_a_repeated_head_block_stays(captioned: None) -> None:
    lines = _lines(_structure_text(_schedule_head_pages()))
    assert [line for line in lines if line.startswith("SCHEDULE")] == ["SCHEDULE 1", "SCHEDULE 2"]
    assert "No. 100" not in lines


def test_without_the_flag_that_caption_leaves_with_the_head(declare_gb: Declare) -> None:
    """The guard belongs to `recurring_furniture`; a jurisdiction without it reads as before."""
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    lines = _lines(_structure_text(_schedule_head_pages()))
    assert [line for line in lines if line.startswith("SCHEDULE")] == []


def test_a_caption_printed_on_the_head_row_stays_and_the_head_still_leaves(
    captioned: None,
) -> None:
    first = ["842 Social Security No. 148", "The register records the matters."]
    second = ["843", "Social Security", "No. 148", "SCHEDULE 1", "Text of page two."]
    blocks = _split_head_blocks()
    blocks.insert(3, _block("header", "SCHEDULE 1", 80, 150))
    pages = [
        _page(1, first, [_block("header", first[0], 78), _block("text", first[1], 200)]),
        _page(2, second, blocks),
    ]
    assert _lines(_structure_text(pages)) == [first[1], "SCHEDULE 1", second[4]]


def test_a_caption_the_engine_filed_as_furniture_stays_where_it_is(recurring: None) -> None:
    lines = _lines(_structure_text(_instrument_pages()))
    assert lines[lines.index("SCHEDULE 1") - 1 :][:3] == [
        "See the Schedule.",
        "SCHEDULE 1",
        "(a) the register is kept;",
    ]


def test_footnotes_the_engine_filed_as_footer_move_to_the_foot(recurring: None) -> None:
    """Notes (a) and (c) differ in their numbers only, so they would read as one repeated
    block but for the marker they open on; the wrapped note is joined."""
    out = _structure_text(_instrument_pages())
    body, _, notes = out.partition("Each entry is dated.")
    assert notes.split("\n\n") == [
        "",
        "(a) S.R. 1987 No. 142",
        "(b) S.R. 1988 No. 77 as amended by S.R. 1990 No. 12.",
        "(c) S.R. 1997 No. 31.",
    ]
    assert "Statutory Rules" not in out


def test_a_wrapped_note_the_engine_typed_as_references_moves_whole(recurring: None) -> None:
    """Its second line opens a line of its own, which a schedule keyword would read."""
    first = "(a) 1970 c. 25 (N.I.) was added to section 19 by S.I. 1989 and"
    pages = [
        _page(
            1,
            ["The register is kept.", first, "Schedule 9 paragraph 79"],
            [
                _block("text", "The register is kept.", 200),
                _block("references", f"{first}\nSchedule 9 paragraph 79", 880),
            ],
        )
    ]
    assert _structure_text(pages) == f"The register is kept.\n\n{first} Schedule 9 paragraph 79"


def test_a_wrapped_body_line_that_shares_words_with_a_note_does_not_start_the_run(
    recurring: None,
) -> None:
    """`(3) of that Act; and` opens on a marker and every word of it is in the note below;
    the run is counted back from the foot, so the lines between stay."""
    note = "(a) 2009 c.23. See sections 116(5) and 147(1) of that Act."
    body = [
        "The Secretary of State has\u2014",
        "(a) published notice of the proposal in accordance with section 119(2) and",
        "(3) of that Act; and",
        "(b) consulted persons who are likely to be interested.",
        "The Secretary of State makes the following Order.",
    ]
    blocks = [_block("text", line, 200 + 30 * n) for n, line in enumerate(body)]
    pages = [_page(1, [*body, note], [*blocks, _block("footer", note, 880)])]
    assert _structure_text(pages) == "\n".join(body) + "\n\n" + note


def test_notes_that_are_not_at_the_foot_stay_in_place(recurring: None) -> None:
    """The text layer does not follow the layout: body lines come after the note's words,
    so counting back from the foot reaches more than the notes hold."""
    note = "(a) S.R. 1987 No. 142"
    lines = [note, "The register records the matters.", "More body text here."]
    blocks = [
        _block("footer", note, 880),
        *(_block("text", t, 200 + n) for n, t in enumerate(lines[1:])),
    ]
    pages = [_page(1, lines, blocks)]
    assert _structure_text(pages) == "\n".join(lines)


def test_a_foot_that_does_not_open_on_a_marker_is_not_the_notes(recurring: None) -> None:
    """Words enough to be the note, but no marker to begin it."""
    note = "(a) S.R. 1987 No. 142"
    lines = [note, "The register records all of the matters."]
    pages = [_page(1, lines, [_block("footer", note, 880), _block("text", lines[1], 200)])]
    assert _structure_text(pages) == "\n".join(lines)


def test_a_provision_at_the_foot_of_a_page_is_not_a_note(recurring: None) -> None:
    """The engine types it text. Only its place on the page and its `(7)` read as a note, so a
    run counted back from the foot would find it, agree with it and carry it to the end."""
    first = [
        "1.-(1) The register must be kept.",
        "(2) It is open.",
        "(7) Subject to paragraph (2), an election is irrevocable.",
    ]
    blocks = [_block("text", t, 200 + 30 * i) for i, t in enumerate(first[:2])]
    blocks.append(_block("text", first[2], 880))
    second = ["(8) An election does not affect a pension."]
    pages = [_page(1, first, blocks), _page(2, second, [_block("text", second[0], 200)])]
    assert _lines(_structure_text(pages)) == [*first, *second]


def test_a_body_item_that_opens_like_a_footnote_stays(recurring: None) -> None:
    """It shares a marker with the notes, not their words."""
    lines = _lines(_structure_text(_instrument_pages()))
    assert "(a) for the purposes of the register;" in lines[:3]
    assert "(a) the register is kept;" in lines[:6]


def test_a_long_header_block_that_repeats_is_not_a_running_head(recurring: None) -> None:
    """A body line that quotes a long title would match it, so only short heads go."""
    head = "Harbour Widgets Register Order Confirmation Act Annex Schedule Part"
    line = "Cited as the Harbour Widgets Register Order Confirmation Act Annex Schedule Part."
    pages = [
        _page(1, [head, line], [_block("header", head, 78), _block("text", line, 200)]),
        _page(
            2, [head, "More text."], [_block("header", head, 78), _block("text", "More text.", 200)]
        ),
    ]
    assert line in _structure_text(pages)


def test_a_caption_inside_a_longer_block_elsewhere_is_not_a_repeat(recurring: None) -> None:
    """Both blocks must cover each other: a short caption that a long head merely contains
    is not that head."""
    pages = [
        _page(1, ["Boundary lines", "Text."], [_block("header", "Boundary lines", 78)]),
        _page(
            2,
            ["Boundary lines of the Zone and the Schedule", "More."],
            [_block("header", "Boundary lines of the Zone and the Schedule", 78)],
        ),
    ]
    assert "Boundary lines" in _structure_text(pages).split("\n")


def test_numbered_captions_filed_as_furniture_are_not_a_repeated_head(recurring: None) -> None:
    """`SCHEDULE 1` and `SCHEDULE 2` on pages of their own differ in their numbers, as the
    page number of a head does, but the number is what makes each a caption."""
    pages = [
        _page(
            1, ["Opening text of the order."], [_block("text", "Opening text of the order.", 200)]
        ),
        _page(
            2,
            ["SCHEDULE 1", "Text of the first."],
            [_block("header", "SCHEDULE 1", 78), _block("text", "Text of the first.", 200)],
        ),
        _page(
            3,
            ["SCHEDULE 2", "Text of the second."],
            [_block("header", "SCHEDULE 2", 78), _block("text", "Text of the second.", 200)],
        ),
    ]
    lines = _lines(_structure_text(pages))
    assert [line for line in lines if line.startswith("SCHEDULE")] == ["SCHEDULE 1", "SCHEDULE 2"]


def test_a_head_whose_page_number_changes_is_still_a_repeated_head(recurring: None) -> None:
    pages = [
        _page(
            n,
            [f"{840 + n} Social Security No. 148", f"Text of page {n}."],
            [
                _block("header", f"{840 + n} Social Security No. 148", 78),
                _block("text", f"Text of page {n}.", 200),
            ],
        )
        for n in (2, 3)
    ]
    assert _lines(_structure_text(pages)) == ["Text of page 2.", "Text of page 3."]


def test_a_table_value_that_equals_the_page_number_stays(recurring: None) -> None:
    """A bare number is furniture at the head or foot of its page, not among its text."""
    lines = ["Fee payable each year", "1", "paid in advance", "1"]
    blocks = [_block("text", t, 200 + 30 * i) for i, t in enumerate(lines[:3])]
    pages = [_page(1, lines, [*blocks, _block("footer", "1", 990)])]
    assert _lines(_structure_text(pages)) == lines[:3]


@pytest.mark.parametrize(("number", "stays"), [("7", True), ("84", True), ("843", False)])
def test_a_bare_number_among_the_text_leaves_only_from_three_digits(
    number: str, stays: bool, recurring: None
) -> None:
    """A page number that long is no value in a table, so it leaves wherever the text layer
    sets it; a shorter one equal to the page's number is a value and stays."""
    lines = ["Fee payable each year", number, "paid in advance"]
    blocks = [_block("text", lines[0], 200), _block("text", lines[2], 230)]
    pages = [_page(1, lines, [*blocks, _block("footer", number, 990)])]
    assert _lines(_structure_text(pages)) == (lines if stays else [lines[0], lines[2]])


def test_a_short_page_number_at_the_head_of_its_page_leaves(recurring: None) -> None:
    lines = ["7", "Fee payable each year", "paid in advance"]
    blocks = [
        _block("header", "7", 40),
        _block("text", lines[1], 200),
        _block("text", lines[2], 230),
    ]
    assert _lines(_structure_text([_page(1, lines, blocks)])) == lines[1:]


def test_a_table_value_beside_the_page_number_at_the_foot_stays(recurring: None) -> None:
    """The page's layout holds one block of `5`: one line of it leaves, the outermost."""
    lines = ["Fee payable each year", "Renewal of registration", "5", "5"]
    blocks = [_block("text", t, 200 + 30 * i) for i, t in enumerate(lines[:3])]
    pages = [_page(5, lines, [*blocks, _block("footer", "5", 990)])]
    assert _lines(_structure_text(pages)) == lines[:3]


def test_a_wrapped_number_at_the_foot_stays_when_the_page_number_follows(recurring: None) -> None:
    lines = ["(2) The fee is payable in the manner set out in regulation", "5.", "5"]
    blocks = [_block("text", "(2) The fee is payable in the manner set out in regulation 5.", 800)]
    pages = [_page(5, lines, [*blocks, _block("footer", "5", 990)])]
    assert _lines(_structure_text(pages)) == lines[:2]


def test_captions_that_name_their_regulation_are_not_a_repeated_head(
    declare_gb: Declare,
) -> None:
    """`SCHEDULE 1 Regulation 2` and `SCHEDULE 2 Regulation 2` share three of four words, which
    is how a running head reads; each opens an attachment and stays."""
    base = json.loads((jurisdictions.JURISDICTIONS_DIR / "gb" / "config.json").read_text())
    declare_gb(
        structuring={**base["structuring"], "recurring_furniture": True},
        attachments=[{"caption": "SCHEDULE", "prefix": True, "always_opens": True}],
    )
    body = [
        "1.-(1) The fees in Schedule 1 and the forms in Schedule 2 apply.",
        "(2) That is final.",
    ]
    pages = [_page(1, body, [_block("text", t, 150 + 30 * i) for i, t in enumerate(body)])]
    for number, (caption, title) in enumerate(
        [("SCHEDULE 1 Regulation 2", "FEES"), ("SCHEDULE 2 Regulation 2", "FORMS")], start=2
    ):
        lines = [caption, title, "1. The fee for registration is ten pounds"]
        blocks = [_block("header", caption, 97), _block("text", title, 200)]
        pages.append(_page(number, lines, [*blocks, _block("text", lines[2], 230)]))
    lines = _lines(_structure_text(pages))
    assert "SCHEDULE 1 Regulation 2" in lines
    assert "SCHEDULE 2 Regulation 2" in lines


def test_a_page_number_drawn_with_dashes_leaves_and_does_not_join_the_last_note(
    recurring: None,
) -> None:
    note = "(a) S.R. 1987 No. 142"
    lines = ["Body line of the order.", note, "- 843 -"]
    blocks = [
        _block("text", lines[0], 200),
        _block("footer", note, 880),
        _block("footer", "843", 990),
    ]
    assert _structure_text([_page(1, lines, blocks)]) == f"{lines[0]}\n\n{note}"


def test_a_text_layer_that_puts_the_note_first_moves_nothing(recurring: None) -> None:
    """The last lines hold as many words as the note and open on a marker, but they are
    not the note: the two texts do not say the same."""
    note = "(a) S.R. 1987 No. 142"
    body = [
        "1.-(1) The register must be kept in full by the authority.",
        "(2) The 1987 register is open today.",
    ]
    blocks = [_block("footer", note, 880)] + [
        _block("text", t, 200 + 30 * i) for i, t in enumerate(body)
    ]
    with capture_logs() as logs:
        out = _structure_text([_page(1, [note, *body], blocks)])
    assert out == "\n".join([note, *body])
    assert [e["event"] for e in logs] == ["footnote_run_unplaced"]


def test_a_foot_that_repeats_one_word_of_the_note_is_not_the_note(recurring: None) -> None:
    """Two thirds of the foot's words are in the note, a quarter of the note's in the foot."""
    note = "(a) the register of entries kept under the Act"
    foot = "(2) the the the the the the register"
    body = "The authority keeps the register in full."
    blocks = [_block("text", body, 200), _block("footer", note, 880)]
    with capture_logs() as logs:
        out = _structure_text([_page(1, [body, foot], blocks)])
    assert out == f"{body}\n{foot}"
    assert [e["event"] for e in logs] == ["footnote_run_unplaced"]


def test_a_garbled_scan_of_a_note_is_still_the_note(recurring: None) -> None:
    """The text layer of an old scan misreads the note. It shares less with the layout's
    clean copy than a clean copy would, but more than other text does, so it moves."""
    note = "(a) S.I. 1991/2628 (N.I. 23)"
    garbled = "(a) S.l. i99112628 (N.l. 23)"
    first = ["Body line of the order.", "Another body line."]
    later = "Later page text of the order."
    pages = [
        _page(
            1,
            [*first, garbled],
            [
                *(_block("text", t, 200 + 30 * i) for i, t in enumerate(first)),
                _block("footer", note, 880),
            ],
        ),
        _page(2, [later], [_block("text", later, 200)]),
    ]
    assert _lines(_structure_text(pages)) == [*first, later, garbled]


def test_a_note_whose_marker_the_text_layer_lost_stays_where_it_is(recurring: None) -> None:
    """The line says what the note says, but nothing marks where a note begins."""
    note, garbled = "(a) S.R. 1987 No. 142", "a) S.R. 1987 No. 142"
    body = "The register is kept in full by the authority."
    blocks = [_block("text", body, 200), _block("footer", note, 880)]
    with capture_logs() as logs:
        out = _structure_text([_page(1, [body, garbled], blocks)])
    assert out == f"{body}\n{garbled}"
    assert [e["event"] for e in logs] == ["footnote_run_unplaced"]


def test_notes_that_read_alike_on_two_pages_are_notes_not_a_running_head(
    recurring: None,
) -> None:
    """Two notes that differ in one number are not one block printed twice."""
    pages = []
    for n, note in enumerate(("(a) S.R. 1987 No. 142", "(a) S.R. 1987 No. 143"), start=1):
        body = f"The register for part {n} is kept in full."
        blocks = [_block("text", body, 200), _block("footer", note, 880)]
        pages.append(_page(n, [body, note], blocks))
    assert _structure_text(pages) == (
        "The register for part 1 is kept in full.\nThe register for part 2 is kept in full."
        "\n\n(a) S.R. 1987 No. 142\n\n(a) S.R. 1987 No. 143"
    )


def test_a_list_item_the_engine_filed_as_furniture_does_not_start_a_run(recurring: None) -> None:
    """Items (c) and (d) follow a head the engine filed under header; the last of them
    is not that item's note."""
    head = "(b) the register is kept;"
    items = ["(c) the entry is signed;", "(d) the entry is dated."]
    blocks = [_block("header", head, 78)] + [
        _block("text", t, 200 + 30 * i) for i, t in enumerate(items)
    ]
    assert _structure_text([_page(1, [head, *items], blocks)]) == "\n".join([head, *items])


def test_without_page_spans_a_footnote_still_moves_to_the_foot(recurring: None) -> None:
    """Notes are read as a run page by page; with no pages to read them by they move
    line by line, as footnote blocks do wherever the flag is not set."""
    note = "(a) S.R. 1987 No. 142"
    lines = ["Body line of the order.", note, "Another body line."]
    blocks = [
        _block("text", lines[0], 200),
        _block("references", note, 880),
        _block("text", lines[2], 230),
    ]
    page = _page(1, lines, blocks)
    text, _ = combine_page_texts_with_spans([page])
    regions = {1: classify_page(blocks, A4, page_number=1)}
    out = combine_text_for_structure(text, regions, None, country="gb")
    assert _lines(out) == [lines[0], lines[2], note]


def test_a_country_without_a_config_is_refused_not_ignored() -> None:
    with pytest.raises(JurisdictionConfigError):
        combine_text_for_structure("text", {}, country="zz-nope")


def test_the_bare_page_numbers_leave(recurring: None) -> None:
    out = _structure_text(_instrument_pages())
    assert [line for line in out.split("\n") if line.strip().isdigit()] == []


def test_without_the_declaration_header_and_footer_blocks_are_dropped(
    declare_gb: Declare,
) -> None:
    """Without `recurring_furniture` every header and footer block goes: caption and notes."""
    declare_gb()
    out = _structure_text(_instrument_pages())
    assert "SCHEDULE 1" not in out
    assert "(a) S.R. 1987 No. 142" not in out


@pytest.mark.asyncio
async def test_the_pdf_lane_names_the_jurisdiction_to_the_structure_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reached(Exception):
        pass

    seen: dict[str, Any] = {}

    def capture(text: str, regions: Any, spans: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        raise Reached

    monkeypatch.setattr(pdf, "extract_metadata", AsyncMock(return_value={"title": "Order"}))
    monkeypatch.setattr(pdf, "resolve_descriptors", lambda *a, **k: SimpleNamespace(year="2031"))
    monkeypatch.setattr(pdf, "combine_text_for_structure", capture)
    with pytest.raises(Reached):
        async for _ in pdf._ingest_pages(
            [PageResult(page_number=1, text="1.-(1) Text.", method="text")],
            "gb",
            llm=MagicMock(),
            source_bytes=b"%PDF-1.7",
            source_name="source.pdf",
            fallback_stem="source",
            model=None,
            on_scan=None,
            langfuse=MagicMock(),
            root=None,
        ):
            pass
    assert seen == {"country": "gb"}


# --- what the article scan reads and refuses ---------------------------------------------


def _articles(numbers: Iterable[int]) -> str:
    return "".join(
        f"{n}.-(1) The authority must keep register {n} in full.\n(2) It is public.\n"
        for n in numbers
    )


@pytest.mark.parametrize(
    "form",
    [
        "{n}.(1) " + _BODY,
        "{n}. (1) " + _BODY,
        "{n} .—(1) " + _BODY,
        "{n}.— (1) " + _BODY,
        "{n}.—(1 ) " + _BODY,
    ],
)
def test_an_article_opens_on_its_paragraph_whatever_the_spacing(
    form: str, declare_gb: Declare
) -> None:
    declare_gb()
    text = "Made today:\n" + "".join(f"{form.format(n=n)}\n(2) It is public.\n" for n in (1, 2, 3))
    assert _sections(_scan(text).anchors) == ["1", "2", "3"]


@pytest.mark.parametrize("mark", ["–", "~", ":", "."])
def test_an_article_opens_after_any_mark_a_dash_prints_as(mark: str, declare_gb: Declare) -> None:
    declare_gb()
    text = "Made today:\n" + "".join(
        f"{n}.{mark}(1) {_BODY}\n(2) It is public.\n" for n in (1, 2, 3)
    )
    assert _sections(_scan(text).anchors) == ["1", "2", "3"]


@pytest.mark.parametrize("indent", [" ", "    ", "        "])
def test_an_indented_article_is_a_unit(indent: str, declare_gb: Declare) -> None:
    declare_gb()
    text = "Made today:\n" + "".join(
        f"{indent}{n}.-(1) {_BODY}\n(2) It is public.\n" for n in (1, 2, 3)
    )
    assert _sections(_scan(text).anchors) == ["1", "2", "3"]


@pytest.mark.parametrize(
    "head",
    [
        "**{n}** Interpretation of terms",
        "**{n}**. Interpretation of terms",
        "{n}~ Interpretation of terms",
    ],
)
def test_a_marked_number_heads_a_unit(head: str, declare_gb: Declare) -> None:
    declare_gb()
    text = "".join(
        f"{head.format(n=n)}\n(1) The authority must perform duty {n} in full.\n"
        "(2) That is final.\n"
        for n in (1, 2, 3)
    )
    assert _sections(_scan(text).anchors) == ["1", "2", "3"]


@pytest.mark.parametrize(
    "line",
    [
        "9(1) of the principal Regulations applies to every entry.",
        "The authority acts under 9.-(1) of the principal Regulations.",
    ],
    ids=["wrapped citation", "mid-line marker"],
)
def test_a_citation_that_looks_like_an_article_is_not_a_unit(
    line: str, declare_gb: Declare
) -> None:
    declare_gb()
    a1, a2, _, _ = _ARTICLES
    assert _sections(_scan(f"Made today:\n{a1}{a2}{line}\nDated 1st April 2031.\n").anchors) == [
        "1",
        "2",
    ]


# --- page numbers: where the rule starts and stops ----------------------------------------


@pytest.mark.parametrize(("number", "dropped"), [(99, False), (100, True)])
def test_a_number_below_the_floor_is_a_section_not_a_page_number(
    number: int, dropped: bool, declare_gb: Declare
) -> None:
    declare_gb()
    a1, a2, _, _ = _ARTICLES
    run = f"{number} Social Security\nThe authority keeps the register.\n"
    found = _sections(_scan(f"Made today:\n{a1}{a2}{run}Dated 1st April 2031.\n").anchors)
    assert (str(number) in found) is not dropped


@pytest.mark.parametrize(("number", "dropped"), [(112, False), (113, True)])
def test_a_number_over_eight_times_its_neighbours_is_a_page_number(
    number: int, dropped: bool, declare_gb: Declare
) -> None:
    declare_gb()
    run = f"{number} Social Security\nThe authority keeps the register.\n"
    text = "Made today:\n" + _articles(range(1, 14)) + run + _articles([14])
    assert (str(number) in _sections(_scan(text).anchors)) is not dropped


def test_a_run_of_three_page_numbers_falls_whole(declare_gb: Declare) -> None:
    declare_gb()
    a1, a2, _, _ = _ARTICLES
    heads = "".join(
        f"{n} Social Security\nThe authority keeps the register.\n" for n in (842, 843, 844)
    )
    scan = _scan(f"Made today:\n{a1}{a2}{heads}Dated 1st April 2031.\n")
    assert _sections(scan.anchors) == ["1", "2"]
    assert scan.fires["drop_uk_page_numbers"] == -3


def test_a_lone_large_number_has_no_neighbours_to_be_judged_by(declare_gb: Declare) -> None:
    declare_gb()
    text = "Made today:\n842 Social Security\nThe authority keeps the register in full.\n"
    assert _sections(_scan(text).anchors) == ["842"]


def test_a_run_at_the_start_is_judged_by_the_section_after_it(declare_gb: Declare) -> None:
    declare_gb()
    tail = "".join(
        f"{n} Heading number {n} of the scheme\nThe scheme applies to {n}.\n"
        for n in range(150, 161)
    )
    text = "Made today:\n1223 Education\nThe authority keeps the register.\n"
    assert "1223" not in _sections(_scan(text + _articles(range(5, 8)) + tail).anchors)


def test_a_page_number_a_contents_entry_cannot_explain_is_dropped_and_declared(
    declare_gb: Declare,
) -> None:
    """Prose follows the running head, so only the page-number rule can drop it."""
    declare_gb()
    a1, a2, a3, a4 = _ARTICLES
    run = "842 Social Security\nThe authority keeps the register.\n"
    scan = _scan(f"Made today:\n{a1}{a2}{run}{a3}{a4}Dated 1st April 2031.\n")
    assert _sections(scan.anchors) == ["1", "2", "3", "4"]
    spans = [
        (s.detail["number"], s.detail["reads_as"])
        for s in scan.ambiguity
        if s.emitted_by == "drop_uk_page_numbers"
    ]
    assert spans == [("842", "page_number")]


# --- quoted regulations: what the rule leaves alone ----------------------------------------


def test_the_provisions_of_a_quoted_regulation_are_quoted_with_it(declare_gb: Declare) -> None:
    declare_gb(amendments=_SUBSTITUTION)
    text = (
        "The Minister makes the following Regulations:\n"
        "3.-(1) These Regulations may be cited as the Widget Regulations.\n"
        "(2) They come into force at once.\n"
        f"4. {_LEAD_IN.format(n=9, dash=_DASHES[0])}\n"
        '"Application of the Food Safety Order (Northern Ireland) 1991\n'
        "9.-(1) The following provisions of the Order shall apply to these Regulations.\n"
        "(2) In paragraph (1) the Order means the Food Order 1991.\n"
        "5.-(1) For the Schedule there shall be substituted the Schedule set out below.\n"
        "(2) The Schedule takes effect at once.\n"
        "Dated 1st April 2031.\n"
    )
    subsections = [a.quoted_amendment for a in _scan(text).anchors if a.kind == "subsection"]
    assert subsections == [False, True, False]


def test_a_quotation_closing_after_its_full_stop_is_a_sentence_between(declare_gb: Declare) -> None:
    declare_gb(amendments=_SUBSTITUTION)
    text = (
        "The Department makes the following Regulations:\n"
        "1.—(1) These Regulations may be cited as the Widget Regulations 2031.\n"
        "(2) They come into operation at once.\n"
        "2.—(1) In these Regulations the principal Regulations means those of 2020.\n"
        "(2) Nothing else.\n"
        "3. For regulation 5 of the principal Regulations there shall be substituted the "
        "following regulation:\n"
        "“5. The fee is £10.”\n"
        "5.—(1) A widget registered before these Regulations remains registered.\n"
        "(2) That is final.\n"
        "6.—(1) The Department may issue guidance.\n"
        "(2) Guidance is public.\n"
    )
    assert [a.number for a in _scan(text).anchors if a.quoted_amendment] == []


_ARABIC = "مادة 1\nنص المادة الأولى.\n\nمادة 2\nيستبدل بنص المادة 9 النص الآتي{lead}\n"


def test_a_dash_ends_a_lead_in_only_for_the_uk() -> None:
    text = (
        _ARABIC.format(lead=" —") + "عنوان طويل جدا لنظام الغذاء والسلامة العامة والصحة في الدولة\n"
        "مادة 9\nنص.\n\nمادة 3\nنص.\n"
    )
    anchors = scan_anchors(text, cached_regex("xz", "act"), country="xz", doctype="act")
    assert [a.number for a in anchors if a.quoted_amendment] == []


def test_a_sentence_between_lead_in_and_unit_is_a_rule_for_the_uk_only() -> None:
    text = _ARABIC.format(lead=":") + "يقرر المجلس ما يلي.\nمادة 9\nنص.\n\nمادة 3\nنص.\n"
    anchors = scan_anchors(text, cached_regex("xz", "act"), country="xz", doctype="act")
    assert [a.number for a in anchors if a.quoted_amendment] == ["9"]


# --- attachments --------------------------------------------------------------------------


def test_the_keyword_anchor_stays_where_the_annex_scan_declines_the_caption(
    declare_gb: Declare,
) -> None:
    declare_gb(attachments=[{"caption": "SCHEDULE", "prefix": True, "always_opens": True}])
    long_caption = (
        "SCHEDULE 1 Article 2 and the areas, lines and points designated for the purposes of "
        "this Order"
    )
    text = _TABLE_SCHEDULE.replace("SCHEDULE 1 Article 2", long_caption)
    kinds = [a.kind for a in _scan(text).anchors if a.kind in ("hcontainer", "schedule")]
    assert kinds == ["hcontainer"]


_CITING = (
    "The Minister makes the following Order:\n"
    "1.-(1) This Order may be cited as the Widget Order.\n(2) It applies at once.\n"
    "2.-(1) A widget is registered under Regulation 6.\n(2) The register is public.\n"
)


def _keyword_sections(text: str) -> list[str | None]:
    return [
        a.number for a in _scan(text).anchors if a.source_pass == "regex" and a.kind == "section"
    ]


@pytest.mark.parametrize(
    "schedule",
    [
        "SCHEDULE 1\nRegulation 6\nForm of notice\n1. The form is as follows.\n",
        "Regulation 6\nSCHEDULE\n1. The form is as follows.\n",
        "SCHEDULE 1\nNo. 221\nRegulation 2\nRegulation 4\nFees\n1. The fee is payable.\n",
        "SCHEDULE 1\nForm of notice\n1. The form is as follows.\n411\nRegulation 2\n"
        "2. The form is signed.\n",
    ],
    ids=["below the caption", "above the caption", "two citations", "a page head inside"],
)
def test_a_schedule_citing_its_article_has_no_section_for_the_citation(
    schedule: str, declare_gb: Declare
) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    assert _keyword_sections(_CITING + schedule) == []


def test_a_keyword_and_number_in_the_body_is_a_unit_still(declare_gb: Declare) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text = (
        _CITING
        + "Regulation 3\nThe fee is payable in advance.\nSCHEDULE 1\n1. The form is as follows.\n"
    )
    assert _keyword_sections(text) == ["3"]


def test_a_document_of_keyword_headings_keeps_every_one(declare_gb: Declare) -> None:
    """Nothing was read as a UK provision, so there is no body to tell a citation from."""
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text = (
        "The Minister makes this Order:\nRegulation 1\nCitation\nThis Order may be cited.\n"
        "Regulation 2\nCommencement\nThis Order applies at once.\n"
        "SCHEDULE 1\nRegulation 2\nForm\nThe form is as follows.\n"
    )
    assert _keyword_sections(text) == ["1", "2", "2"]


def test_a_citation_is_dropped_and_declared(declare_gb: Declare) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    scan = _scan(_CITING + "SCHEDULE 1\nRegulation 6\n1. The form is as follows.\n")
    reads = [
        s.detail["reads_as"]
        for s in scan.ambiguity
        if s.emitted_by == "drop_schedule_cross_references"
    ]
    assert reads == ["cross_reference"]


def test_another_jurisdictions_citation_stays(declare_gb: Declare) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text = _CITING + "SCHEDULE 1\nRegulation 6\n1. The form is as follows.\n"
    cited = StructuralAnchor(
        kind="section",
        keyword="Regulation",
        number="6",
        char_offset=text.index("Regulation 6\n"),
        line=1,
        matched_text="Regulation 6",
        source_pass="regex",
    )
    anchors = [*_scan(text).anchors, cited]
    assert _drop_schedule_cross_references(text, anchors, "gb") == anchors[:-1]
    assert _drop_schedule_cross_references(text, anchors, "xz") == anchors


def test_a_schedule_line_that_says_more_than_the_citation_is_kept(declare_gb: Declare) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text = _CITING + "SCHEDULE 1\nRegulation 6 Citation\n1. The form is as follows.\n"
    assert _keyword_sections(text) == ["6"]


def test_a_schedule_paragraph_that_names_a_regulation_is_kept(declare_gb: Declare) -> None:
    declare_gb(attachments=_INSTRUMENT_ATTACHMENTS)
    text = (
        _CITING
        + "SCHEDULE 1\nForms\n1. Form of notice\nThe form is signed.\n"
        + "2. Regulation 4 does not apply\nIt takes effect at once.\n"
    )
    assert _sections(_scan(text).anchors) == ["1", "2", "1", "2"]


def test_only_a_caption_declared_always_opening_drops_its_twin(declare_gb: Declare) -> None:
    declare_gb(
        attachments=[
            {"caption": "SCHEDULE", "prefix": True, "always_opens": True},
            {"caption": "APPENDIX", "prefix": True},
        ]
    )
    text = _TABLE_SCHEDULE.replace("SCHEDULE 1 Article 2", "APPENDIX 1 Article 2")
    assert _schedules(_scan(text).anchors) == []


def test_the_outline_of_an_always_opening_schedule_is_scanned(declare_gb: Declare) -> None:
    declare_gb(
        attachments=[
            {
                "caption": "SCHEDULE",
                "prefix": True,
                "always_opens": True,
                "hierarchy": [
                    {
                        "local_term": "Item",
                        "akn_element": "paragraph",
                        "level": "basic",
                        "marker_form": "upper_letter_period",
                    }
                ],
            }
        ]
    )
    text = (
        _TABLE_SCHEDULE
        + "A. First point of the designation.\nB. Second point of the designation.\n"
    )
    scan = _scan(text)
    assert [a.number for a in scan.anchors if a.source_pass == "attachment_outline"] == ["A", "B"]


def test_a_dropped_caption_twin_is_declared(declare_gb: Declare) -> None:
    declare_gb(attachments=[{"caption": "SCHEDULE", "prefix": True, "always_opens": True}])
    scan = _scan(_TABLE_SCHEDULE)
    reads = [s.detail["reads_as"] for s in scan.ambiguity if s.emitted_by == "drop_caption_twins"]
    assert reads == ["caption_twin"]


def test_the_twin_is_dropped_beside_a_line_that_holds_only_a_tab(declare_gb: Declare) -> None:
    base = json.loads((jurisdictions.JURISDICTIONS_DIR / "gb" / "config.json").read_text())
    declare_gb(
        structuring={**base["structuring"], "marker_boundary": "line_anchored"},
        attachments=[{"caption": "SCHEDULE", "prefix": True, "always_opens": True}],
    )
    text = _TABLE_SCHEDULE.replace("\n\nSCHEDULE 1", "\n\t\nSCHEDULE 1")
    kinds = [a.kind for a in _scan(text).anchors if a.kind in ("hcontainer", "schedule")]
    assert kinds == ["schedule"]


# --- page furniture: what stays and what leaves --------------------------------------------


def test_a_furniture_block_with_no_words_does_not_stop_the_scan(recurring: None) -> None:
    pages = [
        _page(
            1,
            ["Text of page one."],
            [
                _block("text", "Text of page one.", 200),
                _block("footer", "***", 990),
                _block("header", "Social Security", 78),
            ],
        ),
        _page(
            2,
            ["Social Security", "Text of page two."],
            [_block("header", "Social Security", 78), _block("text", "Text of page two.", 200)],
        ),
    ]
    assert _lines(_structure_text(pages)) == ["Text of page one.", "Text of page two."]


def test_a_list_item_that_ends_its_page_stays_before_the_next_page(recurring: None) -> None:
    items = ["The authority must:", "(a) keep the register;", "(b) publish the register."]
    first = _page(1, items, [_block("text", t, 200 + 30 * i) for i, t in enumerate(items)])
    second = _page(2, ["Text of page two."], [_block("text", "Text of page two.", 200)])
    assert _lines(_structure_text([first, second])) == [*items, "Text of page two."]


def test_short_page_numbers_at_the_foot_of_every_page_leave(recurring: None) -> None:
    pages = [
        _page(
            n,
            [f"Text of page {n}.", str(n + 6)],
            [_block("text", f"Text of page {n}.", 200), _block("footer", str(n + 6), 990)],
        )
        for n in (1, 2)
    ]
    assert _lines(_structure_text(pages)) == ["Text of page 1.", "Text of page 2."]


@pytest.mark.parametrize("value", ["250", "7"])
def test_a_bare_number_that_is_not_the_pages_number_stays(value: str, recurring: None) -> None:
    lines = ["Fee payable each year", value, "paid in advance", "9"]
    blocks = [_block("text", t, 200 + 30 * i) for i, t in enumerate(lines[:3])]
    pages = [_page(1, lines, [*blocks, _block("footer", "9", 990)])]
    assert _lines(_structure_text(pages)) == lines[:3]


def test_a_number_that_is_another_pages_furniture_stays_here(recurring: None) -> None:
    second = ["Fee payable each year", "841", "paid in advance", "842"]
    pages = [
        _page(
            1,
            ["Text of page one.", "841"],
            [_block("text", "Text of page one.", 200), _block("footer", "841", 990)],
        ),
        _page(
            2,
            second,
            [
                _block("text", second[0], 200),
                _block("text", "841", 230),
                _block("text", second[2], 260),
                _block("footer", "842", 990),
            ],
        ),
    ]
    assert _lines(_structure_text(pages)) == ["Text of page one.", *second[:3]]


def test_a_header_and_a_footer_number_both_leave(recurring: None) -> None:
    lines = ["5", "Fee payable each year", "paid in advance", "5"]
    blocks = [
        _block("header", "5", 40),
        _block("text", lines[1], 200),
        _block("text", lines[2], 230),
        _block("footer", "5", 990),
    ]
    assert _lines(_structure_text([_page(5, lines, blocks)])) == lines[1:3]


def test_a_page_that_holds_only_its_number_loses_it(recurring: None) -> None:
    pages = [
        _page(1, ["Text of page one."], [_block("text", "Text of page one.", 200)]),
        _page(2, ["12"], [_block("footer", "12", 990)]),
        _page(3, ["Text of page three."], [_block("text", "Text of page three.", 200)]),
    ]
    assert _lines(_structure_text(pages)) == ["Text of page one.", "Text of page three."]


def test_a_reference_block_with_no_marker_moves_line_by_line(recurring: None) -> None:
    note = "continued from the note above and ending here"
    lines = ["Body line of the order.", note, "Another body line."]
    blocks = [
        _block("text", lines[0], 200),
        _block("references", note, 880),
        _block("text", lines[2], 230),
    ]
    assert _lines(_structure_text([_page(1, lines, blocks)])) == [lines[0], lines[2], note]


def test_a_list_item_filed_as_a_head_between_body_blocks_stays(recurring: None) -> None:
    lines = ["The authority must:", "(a) keep the register;", "(b) publish the register."]
    blocks = [
        _block("text", lines[0], 200),
        _block("header", lines[1], 400),
        _block("text", lines[2], 600),
    ]
    assert _structure_text([_page(1, lines, blocks)]) == "\n".join(lines)


def test_nothing_to_act_on_returns_the_text_unchanged(recurring: None) -> None:
    page = _page(
        1, ["Body line of the order.", ""], [_block("text", "Body line of the order.", 200)]
    )
    text, spans = combine_page_texts_with_spans([page])
    regions = {1: classify_page(page.layout.blocks, A4, page_number=1)}
    assert combine_text_for_structure(text, regions, spans, country="gb") == text


def test_a_placed_run_logs_nothing_and_an_unplaced_one_names_its_page(recurring: None) -> None:
    note = "(a) S.R. 1987 No. 142"
    body = "The register is kept in full by the authority."
    blocks = [_block("text", body, 200), _block("references", note, 880)]
    with capture_logs() as logs:
        _structure_text([_page(1, [body, note], blocks)])
    assert [e for e in logs if e["event"] == "footnote_run_unplaced"] == []
    with capture_logs() as logs:
        _structure_text([_page(4, [body, "a) S.R. 1987 No. 142"], blocks)])
    unplaced = [e for e in logs if e["event"] == "footnote_run_unplaced"]
    assert [(e["page"], e["notes"]) for e in unplaced] == [(4, 1)]


@pytest.mark.parametrize(("count", "kept"), [(8, False), (9, True)])
def test_a_head_of_more_than_eight_words_is_not_a_running_head(
    count: int, kept: bool, recurring: None
) -> None:
    head = " ".join(f"Word{i}" for i in range(count))
    pages = [
        _page(
            n,
            [head, f"Text of page {n}."],
            [_block("header", head, 78), _block("text", f"Text of page {n}.", 200)],
        )
        for n in (1, 2)
    ]
    assert (head in _lines(_structure_text(pages))) is kept


# --- the gate, scan and boundary together ----------------------------------------------------

_FLAT_BODY = (
    "The Minister makes the following Regulations: 1.-(1) These Regulations may be cited as the "
    "Widget Regulations. (2) They come into force at once. 2.-(1) A widget is registered under "
    "Regulation 3 of the principal Regulations. (2) The register is public. Dated 1st April 2031."
)
_LINED_BODY = (
    "The Minister makes the following Regulations:\n"
    "1.-(1) These Regulations may be cited as the Widget Regulations.\n"
    "(2) They come into force at once.\n"
    "2.-(1) A widget is registered under Regulation 3 of the principal Regulations.\n"
    "(2) The register is public.\n"
    "Dated 1st April 2031.\n"
)
_LINED_SCHEDULE = (
    "\nSCHEDULE 1\nTable of fees\n"
    "1 Fee for registration of a widget\nThe fee is payable in advance.\n"
    "2 Fee for renewal of a widget\nThe fee is payable each year.\n"
)


@pytest.fixture
def line_anchored(declare_gb: Declare) -> None:
    base = json.loads((jurisdictions.JURISDICTIONS_DIR / "gb" / "config.json").read_text())
    declare_gb(structuring={**base["structuring"], "marker_boundary": "line_anchored"})


@pytest.fixture
def instrument(declare_gb: Declare) -> None:
    """Line-anchored markers and the captions the shipped gb config declares for attachments."""
    base = json.loads((jurisdictions.JURISDICTIONS_DIR / "gb" / "config.json").read_text())
    declare_gb(
        structuring={**base["structuring"], "marker_boundary": "line_anchored"},
        attachments=_INSTRUMENT_ATTACHMENTS,
    )


async def _scaffold_gb(text: str) -> str:
    return await text_to_bluebell_scaffolded(
        text, client=_EmptyBodies(), country="gb", doctype="nisr", halt_policy="land"
    )


async def test_a_line_laid_body_holds_the_layout_and_a_flat_one_does_not(
    line_anchored: None,
) -> None:
    with capture_logs() as logs:
        assert await _scaffold_gb(_LINED_BODY)
    held = [e for e in logs if e["event"].startswith("anchor_boundary_policy")]
    assert [(e["event"], e["country"], e["kind"], e["stranded"]) for e in held] == [
        ("anchor_boundary_policy_held", "gb", "section", 1)
    ]
    with pytest.raises(AnchorCoverageError):
        await _scaffold_gb(_LINED_BODY.replace("\n", " "))


async def test_a_line_laid_schedule_does_not_hold_a_flat_body(line_anchored: None) -> None:
    """The schedule's paragraphs read 1 and 2, but they are not the body's."""
    with pytest.raises(AnchorCoverageError):
        await _scaffold_gb(_FLAT_BODY + _LINED_SCHEDULE)


async def test_a_flat_body_stays_void_where_the_schedule_caption_is_declared(
    instrument: None,
) -> None:
    with pytest.raises(AnchorCoverageError):
        await _scaffold_gb(_FLAT_BODY + _LINED_SCHEDULE)


async def test_a_cover_note_before_the_body_leaves_the_layout_held(instrument: None) -> None:
    with capture_logs() as logs:
        assert await _scaffold_gb(_COVER_NOTE + _LINED_BODY)
    events = [e["event"] for e in logs if e["event"].startswith("anchor_boundary_policy")]
    assert events == ["anchor_boundary_policy_held"]


_FLAT_VARIANTS = {
    "one paragraph a regulation": (
        "The Minister makes the following Regulations: 1. These Regulations may be cited as the "
        "Widget Regulations and come into force at once. 2. A widget is registered under "
        "Regulation 3 of the principal Regulations. 3. The fees in the Schedule apply. "
        "Dated 1st April 2031."
    ),
    "the dash lost": _FLAT_BODY.replace("1.-(1)", "1.(1)").replace("2.-(1)", "2. (1)"),
    "one opener": _FLAT_BODY.replace("2.-(1) A widget", "2. A widget").replace(
        " (2) The register is public.", ""
    ),
}


@pytest.mark.parametrize("body", list(_FLAT_VARIANTS))
async def test_a_flat_body_stays_void_whatever_its_openers_look_like(
    body: str, instrument: None
) -> None:
    with pytest.raises(AnchorCoverageError):
        await _scaffold_gb(_FLAT_VARIANTS[body] + _LINED_SCHEDULE)


_REGULATIONS = (
    "The Minister makes the following Regulations:\n"
    "1.-(1) These Regulations may be cited as the Widget Regulations.\n"
    "(2) They come into force at once.\n"
    "2.-(1) A widget is registered under Regulation 3 of the principal Regulations.\n"
    "(2) The register is public.\n"
    "3.-(1) The fees in Schedule 1 apply.\n(2) They are payable in advance.\n"
    "Dated 1st April 2031.\n"
)
_FEES = (
    "\nSCHEDULE 1 Regulation 3\nTable of fees\n"
    "1 Fee for registration of a widget\nThe fee is payable in advance.\n"
    "2 Fee for renewal of a widget\nThe fee is payable each year.\n"
)
_CONTENTS = {
    "regulations then the schedule": (
        "CONTENTS\n1. Citation and commencement\n2. Registration\n3. Fees\nSCHEDULE 1 Fees\n\n"
    ),
    "a dash before the title": (
        "CONTENTS\n1. Citation and commencement\n2. Registration\n3. Fees\n"
        "SCHEDULE 1 \u2014 Fees\n\n"
    ),
    "numbers without a dot": (
        "CONTENTS\n1 Citation and commencement\n2 Registration\n3 Fees\nSCHEDULE 1 Fees\n\n"
    ),
    "the schedule alone": "CONTENTS\nSCHEDULE 1 Fees\n\n",
    "a cover note first": _COVER_NOTE
    + "CONTENTS\n1. Citation and commencement\n2. Registration\n3. Fees\nSCHEDULE 1 Fees\n\n",
}


@pytest.mark.parametrize("front", list(_CONTENTS))
async def test_a_contents_list_ending_on_a_schedule_leaves_the_layout_held(
    front: str, instrument: None
) -> None:
    text = _CONTENTS[front] + _REGULATIONS + _FEES
    with capture_logs() as logs:
        assert await _scaffold_gb(text)
    events = [e["event"] for e in logs if e["event"].startswith("anchor_boundary_policy")]
    assert events == ["anchor_boundary_policy_held"]
    sections = _sections(_scan(text).anchors)
    assert sections == ["1", "2", "3", "1", "2"]


def test_a_contents_list_ending_on_a_schedule_does_not_switch_off_the_marking(
    declare_gb: Declare,
) -> None:
    declare_gb(amendments=_SUBSTITUTION, attachments=_INSTRUMENT_ATTACHMENTS)
    contents = "CONTENTS\n1. Citation\n2. Registration\n3. Fees\nSCHEDULE 1 Fees\n\n"
    text = contents + _quoted_regulation(_DASHES[0], _ONE_AND_TWO) + _FEES
    anchors = _scan(text).anchors
    assert [a.number for a in anchors if a.kind == "section" and a.quoted_amendment] == ["9"]


async def test_nothing_stranded_logs_nothing_about_the_boundary(line_anchored: None) -> None:
    text = _LINED_BODY.replace("under Regulation 3 of the principal Regulations", "in the register")
    with capture_logs() as logs:
        assert await _scaffold_gb(text)
    assert [e for e in logs if e["event"].startswith("anchor_boundary_policy")] == []


def test_a_caption_on_another_page_does_not_make_a_one_off_head_recur(captioned: None) -> None:
    """`Fees SCHEDULE 1` shares two of three words with the caption, which is not a head."""
    first = ["Fees SCHEDULE 1", "The register records the matters."]
    second = ["SCHEDULE 1", "Text of page two."]
    pages = [
        _page(1, first, [_block("header", first[0], 78), _block("text", first[1], 200)]),
        _page(2, second, [_block("header", second[0], 80), _block("text", second[1], 200)]),
    ]
    assert _lines(_structure_text(pages)) == [first[0], first[1], second[0], second[1]]
