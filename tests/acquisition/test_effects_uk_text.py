"""Replacement text is attached only where the amending act names the target.

Every act here is invented; the amending act changes two statutes at once.
"""

from __future__ import annotations

import pytest
from lxml import etree

from codify.acquisition.effects.uk_text import EffectText, Refused, effect_text

_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
LIGHTS = "Harbour Lights Act 2018"
FERRIES = "Tidal Ferries Act 2011"


def _act(body: str) -> etree._Element:
    return etree.fromstring(
        f'<akomaNtoso xmlns="{_NS}"><act><body>{body}</body></act></akomaNtoso>'
    )


def _leaves(schedule: int, first: int, *texts: str) -> str:
    return "".join(
        f'<paragraph eId="schedule-{schedule}-paragraph-{n}"><num>{n}</num>'
        f"<content><p>{text}</p></content></paragraph>"
        for n, text in enumerate(texts, first)
    )


_NOTE = f"<authorialNote><p>Amended by the {FERRIES}.</p></authorialNote>"
_LIGHTS_GROUP = _leaves(
    1,
    1,
    f"The {LIGHTS}{_NOTE} is amended as follows.",
    "In section 5(2), for “lamp” substitute “beacon”.",
    "In section 6, for the words from “dusk” to “dawn” substitute “at night”.",
    "In section 7, for “a” substitute “b” and after “c” insert “d”.",
    "In section 8, for “oil there shall be substituted “gas”.",
)
_FERRIES_GROUP = _leaves(
    1,
    6,
    f"The {FERRIES} is amended as follows.",
    "In section 5(2), for “pier” there shall be substituted “quay”.",
    f"In section 3 of the {LIGHTS}, omit “red”.",
    "In section 4, for “bell” substitute “horn”.",
    f"In section 4 of the {FERRIES}, as applied by the {LIGHTS}, for “a” substitute “b”.",
    "In section 8, after “wick”” insert “s”.",
)
_AMENDER = _act(
    f"""
    <section eId="section-1"><num>1</num>
      <subsection eId="section-1-1"><content><p>In this Act “the 2018 Act” means the
        {LIGHTS}.</p></content></subsection>
    </section>
    <section eId="section-2"><num>2</num>
      <subsection eId="section-2-1"><content><p>The 2018 Act is amended as follows.</p></content>
      </subsection>
      <subsection eId="section-2-2"><content><p>Section 9 is amended as follows.</p></content>
      </subsection>
      <subsection eId="section-2-3"><content><p>In subsection (1) (duties of the keeper under the
        {FERRIES}), after “keeper” insert “or deputy”.</p></content></subsection>
      <subsection eId="section-2-4"><content><p><mod>After section 10 insert—
        <quotedStructure><section><num>10A</num><heading>Fog signals</heading><content>
        <p>A signal must sound in fog.</p></content></section><section><num>10B</num>
        <heading>Records</heading><content><p>The keeper must keep a log.</p></content></section>
        </quotedStructure></mod></p></content></subsection>
    </section>
    <hcontainer name="schedule" eId="schedule-1"><num>Schedule 1</num>
      <hcontainer name="crossheading"><heading>Lights</heading>{_LIGHTS_GROUP}</hcontainer>
      <hcontainer name="crossheading"><heading>Ferries</heading>{_FERRIES_GROUP}</hcontainer>
    </hcontainer>
    <hcontainer name="schedule" eId="schedule-2"><num>Schedule 2</num>
      {_leaves(2, 1, "In section 5, for “x” substitute “y”.")}
    </hcontainer>
    """
)


def _read(
    source: str, target: str = "section-5-2", action: str = "substitution", title: str = LIGHTS
) -> EffectText | Refused:
    return effect_text(
        _AMENDER, source_eid=source, target_eid=target, action=action, target_titles=[title]
    )


def test_text_under_an_opener_naming_the_target_is_attached() -> None:
    got = _read("schedule-1-paragraph-2")
    assert isinstance(got, EffectText)
    assert (got.old, got.new) == (("lamp",), ("beacon",))
    assert got.instruction == "In section 5(2), for “lamp” substitute “beacon”."


def test_the_same_provision_number_under_another_act_is_refused() -> None:
    assert _read("schedule-1-paragraph-7") == Refused("names_another_act")
    got = _read("schedule-1-paragraph-7", title=FERRIES)
    assert isinstance(got, EffectText) and got.new == ("quay",)


def test_an_instruction_after_one_naming_another_act_is_under_neither() -> None:
    assert _read("schedule-1-paragraph-9", title=FERRIES) == Refused("names_another_act")
    assert _read("schedule-1-paragraph-9") == Refused("names_another_act")


def _one(instruction: str, title: str = LIGHTS, target: str = "section-5") -> EffectText | Refused:
    doc = _act(
        '<hcontainer name="schedule" eId="schedule-1">'
        + _leaves(1, 1, f"The {LIGHTS} is amended as follows.", instruction)
        + "</hcontainer>"
    )
    return effect_text(
        doc,
        source_eid="schedule-1-paragraph-2",
        target_eid=target,
        action="substitution",
        target_titles=[title],
    )


@pytest.mark.parametrize(
    "instruction",
    [
        "In Article 6 of Regulation (EU) 2031/123, for “c” substitute “d”.",
        "In Article 2 of Council Directive 2031/45/EC, for “c” substitute “d”.",
        "In Article 5 of the Regulation, for “c” substitute “d”.",
        "In article 5 of that instrument, for “c” substitute “d”.",
        "In section 5 of the principal Act, for “c” substitute “d”.",
        "In section 5 of that Act, for “c” substitute “d”.",
        "In regulation 5 of these Regulations, for “c” substitute “d”.",
    ],
)
def test_an_instrument_cited_in_a_form_not_listed_is_still_another_act(instruction: str) -> None:
    assert isinstance(_one("In section 5, for “c” substitute “d”."), EffectText)
    assert _one(instruction) == Refused("names_another_act")


@pytest.mark.parametrize(
    "opener",
    [
        "Regulation (EU) No 1234/2031 is amended as follows.",
        "The Rules of the Harbour Court (Northern Ireland) 2031 are amended as follows.",
        "The Table of Fees Order is amended as follows.",
    ],
)
def test_an_opener_for_an_instrument_named_like_a_unit_ends_the_scope(opener: str) -> None:
    doc = _act(
        '<hcontainer name="schedule" eId="schedule-1">'
        + _leaves(1, 1, f"The {LIGHTS} is amended as follows.", opener, "In Article 5, omit “c”.")
        + _leaves(1, 4, "In Article 5, for “c” substitute “d”.")
        + "</hcontainer>"
    )
    got = effect_text(
        doc,
        source_eid="schedule-1-paragraph-4",
        target_eid="article-5",
        action="substitution",
        target_titles=[LIGHTS],
    )
    assert got == Refused("names_another_act")


def test_scope_does_not_cross_into_another_part_of_a_schedule() -> None:
    doc = _act(
        f"""<hcontainer name="schedule" eId="schedule-1">
        <part eId="schedule-1-part-1"><heading>Primary legislation</heading>
        {_leaves(1, 1, f"The {LIGHTS} is amended as follows.")}</part>
        <part eId="schedule-1-part-2"><heading>Other amendments</heading>
        {_leaves(1, 2, "In section 5, for “c” substitute “d”.")}</part></hcontainer>"""
    )
    got = effect_text(
        doc,
        source_eid="schedule-1-paragraph-2",
        target_eid="section-5",
        action="substitution",
        target_titles=[LIGHTS],
    )
    assert got == Refused("target_not_named")


def test_a_short_form_inside_a_longer_name_is_not_the_target() -> None:
    doc = _act(
        f"""<section eId="section-1"><content><p>“the Act” means the {LIGHTS}.</p></content>
        </section><section eId="section-2"><content><p>In rule 5 of the Act of Sederunt (Fees)
        2031, for “a” substitute “b”.</p></content></section>"""
    )
    got = effect_text(
        doc,
        source_eid="section-2",
        target_eid="rule-5",
        action="substitution",
        target_titles=[LIGHTS],
    )
    assert got == Refused("names_another_act")


def test_a_short_form_also_met_in_an_unreadable_definition_is_not_trusted() -> None:
    doc = _act(
        f"""<section eId="section-1"><content><p>“the principal Act” means the {LIGHTS}.</p>
        <p>“the principal Act” means the {FERRIES} and “ferry includes a hovercraft.</p></content>
        </section><section eId="section-2"><content><p>In section 5 of the principal Act, for
        “a” substitute “b”.</p></content></section>"""
    )
    got = effect_text(
        doc,
        source_eid="section-2",
        target_eid="section-5",
        action="substitution",
        target_titles=[LIGHTS],
    )
    assert got == Refused("names_another_act")


def test_a_unit_at_another_level_with_the_same_number_is_not_the_target() -> None:
    doc = _act(
        f"""<section eId="section-1"><content><p><mod>In the {LIGHTS}, for subsections (1) and
        (2) substitute— <quotedStructure><subsection><num>(1)</num><content><p>one</p></content>
        </subsection><subsection><num>(2)</num><content><p>two</p></content></subsection>
        </quotedStructure></mod></p></content></section>"""
    )

    def read(target: str) -> EffectText | Refused:
        return effect_text(
            doc,
            source_eid="section-1",
            target_eid=target,
            action="substitution",
            target_titles=[LIGHTS],
        )

    assert read("section-2") == Refused("quoted_unit_differs")
    got = read("section-7-2")
    assert isinstance(got, EffectText) and got.new == ("(2) two",)


def test_old_is_the_quote_the_substitution_is_for_and_no_earlier_one() -> None:
    got = _one("In the entry for “Harbour Board”, for paragraph (b) substitute “the Board”.")
    assert isinstance(got, EffectText) and (got.old, got.new) == ((), ("the Board",))


def test_the_first_act_an_instruction_names_is_the_one_it_amends() -> None:
    assert _read("schedule-1-paragraph-10") == Refused("names_another_act")
    assert isinstance(_read("schedule-1-paragraph-10", title=FERRIES), EffectText)


def test_scope_does_not_cross_into_another_schedule() -> None:
    assert _read("schedule-2-paragraph-1") == Refused("target_not_named")


def test_a_short_form_the_act_defines_names_the_target() -> None:
    got = _read("section-2-3", target="section-9-1", action="insertion")
    assert isinstance(got, EffectText) and (got.old, got.new) == ((), ("or deputy",))
    assert _read("section-2-3", action="insertion", title=FERRIES) == Refused("names_another_act")


def test_a_short_form_given_to_two_acts_names_neither() -> None:
    twice = _act(
        f"""<section eId="section-1"><content><p>“the principal Act” means the {LIGHTS}.</p>
        <p>“the principal Act” means the {FERRIES}.</p></content></section>
        <section eId="section-2"><content><p>In section 5 of the principal Act, for “a”
        substitute “b”.</p></content></section>"""
    )
    got = effect_text(
        twice,
        source_eid="section-2",
        target_eid="section-5",
        action="substitution",
        target_titles=[LIGHTS],
    )
    assert got == Refused("names_another_act")


def test_a_quoted_structure_is_cut_to_the_unit_the_effect_names() -> None:
    got = _read("section-2-4", target="section-10B", action="insertion")
    assert isinstance(got, EffectText)
    assert got.new == ("10B Records The keeper must keep a log.",)
    assert _read("section-2-4", target="section-11", action="insertion") == Refused(
        "quoted_unit_differs"
    )


def test_a_described_span_leaves_old_empty() -> None:
    got = _read("schedule-1-paragraph-3", target="section-6")
    assert isinstance(got, EffectText) and (got.old, got.new) == ((), ("at night",))


@pytest.mark.parametrize(
    ("source", "action", "reason"),
    [
        ("schedule-1-paragraph-2", "repeal", "action_carries_no_text"),
        ("schedule-1-paragraph-2", "insertion", "verb_differs_from_effect"),
        ("schedule-1-paragraph-4", "substitution", "several_instructions"),
        ("schedule-1-paragraph-5", "substitution", "quotes_unbalanced"),
        ("schedule-1-paragraph-11", "insertion", "quotes_unbalanced"),
        ("schedule-1-paragraph-8", "substitution", "no_instruction"),
        ("schedule-9-paragraph-1", "substitution", "pointer_not_found"),
    ],
)
def test_what_cannot_be_read_is_refused_by_reason(source: str, action: str, reason: str) -> None:
    assert _read(source, action=action) == Refused(reason)


def test_an_unreadable_instruction_before_this_one_leaves_scope_unknown() -> None:
    broken = _act(
        '<hcontainer name="schedule" eId="schedule-1">'
        + _leaves(
            1,
            1,
            f"The {LIGHTS} is amended as follows.",
            "In section 8, for “oil substitute “gas”.",
            "In section 9, for “a” substitute “b”.",
        )
        + "</hcontainer>"
    )
    got = effect_text(
        broken,
        source_eid="schedule-1-paragraph-3",
        target_eid="section-9",
        action="substitution",
        target_titles=[LIGHTS],
    )
    assert got == Refused("scope_unreadable")


def test_a_pointer_two_provisions_answer_to_is_refused() -> None:
    twice = _act(_leaves(1, 1, f"In section 5 of the {LIGHTS}, for “a” substitute “b”.") * 2)
    got = effect_text(
        twice,
        source_eid="schedule-1-paragraph-1",
        target_eid="section-5",
        action="substitution",
        target_titles=[LIGHTS],
    )
    assert got == Refused("pointer_not_found")


@pytest.mark.parametrize(
    "instruction",
    [
        "substitute “b” for “a”",
        "insert “b” after “a”",
        "there shall be inserted after “a” the words “b”",
        "there shall be substituted for the word “a” the word “b”",
        "there shall be inserted after the word “a” the words “b”",
    ],
)
def test_a_quote_the_instruction_locates_by_is_not_replacement_text(instruction: str) -> None:
    doc = _act(_leaves(1, 1, f"In section 5 of the {LIGHTS}, {instruction}."))
    for action in ("substitution", "insertion"):
        got = effect_text(
            doc,
            source_eid="schedule-1-paragraph-1",
            target_eid="section-5",
            action=action,
            target_titles=[LIGHTS],
        )
        assert isinstance(got, Refused)


def test_a_longer_title_ending_in_the_targets_is_another_act() -> None:
    doc = _act(
        _leaves(1, 1, "In section 5 of the Flood and Water Act 2031, for “a” substitute “b”.")
    )
    got = effect_text(
        doc,
        source_eid="schedule-1-paragraph-1",
        target_eid="section-5",
        action="substitution",
        target_titles=["Water Act 2031"],
    )
    assert got == Refused("names_another_act")


def _defined(definition: str, instruction: str) -> EffectText | Refused:
    doc = _act(
        f"""<section eId="section-1"><content><p>“the Act” means the {LIGHTS}.</p>
        <p>{definition}</p></content></section>
        <section eId="section-2"><content><p>{instruction}</p></content></section>"""
    )
    return effect_text(
        doc,
        source_eid="section-2",
        target_eid="section-5",
        action="substitution",
        target_titles=[LIGHTS],
    )


@pytest.mark.parametrize(
    ("definition", "instruction"),
    [
        (
            f"“the Order” means the {LIGHTS} (Fog Signals) Order 2031.",
            "In article 5 of the Order, for “a” substitute “b”.",
        ),
        (
            f"The Outer {LIGHTS} (“the 2018 Act”) is repealed.",
            "In section 5 of the 2018 Act, for “a” substitute “b”.",
        ),
        (
            f"In Part 2 the {FERRIES} is referred to as “the Act”.",
            "In section 5 of the Act, for “a” substitute “b”.",
        ),
        (
            f"In Part 2 references to “the Act” are to the {FERRIES}.",
            "In section 5 of the Act, for “a” substitute “b”.",
        ),
        (
            f"“the Acts” means the {LIGHTS} or the {FERRIES}, as the case may be.",
            "In section 5 of the Acts, for “a” substitute “b”.",
        ),
    ],
)
def test_a_short_form_is_the_targets_only_when_nothing_else_claims_it(
    definition: str, instruction: str
) -> None:
    assert isinstance(
        _defined("Nothing.", "In section 5 of the Act, for “a” substitute “b”."), EffectText
    )
    assert _defined(definition, instruction) == Refused("names_another_act")


def _headed(own: str, group: str, target: str = "section-5") -> EffectText | Refused:
    doc = _act(
        f"""<hcontainer name="schedule" eId="schedule-1"><heading>Amendments</heading>
        <hcontainer name="crossheading"><heading>Lights</heading>
        {_leaves(1, 1, f"The {LIGHTS} is amended as follows.")}</hcontainer>
        <hcontainer name="crossheading"><heading>{group}</heading>
        <paragraph eId="schedule-1-paragraph-2"><heading>{own}</heading><content>
        <p>In section 5, for “a” substitute “b”.</p></content></paragraph></hcontainer>
        </hcontainer>"""
    )
    return effect_text(
        doc,
        source_eid="schedule-1-paragraph-2",
        target_eid=target,
        action="substitution",
        target_titles=[LIGHTS],
    )


def test_an_opener_under_one_group_heading_does_not_govern_the_next_group() -> None:
    assert _headed("Lamps", "Transitional provision") == Refused("target_not_named")
    assert isinstance(_headed("Lamps", f"Further amendments of the {LIGHTS}"), EffectText)


def test_the_instructions_own_heading_is_read_before_any_above_it() -> None:
    group = f"Further amendments of the {LIGHTS}"
    assert _headed(f"Amendment of the {FERRIES}", group) == Refused("names_another_act")


def test_a_quoted_unit_of_another_kind_with_the_same_number_is_not_the_target() -> None:
    doc = _act(
        f"""<section eId="section-1"><content><p><mod>In the {LIGHTS}, after Part 4 insert—
        <quotedStructure><part><num>Part 5</num><heading>Fog</heading><section><num>5</num>
        <content><p>five</p></content></section></part></quotedStructure></mod></p></content>
        </section>"""
    )

    def read(target: str) -> EffectText | Refused:
        return effect_text(
            doc,
            source_eid="section-1",
            target_eid=target,
            action="insertion",
            target_titles=[LIGHTS],
        )

    assert read("section-5") == Refused("quoted_unit_differs")
    assert isinstance(read("part-5"), EffectText)


def test_old_is_left_empty_when_the_instruction_substitutes_for_two_quotes() -> None:
    got = _one(
        "In section 5, for “x” in subsection (2) and for “y” in subsection (3) substitute “z”."
    )
    assert isinstance(got, EffectText) and (got.old, got.new) == ((), ("z",))


def test_old_is_left_empty_when_the_quote_only_locates_what_is_replaced() -> None:
    got = _one("In section 5, for the entry for “Harbour Board” substitute “the Board”.")
    assert isinstance(got, EffectText) and (got.old, got.new) == ((), ("the Board",))


@pytest.mark.parametrize(
    "earlier",
    ["In Article 5 of the Regulation, omit “x”.", "In section 4 of the principal Act, omit “x”."],
)
def test_an_instruction_naming_anything_else_ends_the_scope_for_those_after_it(
    earlier: str,
) -> None:
    doc = _act(
        '<hcontainer name="schedule" eId="schedule-1">'
        + _leaves(1, 1, f"The {LIGHTS} is amended as follows.", earlier)
        + _leaves(1, 3, "In section 5, for “a” substitute “b”.")
        + "</hcontainer>"
    )
    got = effect_text(
        doc,
        source_eid="schedule-1-paragraph-3",
        target_eid="section-5",
        action="substitution",
        target_titles=[LIGHTS],
    )
    assert got == Refused("names_another_act")


def _quoting(quoted: str, target: str = "section-5") -> EffectText | Refused:
    doc = _act(
        f"""<section eId="section-1"><content><p><mod>In the {LIGHTS}, for section 5
        substitute {quoted}</mod></p></content></section>"""
    )
    return effect_text(
        doc,
        source_eid="section-1",
        target_eid=target,
        action="substitution",
        target_titles=[LIGHTS],
    )


def test_marks_inside_a_quoted_element_must_balance() -> None:
    assert isinstance(_quoting("<quotedText>“beacon”</quotedText>"), EffectText)
    assert _quoting("<quotedText>“beacon</quotedText>") == Refused("quotes_unbalanced")


def test_a_bare_numbered_unit_is_matched_as_the_element_it_is() -> None:
    def unit(name: str) -> str:
        return (
            f"<quotedStructure><{name}><num>5</num><content><p>five</p></content></{name}>"
            f"<{name}><num>6</num><content><p>six</p></content></{name}></quotedStructure>"
        )

    got = _quoting(unit("section"))
    assert isinstance(got, EffectText) and got.new == ("5 five",)
    assert _quoting(unit("paragraph")) == Refused("quoted_unit_differs")
    assert isinstance(_quoting(unit("paragraph"), "schedule-1-paragraph-5"), EffectText)


def test_a_unit_number_is_read_through_inline_markup() -> None:
    marked = (
        "<quotedStructure><section><num><b>5</b></num><content><p>five</p></content></section>"
        "<section><num>6</num><content><p>six</p></content></section></quotedStructure>"
    )
    got = _quoting(marked)
    assert isinstance(got, EffectText) and got.new == ("5 five",)
    assert _quoting(marked, "section-7") == Refused("quoted_unit_differs")


def test_a_title_only_referred_to_is_not_the_act_amended() -> None:
    doc = _act(
        '<hcontainer name="schedule" eId="schedule-1">'
        + _leaves(
            1,
            1,
            f"The {FERRIES} is amended as follows.",
            f"In section 5, for the reference to the {LIGHTS} substitute “the Board”.",
        )
        + "</hcontainer>"
    )
    got = effect_text(
        doc,
        source_eid="schedule-1-paragraph-2",
        target_eid="section-5",
        action="substitution",
        target_titles=[LIGHTS],
    )
    assert got == Refused("names_another_act")


def test_an_opener_inside_an_earlier_part_does_not_govern_what_follows_the_part() -> None:
    def read(opener_holder: str) -> EffectText | Refused:
        opener = _leaves(1, 1, f"The {LIGHTS} is amended as follows.")
        doc = _act(
            f"""<hcontainer name="schedule" eId="schedule-1"><{opener_holder}>{opener}
            </{opener_holder}>{_leaves(1, 2, "In section 5, for “a” substitute “b”.")}
            </hcontainer>"""
        )
        return effect_text(
            doc,
            source_eid="schedule-1-paragraph-2",
            target_eid="section-5",
            action="substitution",
            target_titles=[LIGHTS],
        )

    assert read("part") == Refused("target_not_named")
    assert isinstance(read("wrapper"), EffectText)


def test_scope_starts_at_the_paragraph_giving_the_instruction() -> None:
    doc = _act(
        f"""<section eId="section-1"><subsection eId="section-1-1"><content>
        <p>The {LIGHTS} is repealed.</p></content></subsection>
        <subsection eId="section-1-2"><content><p>In section 5 of the {FERRIES}, for “a”
        substitute “b”.</p></content></subsection></section>"""
    )

    def read(title: str) -> EffectText | Refused:
        return effect_text(
            doc,
            source_eid="section-1",
            target_eid="section-5",
            action="substitution",
            target_titles=[title],
        )

    assert read(LIGHTS) == Refused("names_another_act")
    assert isinstance(read(FERRIES), EffectText)


def test_no_title_is_no_target() -> None:
    assert _read("schedule-1-paragraph-2", title=" ") == Refused("target_title_unknown")
