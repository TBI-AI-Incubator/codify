"""Typed repair operations, the edit vocabulary an agent may propose.

Plain Pydantic models, no Pydantic AI dependency, so the agent's ``output_type`` and
the deterministic apply layer share one contract. Each op names an eId and an intent;
the model never emits raw AKN, only these ops and Bluebell for bodies.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class SetBody(BaseModel):
    """Replace an element's body with the Bluebell parse of ``bluebell``.

    For an article the model read off the source page but the pipeline left
    empty. Bluebell is plain text (parser-guaranteed valid AKN)."""

    op: Literal["set_body"] = "set_body"
    eid: str
    bluebell: str


class SetNum(BaseModel):
    """Relabel an element's number (a mis-read or duplicated numeral)."""

    op: Literal["set_num"] = "set_num"
    eid: str
    num: str


class Move(BaseModel):
    """Reparent an element under ``new_parent_eid`` (orphan / wrong nesting)."""

    op: Literal["move"] = "move"
    eid: str
    new_parent_eid: str


class Split(BaseModel):
    """Split an element into two siblings at ``marker`` (a swallowed enumerator
    the body-fill merged into the preceding unit)."""

    op: Literal["split"] = "split"
    eid: str
    marker: str


class Merge(BaseModel):
    """Merge ``eid_b`` into ``eid_a`` (an over-split unit)."""

    op: Literal["merge"] = "merge"
    eid_a: str
    eid_b: str


class RenumberSequence(BaseModel):
    """Renumber all of a parent's same-kind children to the given sequence, in document
    order. A run of collapsed numbering (points OCR-read as 1,1,1,1,2,2,2 that should be
    1..7) must be fixed together, since renumbering one colliding group at a time moves
    the collision rather than clearing it.
    """

    op: Literal["renumber_sequence"] = "renumber_sequence"
    parent_eid: str
    kind: str  # the child element kind, e.g. "point", "paragraph", "article"
    nums: list[str]  # the true number for each child, in order, read from the page


class Delete(BaseModel):
    """Remove a phantom element the scan promoted from an in-text reference.
    Guarded: only when the source does not support the unit."""

    op: Literal["delete"] = "delete"
    eid: str


class Annotate(BaseModel):
    """Attach an editorial note (e.g. a confirmed repeal gap) rather than
    fabricate a missing unit."""

    op: Literal["annotate"] = "annotate"
    eid: str
    note: str


class SetMoneyNumeral(BaseModel):
    """Correct one OCR-corrupted money numeral in place, touching nothing else. A scanned
    amount gains or loses a digit while the words beside it stay right ("(٥٠٠,٠٠٠) fifty
    thousand"). Carrying the exact surfaces rather than a replacement body keeps a money
    repair off the prose.
    """

    op: Literal["set_money_numeral"] = "set_money_numeral"
    eid: str
    old: str  # the numeral exactly as it appears, e.g. "٥٠٠,٠٠٠"
    new: str  # the corrected numeral read off the page, e.g. "٥٠,٠٠٠"


class RestoreFromSource(BaseModel):
    """Restore an element's body verbatim from its span of the source text, for a body the
    OCR captured and the structurer dropped. Carries no offsets: the apply layer resolves
    the span from the anchor scan, so the model cannot hallucinate a slice.
    """

    op: Literal["restore_from_source"] = "restore_from_source"
    eid: str


class MoveToConclusions(BaseModel):
    """Lift attestation lines (promulgation, signature) out of the named
    provision into `<conclusions>`, the shape the ingest pass declined."""

    op: Literal["move_to_conclusions"] = "move_to_conclusions"
    eid: str


RepairOp = Annotated[
    Union[
        SetBody,
        SetNum,
        SetMoneyNumeral,
        RenumberSequence,
        Move,
        Split,
        Merge,
        Delete,
        Annotate,
        RestoreFromSource,
        MoveToConclusions,
    ],
    Field(discriminator="op"),
]

RiskClass = Literal["low", "medium", "high"]

# Consequence class per op, per the epic's policy: text-preserving moves and
# editorial annotation auto-apply; source-grounded transcription, splitting and
# renumbering stay visible for review; deletion needs human approval.
RISK_CLASS: dict[str, RiskClass] = {
    "move": "low",
    "move_to_conclusions": "low",
    "annotate": "low",
    "set_body": "medium",
    "set_money_numeral": "medium",
    "set_num": "medium",
    "renumber_sequence": "medium",
    "split": "medium",
    "merge": "medium",
    "restore_from_source": "medium",
    "delete": "high",
}

_RISK_ORDER: dict[RiskClass, int] = {"low": 0, "medium": 1, "high": 2}


def plan_risk(ops: list[RepairOp], *, escalate: bool = False) -> RiskClass:
    """The plan's consequence class: the highest of its ops', raised to high
    when the caller has independent grounds (no text evidence, drifted source)."""
    if escalate:
        return "high"
    if not ops:
        return "low"
    return max((RISK_CLASS[op.op] for op in ops), key=_RISK_ORDER.__getitem__)


class EditPlan(BaseModel):
    """The agent's proposed edits for one finding, applied transactionally."""

    ops: list[RepairOp] = Field(default_factory=list)
    reasoning: str = ""
