"""What a statute's provisions were, on a date.

Pure functions over amendment effects: no database, no I/O. Nothing is removed,
because publishers retain a repealed provision and mark it. An undated effect is
counted, never placed. What cannot be applied is counted by cause.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

StateKind = Literal["in_force", "not_yet_in_force", "repealed", "text_unapplied", "affected"]

# Actions that end a provision, start one, and change its words. Anything else
# is out of scope rather than half-applied.
# Only `repeal`: the action vocabulary the column is constrained to has no
# revocation or omission, so naming them here would be dead configuration.
REPEAL_ACTIONS = frozenset({"repeal"})
TEXT_ACTIONS = frozenset({"substitution", "insertion"})
FORCE_ACTIONS = frozenset({"entryIntoForce"})


class Effect(Protocol):
    """The shape this module needs, which the effect row already has.

    A Protocol, not the row class: callers include experiments that never touch
    the database.
    """

    # Read-only: this module never writes, and a mutable attribute on a
    # Protocol is invariant, so the row's narrower types would not match.
    @property
    def target_akn_wid(self) -> str | None: ...

    @property
    def akn_action(self) -> str: ...

    @property
    def in_force_date(self) -> date | None: ...

    @property
    def applied(self) -> bool | None: ...


@dataclass(frozen=True)
class ProvisionState:
    kind: StateKind
    date: date | None = None
    #: The AKN action, for `affected`. Named rather than interpreted: the reader
    #: shows the word the record used.
    action: str | None = None


@dataclass
class HoleCounts:
    """Effects the reading could not apply, by cause. Kept apart: undated and
    unreadable are different problems."""

    undated: int = 0
    unapplied_by_publisher: int = 0
    needs_text: int = 0
    no_target: int = 0
    out_of_scope: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return (
            self.undated
            + self.unapplied_by_publisher
            + self.needs_text
            + self.no_target
            + sum(self.out_of_scope.values())
        )


@dataclass
class Timeline:
    states: dict[str, ProvisionState]
    holes: HoleCounts
    change_dates: list[date]
    """Every date this act changed, ascending."""
    insertions_unrenderable: int
    """Provisions added by later acts whose wording lives in the amending act,
    so this reading cannot show them."""


def change_dates(effects: Sequence[Effect]) -> list[date]:
    """The days something happened, ascending and deduplicated. An undated
    effect contributes nothing."""
    return sorted({e.in_force_date for e in effects if e.in_force_date is not None})


def state_at(effects: Sequence[Effect], as_at: date) -> Timeline:
    """Each provision's state on `as_at`, and everything that did not apply."""
    states: dict[str, ProvisionState] = {}
    holes = HoleCounts()
    insertions = 0

    # Date order, so a provision repealed after being amended ends repealed.
    # Date order, undated last. A stable sort keeps the caller's order for
    # same-day ties, and the storage helper fixes that by row id.
    ordered = sorted(effects, key=lambda e: (e.in_force_date is None, e.in_force_date or date.min))

    # Commencement is decided per provision after the rest, from all of its
    # commencements at once. Deciding it effect by effect let a later dormancy
    # unseat an earlier repeal and let an undated effect place itself.
    commencements: dict[str, list[date | None]] = {}

    for effect in ordered:
        eid = effect.target_akn_wid
        when = effect.in_force_date

        if effect.akn_action in FORCE_ACTIONS:
            if not eid:
                if when is None or when <= as_at:
                    holes.no_target += 1
                continue
            if effect.applied is False:
                if when is None or when <= as_at:
                    holes.unapplied_by_publisher += 1
                continue
            if when is None:
                holes.undated += 1
            commencements.setdefault(eid, []).append(when)
            continue

        if when is not None and when > as_at:
            continue  # Has not happened yet on this date: not a hole.
        if not eid:
            holes.no_target += 1
            continue
        if when is None:
            holes.undated += 1
            continue
        if effect.applied is False:
            holes.unapplied_by_publisher += 1
            continue

        if effect.akn_action in REPEAL_ACTIONS:
            states[eid] = ProvisionState("repealed", when)
        elif effect.akn_action in TEXT_ACTIONS:
            holes.needs_text += 1
            if effect.akn_action == "insertion":
                insertions += 1
            # A repeal outranks an unshown amendment, whichever is dated later.
            if states.get(eid, ProvisionState("in_force")).kind != "repealed":
                states[eid] = ProvisionState("text_unapplied", when)
        else:
            holes.out_of_scope[effect.akn_action] += 1
            # Something happened to this provision that we did not apply. It is
            # marked with the action's own name, so nothing touched reads as
            # untouched, and it never displaces a state that says more.
            if eid not in states:
                states[eid] = ProvisionState("affected", when, effect.akn_action)

    for eid, dates in commencements.items():
        current = states.get(eid)
        if current is not None and current.kind == "repealed":
            continue  # Repealed outranks any commencement: it is gone.
        live = [d for d in dates if d is not None and d <= as_at]
        if live:
            # In force, and it stays whatever a later commencement says. Any
            # other state says more than "in force" and is left alone.
            if current is None:
                states[eid] = ProvisionState("in_force", max(live))
            continue
        # Dormant outranks affected as well: not in force at all says more.
        future = [d for d in dates if d is not None]
        states[eid] = ProvisionState("not_yet_in_force", min(future) if future else None)

    return Timeline(
        states=states,
        holes=holes,
        change_dates=change_dates(effects),
        insertions_unrenderable=insertions,
    )
