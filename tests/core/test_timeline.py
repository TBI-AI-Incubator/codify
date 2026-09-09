"""What a provision was on a date, and what the reading could not say.

The rule these cover is that nothing is removed and nothing undated is placed.
Both come from measurement: the publisher keeps a repealed provision, and 14%
of one act's effects carry no date at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from codify.timeline import state_at


@dataclass
class E:
    """An effect, in the shape the rule needs."""

    target_akn_wid: str | None
    akn_action: str
    in_force_date: date | None
    applied: bool | None = True


def test_a_repeal_before_the_date_marks_the_provision() -> None:
    t = state_at([E("section-5", "repeal", date(2019, 1, 1))], date(2020, 1, 1))
    assert t.states["section-5"].kind == "repealed"
    assert t.states["section-5"].date == date(2019, 1, 1)


def test_a_repeal_after_the_date_has_not_happened_yet() -> None:
    t = state_at([E("section-5", "repeal", date(2021, 1, 1))], date(2020, 1, 1))
    assert "section-5" not in t.states
    assert t.holes.total == 0, "a future effect is not a hole"


def test_an_undated_effect_is_counted_never_placed() -> None:
    t = state_at([E("section-5", "repeal", None)], date(2020, 1, 1))
    assert t.states == {}
    assert t.holes.undated == 1


def test_an_effect_the_publisher_has_not_applied_is_counted() -> None:
    t = state_at([E("section-5", "repeal", date(2019, 1, 1), applied=False)], date(2020, 1, 1))
    assert t.states == {}
    assert t.holes.unapplied_by_publisher == 1


def test_a_substitution_marks_the_text_as_unapplied() -> None:
    t = state_at([E("section-5", "substitution", date(2019, 1, 1))], date(2020, 1, 1))
    assert t.states["section-5"].kind == "text_unapplied"
    assert t.holes.needs_text == 1


def test_a_repeal_outranks_an_earlier_unshown_amendment() -> None:
    """Order matters: saying the text is out of date, when the provision is
    gone, is the lesser truth."""
    t = state_at(
        [
            E("section-5", "substitution", date(2019, 1, 1)),
            E("section-5", "repeal", date(2019, 6, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "repealed"


def test_a_repeal_outranks_a_later_amendment_whatever_the_input_order() -> None:
    t = state_at(
        [
            E("section-5", "repeal", date(2019, 6, 1)),
            E("section-5", "substitution", date(2019, 1, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "repealed"


def test_a_repeal_survives_an_amendment_dated_after_it() -> None:
    """The contradictory case, and the only one the precedence rule decides:
    the feed carries an amendment to a provision it has already repealed.
    Sorting alone cannot answer it, because the amendment genuinely comes last.
    Repealed is the honest reading: the provision is gone, and reporting its
    words as out of date would imply there are words to read."""
    t = state_at(
        [
            E("section-5", "repeal", date(2019, 1, 1)),
            E("section-5", "substitution", date(2019, 6, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "repealed"
    assert t.holes.needs_text == 1, "the amendment is still an effect we did not apply"


def test_a_provision_not_yet_commenced_is_dormant_not_absent() -> None:
    t = state_at([E("section-6", "entryIntoForce", date(2026, 1, 1))], date(2020, 1, 1))
    assert t.states["section-6"].kind == "not_yet_in_force"


def test_commencement_without_a_date_cannot_be_called_live() -> None:
    t = state_at([E("section-6", "entryIntoForce", None)], date(2020, 1, 1))
    assert t.states["section-6"].kind == "not_yet_in_force"


def test_a_commenced_provision_carries_no_dormant_mark() -> None:
    t = state_at([E("section-6", "entryIntoForce", date(2019, 1, 1))], date(2020, 1, 1))
    assert t.states.get("section-6", None) is None or t.states["section-6"].kind == "in_force"


def test_an_effect_with_no_target_is_counted() -> None:
    t = state_at([E(None, "repeal", date(2019, 1, 1))], date(2020, 1, 1))
    assert t.holes.no_target == 1


def test_an_unhandled_action_is_named_not_swallowed() -> None:
    t = state_at([E("section-5", "variation", date(2019, 1, 1))], date(2020, 1, 1))
    assert dict(t.holes.out_of_scope) == {"variation": 1}


def test_change_dates_are_the_days_something_happened() -> None:
    t = state_at(
        [
            E("section-5", "repeal", date(2019, 1, 1)),
            E("section-6", "substitution", date(2018, 5, 25)),
            E("section-7", "repeal", date(2019, 1, 1)),
            E("section-8", "repeal", None),
        ],
        date(2020, 1, 1),
    )
    assert t.change_dates == [date(2018, 5, 25), date(2019, 1, 1)], "deduplicated, undated excluded"


def test_the_ceiling_counts_only_insertions() -> None:
    """Substitutions change words in place; insertions add provisions that are
    not in the document at all, which is what the reading cannot show."""
    t = state_at(
        [
            E("section-5", "substitution", date(2019, 1, 1)),
            E("section-9", "insertion", date(2019, 1, 1)),
            E("section-10", "insertion", date(2019, 1, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.insertions_unrenderable == 2
    assert t.holes.needs_text == 3


# --- Commencement against the rest -------------------------------------------
# Commencement was decided effect by effect, so it could overwrite a repeal and
# place an undated effect. It is decided per provision from all its
# commencements, after the other effects, and never over a repeal.


def test_an_undated_commencement_is_counted_and_does_not_place_itself() -> None:
    t = state_at(
        [
            E("section-5", "repeal", date(2019, 1, 1)),
            E("section-5", "entryIntoForce", None),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "repealed", "an undated effect cannot unseat a dated one"
    assert t.holes.undated == 1


def test_a_future_commencement_does_not_revive_a_repealed_provision() -> None:
    t = state_at(
        [
            E("section-5", "repeal", date(2019, 1, 1)),
            E("section-5", "entryIntoForce", date(2030, 1, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "repealed"


def test_a_provision_commenced_once_stays_in_force_despite_a_later_commencement() -> None:
    """The feed carries more than one commencement for a provision. Any date on
    or before the day being read means it was in force that day."""
    t = state_at(
        [
            E("section-5", "entryIntoForce", date(2018, 1, 1)),
            E("section-5", "entryIntoForce", date(2030, 1, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "in_force"


def test_a_commencement_the_publisher_has_not_applied_is_counted() -> None:
    t = state_at(
        [E("section-5", "entryIntoForce", date(2019, 1, 1), applied=False)],
        date(2020, 1, 1),
    )
    assert t.states.get("section-5") is None
    assert t.holes.unapplied_by_publisher == 1


def test_commencement_does_not_erase_an_unshown_amendment() -> None:
    t = state_at(
        [
            E("section-5", "entryIntoForce", date(2018, 1, 1)),
            E("section-5", "substitution", date(2019, 1, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "text_unapplied"


def test_an_effect_with_no_target_dated_in_the_future_is_not_a_hole_yet() -> None:
    t = state_at([E(None, "repeal", date(2030, 1, 1))], date(2020, 1, 1))
    assert t.holes.no_target == 0


# --- Actions we do not apply -------------------------------------------------
# Counted is not enough: a provision something happened to must not render as
# untouched. The mark carries the record's own word for it, nothing invented.


def test_an_unapplied_action_marks_the_provision_with_its_own_name() -> None:
    t = state_at([E("section-5", "variation", date(2019, 1, 1))], date(2020, 1, 1))
    assert t.states["section-5"].kind == "affected"
    assert t.states["section-5"].action == "variation"
    assert t.states["section-5"].date == date(2019, 1, 1)


def test_being_affected_never_displaces_a_repeal() -> None:
    t = state_at(
        [
            E("section-5", "repeal", date(2019, 1, 1)),
            E("section-5", "split", date(2019, 6, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "repealed"


def test_being_affected_never_displaces_an_unshown_amendment() -> None:
    t = state_at(
        [
            E("section-5", "substitution", date(2019, 1, 1)),
            E("section-5", "join", date(2019, 6, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "text_unapplied"


def test_dormant_outranks_affected() -> None:
    t = state_at(
        [
            E("section-5", "replacement", date(2019, 1, 1)),
            E("section-5", "entryIntoForce", date(2030, 1, 1)),
        ],
        date(2020, 1, 1),
    )
    assert t.states["section-5"].kind == "not_yet_in_force"


def test_an_action_dated_after_the_day_marks_nothing() -> None:
    t = state_at([E("section-5", "unconstitutionality", date(2030, 1, 1))], date(2020, 1, 1))
    assert t.states == {}


def test_future_unlocated_commencement_is_not_a_hole_until_due() -> None:
    effects = [E(None, "entryIntoForce", date(2030, 1, 1))]
    assert state_at(effects, date(2029, 1, 1)).holes.total == 0
    assert state_at(effects, date(2030, 1, 1)).holes.no_target == 1


def test_future_unapplied_commencement_is_not_a_hole_until_due() -> None:
    effects = [E("section-5", "entryIntoForce", date(2030, 1, 1), applied=False)]
    assert state_at(effects, date(2029, 1, 1)).holes.total == 0
    assert state_at(effects, date(2030, 1, 1)).holes.unapplied_by_publisher == 1
