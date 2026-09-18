"""Config-declared grammar the scan reads: suffixes, citation runs, closing boundary,
prefix captions. Synthetic jurisdiction throughout: the shapes are the subject."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from codify import jurisdictions
from codify.akn import AKN_NS
from codify.pipeline.enrich import anchors as anchors_mod
from codify.pipeline.enrich import scaffold as scaffold_mod
from codify.pipeline.enrich import structure as structure_mod
from codify.pipeline.enrich.anchors import build_anchor_regex, scan_anchors_with_ambiguity
from codify.pipeline.enrich.closing import bound_body_at_closing, closing_offset

COUNTRY = "xa"
SUFFIXES = {"zib": "bis", "zter": "ter", "zq": "quater"}
CLOSING = "Sealed by the Harbour Clerk"
SUCCESSORS = ["to", "and", "Section", "กขค"]


@pytest.fixture
def declared(monkeypatch: pytest.MonkeyPatch) -> jurisdictions.JurisdictionConfig:
    """The synthetic jurisdiction with every grammar under test declared."""
    original = jurisdictions.load_config
    base = original(COUNTRY)
    structuring = base.structuring.model_copy(
        update={
            "insertion_suffixes": SUFFIXES,
            "citation_successors": SUCCESSORS,
            "marker_boundary": "line_anchored",
        }
    )
    config = base.model_copy(
        update={
            "structuring": structuring,
            "closing_phrases": [CLOSING],
            "attachments": [
                jurisdictions.AttachmentCaption(caption="NOTE", normative=False),
                jurisdictions.AttachmentCaption(caption="TABLE OF", prefix=True),
                jurisdictions.AttachmentCaption(
                    caption="ANNEX",
                    hierarchy=[
                        jurisdictions.HierarchyEntry(
                            local_term="Rule",
                            akn_element="paragraph",
                            level="basic",
                            numbering="arabic_continuous",
                        )
                    ],
                ),
            ],
        }
    )

    def load(country: str) -> Any:
        return config if country == COUNTRY else original(country)

    for mod in (jurisdictions, anchors_mod, structure_mod):
        monkeypatch.setattr(mod, "load_config", load)
    monkeypatch.setattr(jurisdictions, "insertion_suffix_folds", lambda: dict(SUFFIXES))
    for cached in (
        anchors_mod.cached_regex,
        anchors_mod._citation_successors_for,
        anchors_mod._successor_re,
        anchors_mod._prefix_captions,
        anchors_mod._annex_caption_re,
        anchors_mod._basic_unit_line_re,
    ):
        cached.cache_clear()
    yield config
    for cached in (
        anchors_mod.cached_regex,
        anchors_mod._citation_successors_for,
        anchors_mod._successor_re,
        anchors_mod._prefix_captions,
        anchors_mod._annex_caption_re,
        anchors_mod._basic_unit_line_re,
    ):
        cached.cache_clear()


def _scan(text: str) -> anchors_mod.AnchorScan:
    regex = build_anchor_regex(jurisdictions.load_config(COUNTRY), "act")
    return scan_anchors_with_ambiguity(text, regex, country=COUNTRY, doctype="act")


def _sections(scan: anchors_mod.AnchorScan) -> list[tuple[str, str]]:
    return [(a.number or "", a.akn_eid) for a in scan.anchors if a.kind == "section"]


BODY = """Section 5
Five applies.

Section 5 zib
Five bis applies.

Section 5
zter*
Five ter, wrapped.

Section 6
Six applies.
"""


def test_the_grammar_reads_windows_line_endings(declared: Any) -> None:
    """Sources arrive with CRLF; a wrapped suffix, a caption line and the closing
    phrase must read the same as with LF."""
    lf = f"Section 5\nzter*\nFive ter.\n\nSection 6\nSix.\n\n{CLOSING}\n\nNOTE\nWhy.\n"
    text = lf.replace("\n", "\r\n")
    scan = _scan(text)
    assert [n for n, _ in _sections(scan)] == ["5 zter", "6"]
    assert [a.heading for a in scan.anchors if a.kind == "schedule"] == ["NOTE"]
    bound = bound_body_at_closing(text, scan.anchors, [CLOSING], country=COUNTRY)
    assert bound.cut_at == text.index(CLOSING)


def test_a_declared_suffix_is_one_number_and_not_a_twin(declared: Any) -> None:
    scan = _scan(BODY)
    assert _sections(scan) == [
        ("5", "sec_5"),
        ("5 zib", "sec_5bis"),
        ("5 zter", "sec_5ter"),
        ("6", "sec_6"),
    ]
    assert not [sp for sp in scan.ambiguity if sp.kind == "duplicate_number"]


def test_a_suffix_word_needs_its_own_end(declared: Any) -> None:
    """`zib` opening a longer word is prose after the number, not a suffix."""
    scan = _scan("Section 5\nOne.\n\nSection 7 zibber applies.\nTwo.\n")
    assert [n for n, _ in _sections(scan)] == ["5", "7"]


def test_a_suffixed_number_sequences_as_its_base() -> None:
    assert anchors_mod._roman_or_digit("5 zib") is None
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(jurisdictions, "insertion_suffix_folds", lambda: dict(SUFFIXES))
        assert anchors_mod._roman_or_digit("5 zib") == 5
        assert anchors_mod._normalise_number("12 zq") == "12quater"


def test_the_eid_fold_agrees_with_the_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    from codify.pipeline.enrich.bluebell import _ascii_fold_eid

    # A non-Latin suffix, as a source script would write it; an ASCII eId is left alone.
    monkeypatch.setattr(jurisdictions, "insertion_suffix_folds", lambda: {"ζib": "bis"})
    assert _ascii_fold_eid("sec_٥ζib") == "sec_5bis"
    assert _ascii_fold_eid("sec_5ζib__p_1") == "sec_5bis__p_1"


def test_a_marker_followed_by_a_successor_is_a_citation_run(declared: Any) -> None:
    text = "Section 5\nFive.\n\nSection 6 to Section 9 apply here.\nMore.\n\nSection 7\nSeven.\n"
    scan = _scan(text)
    assert [n for n, _ in _sections(scan)] == ["5", "7"]
    reasons = [sp.detail.get("reason") for sp in scan.ambiguity if sp.kind == "unmatched_marker"]
    assert "read_as_citation_run" in reasons


def test_decorations_sit_between_the_number_and_the_successor(declared: Any) -> None:
    scan = _scan("Section 5\nFive.\n\nSection 6[2]* and Section 8 apply.\n\nSection 7\nSeven.\n")
    assert [n for n, _ in _sections(scan)] == ["5", "7"]


def test_no_successors_declared_keeps_the_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    anchors_mod._citation_successors_for.cache_clear()
    anchors_mod._successor_re.cache_clear()
    scan = _scan("Section 5\nFive.\n\nSection 6 to Section 9 apply here.\n\nSection 7\nSeven.\n")
    assert "6" in [n for n, _ in _sections(scan)]


TAIL = """Section 1
One.

Section 2
Two.

Sealed by the Harbour Clerk
The Warden

Appended instrument
Section 1
Appended one.

NOTE
Why it was made.

Section 2
Appended two.
"""


def test_the_closing_phrase_bounds_the_body(declared: Any) -> None:
    scan = _scan(TAIL)
    cut = next(sp for sp in scan.ambiguity if sp.kind == "tail_excluded")
    assert cut.detail["anchors"] == 1, "the appended section before the caption was not counted"
    assert not [sp for sp in scan.ambiguity if sp.kind == "duplicate_number"], (
        "the appended section collided with the body's before the cut"
    )
    bound = bound_body_at_closing(TAIL, scan.anchors, [CLOSING])
    assert bound.cut_at == TAIL.index(CLOSING)
    body = [a for a in bound.anchors if a.kind == "section" and a.parent_eid is None]
    assert [a.number for a in body] == ["1", "2"], "a marker after the signature stayed in the body"
    assert bound.conclusions is not None and bound.conclusions.startswith(CLOSING)
    assert "Appended one." in bound.conclusions, "the cut span was dropped instead of kept"
    # The attachment and its own section survive the cut with offsets that still index the text.
    note = next(a for a in bound.anchors if a.kind == "schedule")
    assert bound.text[note.char_offset :].lstrip().startswith("NOTE")
    inside = [a for a in bound.anchors if a.parent_eid == note.akn_eid]
    assert [a.number for a in inside] == ["2"]


def test_no_closing_phrase_leaves_the_text_alone(declared: Any) -> None:
    scan = _scan(TAIL)
    bound = bound_body_at_closing(TAIL, scan.anchors, [])
    assert bound.text == TAIL and bound.conclusions is None and bound.excluded_anchors == 0
    assert closing_offset(TAIL, ["Nothing here"], after=0) is None


def test_a_caption_before_the_closing_phrase_is_not_an_attachment(declared: Any) -> None:
    # Nothing numbered follows the caption, the shape the caption rule reads as an annex.
    text = f"Section 1\nOne.\n\nSection 2\nTwo.\n\nNOTE\nA body note.\n\n{CLOSING}\nThe Warden\n"
    scan = _scan(text)
    assert not [a for a in scan.anchors if a.kind == "schedule"]


def test_a_prefix_caption_takes_its_line_as_the_heading(declared: Any) -> None:
    text = f"Section 1\nOne.\n\n{CLOSING}\n\nTABLE OF FEES AND DUTIES\n1. Two coins.\n"
    scan = _scan(text)
    schedules = [a for a in scan.anchors if a.kind == "schedule"]
    assert [a.heading for a in schedules] == ["TABLE OF FEES AND DUTIES"]


def test_a_long_line_opening_with_the_caption_word_is_prose(declared: Any) -> None:
    long_line = "TABLE OF " + "x" * 90
    text = f"Section 1\nOne.\n\n{CLOSING}\n\n{long_line}\n"
    assert not [a for a in _scan(text).anchors if a.kind == "schedule"]


def test_the_scaffold_places_conclusions_before_the_first_attachment(declared: Any) -> None:
    scan = _scan(TAIL)
    bound = bound_body_at_closing(TAIL, scan.anchors, [CLOSING])
    scaffold, _ = scaffold_mod.scaffold_from_anchors(
        bound.anchors, preface="Title", country=COUNTRY, conclusions=bound.conclusions
    )
    lines = scaffold.splitlines()
    assert lines.index("CONCLUSIONS") > lines.index("  SECTION 2")
    first_schedule = next(i for i, line in enumerate(lines) if line.startswith("SCHEDULE"))
    assert lines.index("CONCLUSIONS") < first_schedule
    assert f"  {CLOSING}" in lines


def test_a_wrapped_marker_still_bounds_a_reversed_quote(declared: Any) -> None:
    """The number may wrap onto the next line; the quote walker's boundary reads it."""
    boundary = anchors_mod._basic_unit_line_re(COUNTRY)
    assert boundary is not None
    assert boundary.search("Section\n5\nText")


class _EmptyFillClient:
    """Returns no bodies, so the structurer's verbatim fallback fills every anchor."""

    async def chat_schema(self, *a: Any, **k: Any) -> Any:
        return scaffold_mod.BodyFillResponse(bodies=[])


@pytest.mark.asyncio
async def test_the_structured_document_keeps_the_tail_out_of_the_body(declared: Any) -> None:
    """End to end: the signature lands in conclusions, the appended instrument's text
    is kept there, and the note is an attachment holding its own section."""
    from codify.pipeline.enrich.bluebell import parse_to_akn

    bluebell = await structure_mod.text_to_bluebell_scaffolded(
        "AN ACT\n\n" + TAIL, client=_EmptyFillClient(), country=COUNTRY, doctype="act"
    )
    akn = parse_to_akn(bluebell, country=COUNTRY, doctype="act", date="2020", number="1")
    body = akn[akn.index("<body") : akn.index("</body>")]
    assert body.count("<section ") == 2, body
    assert "One." in body and "Two." in body, "an empty section was never refilled"
    assert "Appended one." not in body
    conclusions = akn[akn.index("<conclusions") : akn.index("</conclusions>")]
    assert CLOSING in conclusions and "Appended one." in conclusions
    attachments = akn[akn.index("<attachments") :]
    assert "Why it was made." in attachments and "Appended two." in attachments


def test_a_slashed_insertion_is_its_own_number(declared: Any) -> None:
    scan = _scan("Section 7\nSeven.\n\nSection 7/1\nSeven one.\n\nSection 8\nEight.\n")
    assert _sections(scan) == [("7", "sec_7"), ("7/1", "sec_7-1"), ("8", "sec_8")]
    assert not [sp for sp in scan.ambiguity if sp.kind == "duplicate_number"]


def test_a_dropped_closer_does_not_mask_the_provisions_that_follow(declared: Any) -> None:
    """The definitions block opens a quote nothing closes; the next quotation is
    pages later. The span ends at the blank line, since a heading lies between."""
    text = (
        "Section 1\nIn this Act, \u201cterm means a thing.\n\n"
        "Section 2\nTwo.\n\nSection 3\nA \u201cquoted\u201d word.\n"
    )
    scan = _scan(text)
    assert [n for n, _ in _sections(scan)] == ["1", "2", "3"]


def test_a_precursor_in_an_unspaced_script_follows_a_letter_directly() -> None:
    """Scripts without word spacing glue the precursor to the word before it."""
    from codify.pipeline.enrich.anchors import _precursor_re

    glued = _precursor_re(("ตาม",))
    assert glued.search("ให้ธนาคารตาม")
    assert glued.search("as per") is None
    spaced = _precursor_re(("per",))
    assert spaced.search("as per") and spaced.search("paper") is None


def test_a_quoted_closing_phrase_is_not_the_boundary(declared: Any) -> None:
    """An amendment quotes an instrument that ends on the phrase; the real one follows."""
    text = (
        "Section 1\nOne.\n\nSection 2\nThe old Act read: \u201cSection 9\nNine.\n"
        f"{CLOSING}\nThe old Warden\u201d and is repealed.\n\n"
        f"Section 3\nThree.\n\n{CLOSING}\nThe Warden\n"
    )
    scan = _scan(text)
    assert [n for n, _ in _sections(scan)] == ["1", "2", "3"]
    bound = bound_body_at_closing(text, scan.anchors, [CLOSING], country=COUNTRY)
    assert bound.cut_at == text.rindex(CLOSING)


def test_a_successor_is_a_whole_word(declared: Any) -> None:
    scan = _scan("Section 5\nFive.\n\nSection 6 tomorrow applies.\n\nSection 7\nSeven.\n")
    assert [n for n, _ in _sections(scan)] == ["5", "6", "7"]


def test_the_coverage_denominator_stops_at_the_closing_phrase(declared: Any) -> None:
    from codify.pipeline.enrich.anchors import _marker_numbers

    text = (
        f"Section 1\nOne.\n\nSection 2\nTwo.\n\n{CLOSING}\n\nSection 5\nFive.\n\nSection 6\nSix.\n"
    )
    expected = _marker_numbers(text, jurisdictions.load_config(COUNTRY), "act", "section")
    assert expected == {"1", "2"}, expected


def test_a_successor_in_an_unspaced_script_needs_no_word_end(declared: Any) -> None:
    scan = _scan("Section 5\nFive.\n\nSection 6 กขคงจ\nSix.\n\nSection 7\nSeven.\n")
    assert [n for n, _ in _sections(scan)] == ["5", "7"]


def test_a_stray_opener_does_not_hide_the_closing_phrase(declared: Any) -> None:
    # No blank line and no closer: the stray span reaches the signature.
    text = f"Section 1\nOne.\n\nSection 2\nTwo \u201cunclosed.\n{CLOSING}\nThe Warden\n"
    scan = _scan(text)
    bound = bound_body_at_closing(text, scan.anchors, [CLOSING], country=COUNTRY)
    assert bound.cut_at == text.index(CLOSING)


def test_the_verbatim_fill_consumes_a_wrapped_marker(declared: Any) -> None:
    from codify.pipeline.enrich.verbatim import fill_bodies_verbatim

    text = "Section 5\nzter*\nFive ter, wrapped.\n\nSection 6\nSix.\n"
    scan = _scan(text)
    bodies = {b.eid: b for b in fill_bodies_verbatim(text, scan.anchors).bodies}
    assert bodies["sec_5ter"].lines == ["Five ter, wrapped."], bodies["sec_5ter"]
    assert bodies["sec_5ter"].heading is None


def test_a_spaced_slash_still_keys_as_one_number(declared: Any) -> None:
    scan = _scan("Section 7\nSeven.\n\nSection 7 / 1\nSeven one.\n\nSection 8\nEight.\n")
    assert _sections(scan) == [("7", "sec_7"), ("7/1", "sec_7-1"), ("8", "sec_8")]


def test_the_verbatim_lane_keeps_the_tail_out_of_the_body(declared: Any) -> None:
    from codify.pipeline.enrich.verbatim import text_to_bluebell_verbatim

    bluebell, _, _ = text_to_bluebell_verbatim("AN ACT\n\n" + TAIL, country=COUNTRY)
    body = bluebell[bluebell.index("BODY") : bluebell.index("CONCLUSIONS")]
    assert "Appended one." not in body and "Two." in body
    assert "Appended one." in bluebell[bluebell.index("CONCLUSIONS") :]


def test_a_prefix_caption_may_carry_a_long_punctuated_title(declared: Any) -> None:
    title = "TABLE OF RATES, FEES AND OTHER DUTIES ON SEVEN COUNTED WORDS."
    text = f"Section 1\nOne.\n\n{CLOSING}\n\n{title}\n1. Two coins.\n"
    assert [a.heading for a in _scan(text).anchors if a.kind == "schedule"] == [title]


def test_an_attachment_keyword_scan_keeps_the_suffix(declared: Any) -> None:
    levels = [a for a in jurisdictions.load_config(COUNTRY).attachments if a.caption == "ANNEX"][
        0
    ].hierarchy
    window = "ANNEX\nRule 1\nAnnex one.\n\nRule 1 zib\nAnnex one bis.\n"
    found = anchors_mod._scan_attachment_keywords(
        window, 0, levels, jurisdictions.load_config(COUNTRY), []
    )
    assert [a.number for a in found] == ["1", "1 zib"]


def test_outputs_keep_source_offsets_after_the_cut(declared: Any) -> None:
    """The trace and the verbatim lane locate the source; the cut is internal."""
    from codify.pipeline.enrich.verbatim import text_to_bluebell_verbatim

    text = "AN ACT\n\n" + TAIL
    _, _, at_source = text_to_bluebell_verbatim(text, country=COUNTRY)
    note = next(a for a in at_source.values() if a.kind == "schedule")
    assert text[note.char_offset :].lstrip().startswith("NOTE")
    traces: list = []

    async def run() -> None:
        await structure_mod.text_to_bluebell_scaffolded(
            text, client=_EmptyFillClient(), country=COUNTRY, doctype="act", on_scan=traces.append
        )

    asyncio.run(run())
    traced = next(a for a in traces[0].anchors if a.kind == "schedule")
    assert traced.char_offset == note.char_offset


def test_a_caption_after_the_closing_phrase_survives_the_continuity_test(declared: Any) -> None:
    """Body 1, an excluded appended 1, then a note holding 2: continuity would read
    the note as a table caption in the body. Past the signature it is an attachment."""
    text = (
        f"Section 1\nOne.\n\n{CLOSING}\n\nAppended\nSection 1\nAppended one.\n\n"
        "NOTE\nWhy.\n\nSection 2\nNote two.\n"
    )
    scan = _scan(text)
    notes = [a for a in scan.anchors if a.kind == "schedule"]
    assert [a.heading for a in notes] == ["NOTE"]
    bound = bound_body_at_closing(text, scan.anchors, [CLOSING], country=COUNTRY)
    assert "Note two." not in (bound.conclusions or "")


def test_an_ascii_suffix_alias_folds_in_the_parsed_eid(monkeypatch: pytest.MonkeyPatch) -> None:
    from codify.pipeline.enrich.bluebell import _ascii_fold_eid

    monkeypatch.setattr(jurisdictions, "insertion_suffix_folds", lambda: dict(SUFFIXES))
    assert _ascii_fold_eid("sec_5zib") == "sec_5bis"
    assert _ascii_fold_eid("sec_5zib_2__p_1") == "sec_5bis_2__p_1"
    assert _ascii_fold_eid("sec_5") == "sec_5"


def test_declared_markers_in_the_denominator_stop_at_the_closing_phrase(
    declared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A basic unit marked without a keyword ("5.") is counted by its own scanner."""
    from codify.pipeline.enrich.anchors import _marker_numbers

    config = jurisdictions.load_config(COUNTRY)
    act = config.document_classes["act"]
    marked = act.model_copy(
        update={
            "hierarchy": [
                jurisdictions.HierarchyEntry(
                    local_term="Item",
                    akn_element="section",
                    level="basic",
                    numbering="arabic_period",
                    marker_form="arabic_period",
                )
            ]
        }
    )
    config = config.model_copy(
        update={"document_classes": {**config.document_classes, "act": marked}}
    )
    original = jurisdictions.load_config
    monkeypatch.setattr(
        anchors_mod, "load_config", lambda c: config if c == COUNTRY else original(c)
    )
    text = f"1. One.\n\n2. Two.\n\n{CLOSING}\n\n5. Five.\n\n6. Six.\n"
    expected = _marker_numbers(text, config, "act", "section")
    assert expected == {"1", "2"}, expected


def test_one_floor_serves_the_keyword_and_bare_grammars(
    declared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The body uses the keyword form and the appended instrument the bare one; the
    bare markers, all past the closing phrase, must not set the floor after themselves."""
    from codify.pipeline.enrich.anchors import _marker_numbers

    config = jurisdictions.load_config(COUNTRY)
    act = config.document_classes["act"]
    entries = list(act.hierarchy) + [
        jurisdictions.HierarchyEntry(
            local_term="Item",
            akn_element="section",
            level="basic",
            numbering="arabic_period",
            marker_form="arabic_period",
        )
    ]
    marked = act.model_copy(update={"hierarchy": entries})
    config = config.model_copy(
        update={"document_classes": {**config.document_classes, "act": marked}}
    )
    original = jurisdictions.load_config
    monkeypatch.setattr(
        anchors_mod, "load_config", lambda c: config if c == COUNTRY else original(c)
    )
    text = f"Section 1\nOne.\n\nSection 2\nTwo.\n\n{CLOSING}\n\n5. Five.\n\n6. Six.\n"
    expected = _marker_numbers(text, config, "act", "section")
    assert expected == {"1", "2"}, expected


def test_conclusions_keep_their_paragraph_breaks(declared: Any) -> None:
    lines = scaffold_mod._conclusions_lines(f"  {CLOSING}\n\n\nThe Warden\n  Clerk\n\n")
    assert lines == ["CONCLUSIONS", f"  {CLOSING}", "", "  The Warden", "  Clerk", ""]


def test_a_slashed_base_with_a_suffix_keys_as_the_parser_does() -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(jurisdictions, "insertion_suffix_folds", lambda: dict(SUFFIXES))
        assert anchors_mod._normalise_number("7/1 zib") == "7-1bis"


def test_a_prefix_caption_is_a_whole_word_outside_unspaced_scripts(declared: Any) -> None:
    text = (
        f"Section 1\nOne.\n\n{CLOSING}\n\nTABLE OFFERS AND SUCH\nprose.\n\nTABLE OF FEES\n1. Two.\n"
    )
    assert [a.heading for a in _scan(text).anchors if a.kind == "schedule"] == ["TABLE OF FEES"]


def test_the_denominator_reads_a_suffix_case_sensitively(declared: Any) -> None:
    from codify.pipeline.enrich.anchors import _marker_numbers

    text = "Section 5\nFive.\n\nSection 6 ZIB\nProse, not an inserted unit.\n"
    assert _marker_numbers(text, jurisdictions.load_config(COUNTRY), "act", "section") == {"5", "6"}


def test_a_bare_keyword_ending_a_line_is_not_a_quote_boundary(declared: Any) -> None:
    boundary = anchors_mod._basic_unit_line_re(COUNTRY)
    assert boundary is not None
    assert boundary.search("Section\n5\nText")
    assert boundary.search("Section\ncontinues here") is None


def test_a_rule_based_unit_bounds_a_quotation_too(monkeypatch: pytest.MonkeyPatch) -> None:
    original = jurisdictions.load_config
    base = original(COUNTRY)
    act = base.document_classes["act"]
    ruled = act.model_copy(
        update={
            "hierarchy": [
                jurisdictions.HierarchyEntry(
                    local_term="Rule", akn_element="rule", level="basic", numbering="arabic"
                )
            ]
        }
    )
    config = base.model_copy(update={"document_classes": {**base.document_classes, "act": ruled}})
    monkeypatch.setattr(
        anchors_mod, "load_config", lambda c: config if c == COUNTRY else original(c)
    )
    anchors_mod._basic_unit_line_re.cache_clear()
    boundary = anchors_mod._basic_unit_line_re(COUNTRY)
    anchors_mod._basic_unit_line_re.cache_clear()
    assert boundary is not None and boundary.search("Rule 2\nText")


def test_an_attachment_keyword_scan_refuses_a_citation_run(declared: Any) -> None:
    levels = [a for a in jurisdictions.load_config(COUNTRY).attachments if a.caption == "ANNEX"][
        0
    ].hierarchy
    window = "ANNEX\nRule 1\nAnnex one.\n\nRule 2 to Rule 4 apply.\nProse.\n"
    found = anchors_mod._scan_attachment_keywords(
        window, 0, levels, jurisdictions.load_config(COUNTRY), []
    )
    assert [a.number for a in found] == ["1"]


def test_glyph_repair_never_touches_the_suffix(
    declared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the digit-glyph tolerance on, `2O` repairs to 20 and the suffix stays a word."""
    config = jurisdictions.load_config(COUNTRY)
    tolerant = config.model_copy(
        update={
            "structuring": config.structuring.model_copy(
                update={
                    "marker_tolerances": ["digit_glyph"],
                    "insertion_suffixes": {"novies": "novies"},
                }
            )
        }
    )
    original = jurisdictions.load_config
    monkeypatch.setattr(
        anchors_mod, "load_config", lambda c: tolerant if c == COUNTRY else original(c)
    )
    monkeypatch.setattr(jurisdictions, "insertion_suffix_folds", lambda: {"novies": "novies"})
    anchors_mod.cached_regex.cache_clear()
    regex = build_anchor_regex(tolerant, "act")
    scan = scan_anchors_with_ambiguity(
        "Section 19\nOne.\n\nSection 2O novies\nTwo.\n", regex, country=COUNTRY, doctype="act"
    )
    anchors_mod.cached_regex.cache_clear()
    assert [n for n, _ in _sections(scan)] == ["19", "20 novies"]


def test_the_denominator_and_the_scan_key_a_spaced_slash_alike(declared: Any) -> None:
    from codify.pipeline.enrich.anchors import _marker_numbers

    text = "Section 7\nSeven.\n\nSection 7 / 1\nSeven one.\n"
    expected = _marker_numbers(text, jurisdictions.load_config(COUNTRY), "act", "section")
    assert expected == {"7", "7-1"}


def test_a_wrapped_roman_heading_bounds_a_quotation(declared: Any) -> None:
    boundary = anchors_mod._basic_unit_line_re(COUNTRY)
    assert boundary is not None
    assert boundary.search("Section\nii\nText") and boundary.search("Section\nІ\nText")


@pytest.mark.parametrize(
    "suffixes",
    [
        {"": "bis"},
        {" zib": "bis"},
        {"z ib": "bis"},
        {"foo_bar": "bis"},
        {"**": "bis"},
        {"z-b": "bis"},
        {"A": "bis"},
        {"12": "bis"},
        {"z2": "bis"},
        {"zib": ""},
        {"zib": "b is"},
        {"zib": "bis/evil"},
        {"zib": "bis__p_1"},
        {"zib": "Bis"},
    ],
)
def test_a_suffix_declaration_must_be_a_distinctive_word(suffixes: dict[str, str]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        jurisdictions.StructuringConfig(insertion_suffixes=suffixes)
    # A single character in a script that writes its suffix so is a word.
    assert jurisdictions.StructuringConfig(insertion_suffixes={"ζ": "sexies"})


@pytest.mark.parametrize("field", ["citation_successors", "prose_precursors"])
def test_a_blank_cue_word_is_refused(field: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        jurisdictions.StructuringConfig(**{field: ["to", ""]})
    assert getattr(jurisdictions.StructuringConfig(**{field: ["to"]}), field) == ["to"]


def test_a_keyword_shaped_conclusion_line_stays_text(declared: Any) -> None:
    """An appended instrument's upper-case marker in the conclusions is a paragraph."""
    from codify.pipeline.enrich.bluebell import parse_to_akn

    lines = scaffold_mod._conclusions_lines(f"{CLOSING}\nSECTION 9\nThe Warden")
    assert lines[2] == "  \\SECTION 9"
    bluebell = "BODY\n  SECTION 1\n    One.\n\n" + "\n".join(lines) + "\n"
    akn = parse_to_akn(bluebell, country=COUNTRY, doctype="act", date="2020", number="1")
    conclusions = akn[akn.index("<conclusions") : akn.index("</conclusions>")]
    assert "SECTION 9" in conclusions and "<section" not in conclusions
    assert akn[akn.index("<body") : akn.index("</body>")].count("<section ") == 1


def test_an_excluded_tail_marker_is_not_reported_unclaimed(declared: Any) -> None:
    text = f"Section 1\nOne.\n\n{CLOSING}\n\nAppended\nSection (1)\nAppended one.\n"
    scan = _scan(text)
    unclaimed = [
        sp for sp in scan.ambiguity if sp.detail.get("reason") == "marker_shaped_line_unclaimed"
    ]
    assert unclaimed == [], unclaimed


def test_a_keyword_opening_prose_on_its_line_is_not_a_quote_boundary(declared: Any) -> None:
    boundary = anchors_mod._basic_unit_line_re(COUNTRY)
    assert boundary is not None
    assert boundary.search("Section continues as follows") is None
    assert boundary.search("Section 12 Heading")


def test_the_container_probe_reads_the_cut_text(
    declared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe pairs anchors with text; after the cut it must get the rebased list."""
    seen: list[Any] = []
    real = structure_mod.container_coverage_probe

    def spy(text: str, scan: Any, *args: Any) -> Any:
        seen.append((text, scan))
        return real(text, scan, *args)

    monkeypatch.setattr(structure_mod, "container_coverage_probe", spy)

    async def run() -> None:
        await structure_mod.text_to_bluebell_scaffolded(
            "AN ACT\n\n" + TAIL, client=_EmptyFillClient(), country=COUNTRY, doctype="act"
        )

    asyncio.run(run())
    text, scan = seen[0]
    note = next(a for a in scan.anchors if a.kind == "schedule")
    assert text[note.char_offset :].lstrip().startswith("NOTE")


def test_the_quote_boundary_reads_every_heading_shape_the_scanner_does(declared: Any) -> None:
    boundary = anchors_mod._basic_unit_line_re(COUNTRY)
    assert boundary is not None
    for heading in ("Section (2)", "Section. 2", "Section 2A", "Section\n5", "Section ii"):
        assert boundary.search(heading + "\nText"), heading
    assert boundary.search("Section continues as follows") is None


def test_the_verbatim_heading_never_carries_a_carriage_return(declared: Any) -> None:
    from codify.pipeline.enrich.verbatim import fill_bodies_verbatim

    text = "Section 5\r\nzter*\r\nFive ter.\r\n\r\nSection 6\r\nSix.\r\n"
    scan = _scan(text)
    bodies = {b.eid: b for b in fill_bodies_verbatim(text, scan.anchors).bodies}
    assert bodies["sec_5ter"].heading is None and bodies["sec_6"].heading is None
    assert bodies["sec_5ter"].lines == ["Five ter."]


def test_a_keyword_less_unit_bounds_a_quotation(monkeypatch: pytest.MonkeyPatch) -> None:
    original = jurisdictions.load_config
    base = original(COUNTRY)
    act = base.document_classes["act"]
    bare = act.model_copy(
        update={
            "hierarchy": [
                jurisdictions.HierarchyEntry(
                    local_term="Item",
                    akn_element="section",
                    level="basic",
                    numbering="arabic_period",
                    marker_form="arabic_period",
                )
            ]
        }
    )
    config = base.model_copy(update={"document_classes": {**base.document_classes, "act": bare}})
    monkeypatch.setattr(
        anchors_mod, "load_config", lambda c: config if c == COUNTRY else original(c)
    )
    anchors_mod._basic_unit_line_re.cache_clear()
    boundary = anchors_mod._basic_unit_line_re(COUNTRY)
    anchors_mod._basic_unit_line_re.cache_clear()
    assert boundary is not None
    assert boundary.search("2. Next provision") and boundary.search("prose 2. no") is None


def test_a_suffix_is_matched_at_the_end_of_a_lettered_base() -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(jurisdictions, "insertion_suffix_folds", lambda: dict(SUFFIXES))
        assert jurisdictions.fold_inserted_suffix("IV zib") == "IVbis"
        assert jurisdictions.fold_inserted_suffix("IVzib") == "IVbis"
        assert jurisdictions.fold_inserted_suffix("zib") is None
        assert jurisdictions.fold_inserted_suffix("5 zibber") is None


def test_a_direct_conclusions_block_still_groups_the_signatory(declared: Any) -> None:
    from lxml import etree

    from codify.pipeline.enrich.bluebell import parse_to_akn
    from codify.pipeline.enrich.conclusions import emit_conclusions
    from codify.pipeline.enrich.regions import RegionVocabulary

    lines = scaffold_mod._conclusions_lines(f"{CLOSING}\nThe Warden\nHarbour Clerk")
    akn = parse_to_akn(
        "BODY\n  SECTION 1\n    One.\n\n" + "\n".join(lines) + "\n",
        country=COUNTRY,
        doctype="act",
        date="2020",
        number="1",
    )
    out = emit_conclusions(akn, vocab=RegionVocabulary(closing_phrases=(CLOSING,)))
    block = etree.fromstring(out.encode()).find(
        f".//{{{AKN_NS}}}conclusions/{{{AKN_NS}}}blockContainer"
    )
    assert block is not None and block.get("eId") == "sig_1"
    assert [" ".join(p.itertext()) for p in block] == ["The Warden", "Harbour Clerk"]


def test_a_long_conclusions_block_is_not_regrouped(declared: Any) -> None:
    from codify.pipeline.enrich.bluebell import parse_to_akn
    from codify.pipeline.enrich.conclusions import emit_conclusions
    from codify.pipeline.enrich.regions import RegionVocabulary

    body = "\n".join(f"Appended line {i} of the instrument" for i in range(12))
    lines = scaffold_mod._conclusions_lines(f"{CLOSING}\nThe Warden\n{body}")
    akn = parse_to_akn(
        "BODY\n  SECTION 1\n    One.\n\n" + "\n".join(lines) + "\n",
        country=COUNTRY,
        doctype="act",
        date="2020",
        number="1",
    )
    assert emit_conclusions(akn, vocab=RegionVocabulary(closing_phrases=(CLOSING,))) == akn


def test_a_foreign_conclusions_block_is_left_alone(declared: Any) -> None:
    from codify.pipeline.enrich.conclusions import emit_conclusions
    from codify.pipeline.enrich.regions import RegionVocabulary

    akn = (
        f'<akomaNtoso xmlns="{AKN_NS}"><act name="act"><body><section eId="sec_1">'
        "<num>1</num><content><p>One.</p></content></section></body><conclusions>"
        "<p>Made by another parser.</p><p>The Warden</p><p>Harbour Clerk</p>"
        "</conclusions></act></akomaNtoso>"
    )
    assert emit_conclusions(akn, vocab=RegionVocabulary(closing_phrases=(CLOSING,))) == akn


def test_a_restored_table_body_consumes_a_wrapped_marker(declared: Any) -> None:
    from codify.pipeline.enrich.scaffold import BodyBlock
    from codify.pipeline.enrich.table_fidelity import preserve_source_tables

    text = "Section 5\nzter*\n| A | B |\n|---|---|\n| 1 | 2 |\n\nSection 6\nSix.\n"
    scan = _scan(text)
    bodies = {"sec_5ter": BodyBlock(eid="sec_5ter", lines=["A B 1 2"])}
    restored = preserve_source_tables(text, scan.anchors, bodies)
    assert "sec_5ter" in restored
    assert bodies["sec_5ter"].heading is None
    assert not any("zter" in line for line in bodies["sec_5ter"].lines), bodies["sec_5ter"]
