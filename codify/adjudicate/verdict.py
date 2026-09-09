"""What an adjudicator may say about one ambiguous span.

The output is closed by construction. The model picks a kind the jurisdiction
declares or says there is no anchor here; it never proposes free-form structure,
so a bad verdict is a wrong choice from a known set rather than invented text.
The choice then re-enters the same invariant gate as everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field, model_validator

# What the scanner could not settle, and the adjudicator is asked to.
ADJUDICABLE_REASONS: frozenset[str] = frozenset(
    {
        # A marker-shaped line no anchor claimed: a mangled keyword, or prose
        # that looks like a heading. The scanner cannot tell those apart.
        "marker_shaped_line_unclaimed",
        # A marker whose keyword matched but whose hierarchy level did not.
        "no_kind_resolved",
    }
)


@dataclass(frozen=True)
class AdjudicationRequest:
    """One span, with everything needed to read it and nothing more.

    Deliberately small and serialisable: no AKN, no PDF bytes, no full document.
    The page render is fetched through an injected boundary when the model asks
    for it, so a request can be replayed offline from a fixture.
    """

    line: str
    reason: str
    candidate_kinds: tuple[str, ...]
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    # eIds of the nearest anchors either side, so the model can see what a new
    # anchor here would sit between.
    preceding_eid: str = ""
    following_eid: str = ""
    page_no: int | None = None
    object_key: str = ""
    country: str = ""
    doctype: str = "act"
    detail: dict[str, object] = field(default_factory=dict)


class Adjudication(BaseModel):
    """The verdict. `kind` None means the line is not a structural marker."""

    kind: str | None = Field(
        None, description="One of the offered candidate kinds, or null for no anchor here."
    )
    number: str | None = Field(
        None, description="The unit's number exactly as printed, or null when it has none."
    )
    reason: str = Field(
        ...,
        min_length=8,
        description="Why, in one sentence, citing what on the page decided it.",
    )

    @model_validator(mode="after")
    def _number_needs_a_kind(self) -> Adjudication:
        if self.kind is None and self.number is not None:
            raise ValueError("a number without a kind is not a reading; set kind or clear number")
        return self


def validate_choice(verdict: Adjudication, request: AdjudicationRequest) -> Adjudication:
    """Reject a kind outside the offered set.

    The closed set is the whole guarantee. A model that answers `subsection` for
    a jurisdiction whose class declares no such level has invented a hierarchy,
    and the invariant gate downstream would accept it because the shape is
    valid. This is the only place that can tell.
    """
    if verdict.kind is not None and verdict.kind not in request.candidate_kinds:
        offered = ", ".join(request.candidate_kinds) or "(none)"
        raise ValueError(f"kind {verdict.kind!r} was not offered; choose from: {offered}, or null")
    return verdict


__all__ = [
    "ADJUDICABLE_REASONS",
    "Adjudication",
    "AdjudicationRequest",
    "validate_choice",
]
