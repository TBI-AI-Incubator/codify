"""What an amendment-effects source has to provide, whoever publishes it.

A publisher records what changed, where, by whom and when. The shapes here are
that record, before any jurisdiction's spelling of it: an adapter turns its own
feed into `FeedEffect`, and the caller turns those into rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FeedEffect:
    """One recorded effect, normalised. `raw_type` is kept whatever happens to
    the crosswalk: a mapping we cannot make is not a fact we may discard.

    The two work fields are FRBR work URIs in our own vocabulary, whatever the
    publisher writes, so an effect's authority joins `laws.frbr_work_uri`.
    """

    publisher_id: str
    affected_work: str | None
    affected_provision: str
    affecting_work: str | None
    affecting_provision: str
    raw_type: str
    akn_action: str | None
    akn_category: str | None
    in_force_date: date | None
    #: Whether the publisher has applied it. None where a publisher records the
    #: effect without saying so, which is not the same as "not applied".
    applied: bool | None
    is_meta: bool


@dataclass
class FeedResult:
    """An adapter's answer for one act, with what it could not map counted.

    `skipped` is by reason and never a single number: an effect we could not
    place and one the publisher has not applied are different facts.
    """

    effects: list[FeedEffect] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def dated(self) -> int:
        return sum(1 for e in self.effects if e.in_force_date is not None)


class FeedTruncated(RuntimeError):
    """The export returned exactly what was asked for, so rows beyond it are
    missing and the feed says nothing about it."""


@runtime_checkable
class EffectsAdapter(Protocol):
    """One publisher's effects feed.

    `fetch` is expected to make more than one request: a publisher may serve the
    effects and their dates separately, and an adapter that returns undated
    effects has not finished.
    """

    jurisdiction_code: str

    async def fetch(self, publisher_path: str) -> FeedResult: ...

    async def fetch_many(self, publisher_paths: list[str]) -> dict[str, FeedResult]:
        """Several acts at once where the publisher can answer that way.

        Optional: this default loops `fetch`. One publisher answers fifty in a
        single query, which is hours against minutes at a polite rate, and that
        difference belongs in the protocol rather than in a caller's loop.
        """
        return {path: await self.fetch(path) for path in publisher_paths}

    def publisher_path_for(self, work_uri: str) -> str | None:
        """The publisher's own key for a work, or None when we cannot form one.

        Publishers key their feeds their own way, so the mapping belongs beside
        the fetch rather than in the caller.
        """
        ...

    def target_eid(self, affected_provision: str) -> str | None:
        """A publisher's reference to the eId it names, or None when it names
        more than one unit. Each publisher writes references its own way.
        """
        ...

    def held_target_eid(self, eid: str, held: set[str]) -> str | None:
        """Resolve a parsed publisher identifier against this expression's units."""
        ...
