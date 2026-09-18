"""Basic units printed marginal-note style: the note on its own line, the bare
number beneath it, the body after or beside it."""

from __future__ import annotations

from pathlib import Path

from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import (
    anchor_coverage,
    build_anchor_regex,
    scan_anchors_with_ambiguity,
)

# A vision transcription of the bundled synthetic gazette scan, verbatim.
TRANSCRIPTION = (Path(__file__).parent / "fixtures" / "marginal_note_transcription.txt").read_text(
    encoding="utf-8"
)

# The shape in miniature: a bold bare number under an italic note, then a
# same-line body, then a number line with a cross-reference in its body.
MARGINAL = """PART I — PRELIMINARY

*Short title*
**1.**
(1) This Act may be cited as the Harbour Widgets Act.
(2) It comes into operation on a day the Minister appoints.

*Interpretation*
**2.** In this Act "widget" means a device registered under this Act.

*Repeal*
3. Section 11 of the principal Act is repealed.
"""

# Two paragraphs with a page number between them, and a list inside a body.
PAGE_AND_LIST = """*Short title*
**1.**
(1) This Act may be cited as the Harbour Widgets Act.
The Minister may make regulations for the purposes of this Act.

7.

(2) Regulations may prescribe fees.
The register shall record the following matters
1. the name of the widget;
2. the date of registration.
"""


def _scan(text: str, country: str):
    return scan_anchors_with_ambiguity(
        text, build_anchor_regex(load_config(country), "act"), country=country, doctype="act"
    )


def _sections(text: str, country: str) -> list[tuple[str | None, str | None]]:
    return [(a.number, a.heading) for a in _scan(text, country).anchors if a.kind == "section"]


def test_bare_numbers_under_notes_anchor_as_sections() -> None:
    assert _sections(MARGINAL, "xa") == [
        ("1", "Short title"),
        ("2", "Interpretation"),
        ("3", "Repeal"),
    ]


def test_the_window_opens_on_the_note() -> None:
    """The note belongs to its section, not to the tail of the one before."""
    scan = _scan(MARGINAL, "xa")
    first = next(a for a in scan.anchors if a.kind == "section")
    assert MARGINAL[first.char_offset :].startswith("*Short title*")


def test_a_cross_reference_on_the_number_line_is_not_a_second_section() -> None:
    numbers = [a.number for a in _scan(MARGINAL, "xa").anchors if a.kind == "section"]
    assert "11" not in numbers
    assert scan_fires(MARGINAL)["drop_references_in_marginal_bodies"] == -1


def scan_fires(text: str) -> dict[str, int]:
    return _scan(text, "xa").fires


def test_the_transcription_yields_every_section() -> None:
    """Fails on a scan that reads only the parts: the defect the fixture records."""
    scan = _scan(TRANSCRIPTION, "xa")
    numbers = [a.number for a in scan.anchors if a.kind == "section"]
    assert numbers == [str(n) for n in range(1, 21)]
    coverage = anchor_coverage(TRANSCRIPTION, scan.anchors, load_config("xa"), "act", "section")
    assert (coverage.ratio, len(coverage.expected)) == (1.0, 20)


def test_a_config_without_marginal_notes_ignores_bare_numbers() -> None:
    """`xe` declares inline headings; the same lines stay body text there."""
    assert load_config("xe").display.heading_type != "marginal_note"
    assert [a for a in _scan(MARGINAL, "xe").anchors if a.kind in {"section", "article"}] == []


def test_a_page_number_between_paragraphs_is_not_a_section() -> None:
    assert _sections(PAGE_AND_LIST, "xa") == [("1", "Short title")]


def test_a_list_item_after_a_body_line_is_not_a_section() -> None:
    """The list's lead line reads like a note; the items open lowercase and
    restart at 1, and either alone keeps them out."""
    numbers = [a.number for a in _scan(PAGE_AND_LIST, "xa").anchors if a.kind == "section"]
    assert numbers == ["1"]


# A list whose lead line reads like a note and whose items open like sentences.
RESTARTING_LIST = """*Matters to be recorded*
**6.**
The following matters shall be recorded
1. The name of the widget.
2. The date of registration.

*Fees*
**7.** A fee is payable on registration.
"""


def test_a_list_restarting_inside_a_section_is_not_sections() -> None:
    """Only the climbing-number guard tells these items from provisions."""
    assert _sections(RESTARTING_LIST, "xa") == [("6", "Matters to be recorded"), ("7", "Fees")]


# A schedule whose paragraphs carry on the body's numbering under a caption, and
# an outline list inside a body whose items climb past the last section.
WITH_SCHEDULE = """*Register*
**3.**
The register has these parts
A. Names
4. The name of every widget.

SCHEDULE 1

Enactments repealed
5. The Widgets Act.
6. The Dials Act.
"""


def test_outline_and_schedule_lists_are_not_sections() -> None:
    """Numbers climb past 3 in both lists; the outline caption above one and the
    schedule marker before the other are what keep them out."""
    assert _sections(WITH_SCHEDULE, "xa") == [("3", "Register")]
    coverage = anchor_coverage(
        WITH_SCHEDULE, _scan(WITH_SCHEDULE, "xa").anchors, load_config("xa"), "act", "section"
    )
    assert sorted(coverage.expected) == ["3"]


# A page number after a wrapped line that opens with a capitalised noun, and an
# inserted section quoted at its body: neither note opens a block.
STRAYS = """*Interpretation*
**2.**
(1) In this Act a reference to the Minister is a reference to the
Minister responsible for widgets, and includes any
9.
Department of the Minister.

*Amendment*
**3.**
(1) The principal Act is amended by inserting after section 4 the following section—
*Registers*
**4A.** “(1) The Minister shall keep a register.”

*Application*
**5.**
(1) This Act binds the State.
"""


def test_a_note_must_open_a_block() -> None:
    """A stray accepted with a high number would eject every later section."""
    assert _sections(STRAYS, "xa") == [
        ("2", "Interpretation"),
        ("3", "Amendment"),
        ("5", "Application"),
    ]


# A contents list that names the schedule, ahead of the body it lists.
WITH_CONTENTS = """ARRANGEMENT OF SECTIONS

PART I — PRELIMINARY
1. Short title
2. Interpretation

SCHEDULE 1

PART I — PRELIMINARY

*Short title*
**1.**
(1) This Act may be cited as the Widgets Act.

*Interpretation*
**2.**
(1) In this Act "widget" means a device.

SCHEDULE 1
Enactments repealed
1. The Dials Act.
"""


def test_a_contents_list_naming_the_schedule_does_not_floor_the_body() -> None:
    scan = _scan(WITH_CONTENTS, "xa")
    assert [(a.number, a.heading) for a in scan.anchors if a.kind == "section"] == [
        ("1", "Short title"),
        ("2", "Interpretation"),
    ]
    coverage = anchor_coverage(WITH_CONTENTS, scan.anchors, load_config("xa"), "act", "section")
    assert (coverage.ratio, sorted(coverage.expected)) == (1.0, ["1", "2"])


def test_a_contents_entry_beside_its_number_is_not_a_body() -> None:
    """The first contents entry sits under a note-like header; its tail is a
    title, not a sentence."""
    numbers = [a.number for a in _scan(WITH_CONTENTS, "xa").anchors if a.kind == "section"]
    assert numbers.count("1") == 1


def test_crlf_line_endings_anchor_the_same() -> None:
    assert _sections(MARGINAL.replace("\n", "\r\n"), "xa") == _sections(MARGINAL, "xa")


# A number line whose tail the keyword-less UK section scan also reads.
UK_TAIL = """*Short title*
**1.**
(1) This Act may be cited as the Widgets Act.

*Interpretation*
2. In this Act—
(a) "widget" means a device.

*Application*
**3.**
(1) This Act binds the Crown.
"""


def test_the_uk_scan_does_not_split_a_noted_section() -> None:
    scan = _scan(UK_TAIL, "gb")
    sections = [(a.number, a.heading, a.source_pass) for a in scan.anchors if a.kind == "section"]
    assert sections == [
        ("1", "Short title", "marginal_note_units"),
        ("2", "Interpretation", "marginal_note_units"),
        ("3", "Application", "marginal_note_units"),
    ]


# A contents list under a plain header, then the body.
CONTENTS_FIRST = """Contents
1. Title
2. Commencement

PART I — PRELIMINARY

*Title*
**1.**
(1) This Act is the Widgets Act.

*Commencement*
**2.**
(1) This Act comes into force on assent.
"""


def test_a_contents_entry_under_a_plain_header_is_not_a_section() -> None:
    """The header reads as a note; only the entry's title-shaped tail tells."""
    assert _sections(CONTENTS_FIRST, "xa") == [("1", "Title"), ("2", "Commencement")]


# A quoted schedule and a quoted inserted section, each at column start.
QUOTED = """*Fees*
**2.**
(1) The Minister may insert the following Schedule—
“
SCHEDULE 2
Fees
1. A fee of ten pounds is payable.
”
(2) The principal Act is amended by inserting after section 3—
“

*Registers*
**4A.**
(1) The Minister shall keep a register.
”

*Application*
**3.**
(1) This Act binds the State.

SCHEDULE 1
Enactments repealed
1. The Dials Act.
"""


def test_the_denominator_masks_quotes_as_the_scan_does() -> None:
    scan = _scan(QUOTED, "xa")
    assert [a.number for a in scan.anchors if a.kind == "section"] == ["2", "3"]
    coverage = anchor_coverage(QUOTED, scan.anchors, load_config("xa"), "act", "section")
    assert (coverage.ratio, sorted(coverage.expected)) == (1.0, ["2", "3"])
