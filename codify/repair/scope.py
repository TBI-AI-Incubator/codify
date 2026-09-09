"""Which elements a repair plan is allowed to touch.

Ingested document text is attacker-influenced and sits in the agent's context, so what
matters is not what the model can be told but what the applier accepts. The rest of
`apply_plan` gates the plan's shape; this gates its reach.
"""

from __future__ import annotations

from typing import Any

from codify.repair.ops import Merge, RenumberSequence, RepairOp

_SEP = "__"


class UnreadableOp(ValueError):
    """An op whose edit targets this module cannot determine."""


def _related(target: str, named: str, *, allow_ancestor: bool) -> bool:
    """A named eId or one of its descendants, and upward only when asked.

    Upward is not free. eIds are fully hierarchical, so every container between the flagged
    element and the root is an ancestor: a finding two levels inside a chapter would
    license deleting the chapter, this gate's own injection one level up. Only
    `renumber_sequence` needs it, the element owning a numbering run being the parent while
    the finding sits on a child.
    """
    if target == named or target.startswith(named + _SEP):
        return True
    return allow_ancestor and named.startswith(target + _SEP)


def permitted_eids(finding: dict[str, Any] | None) -> frozenset[str]:
    """Every eId the finding itself names: the flagged element, the other
    elements it implicates (`eids`), and the parent it scopes to."""
    if not finding:
        return frozenset()
    named = {str(finding.get("eid") or ""), str(finding.get("scope") or "")}
    raw = finding.get("eids")
    if isinstance(raw, (list, tuple)):
        named.update(str(e) for e in raw)
    return frozenset(n for n in named if n)


def edited_eids(op: RepairOp) -> tuple[str, ...]:
    """The elements an op changes.

    A move's `new_parent_eid` is excluded, being where the element lands rather than
    something the op rewrites, and a destination is outside the finding's scope by
    definition. Raises for an op whose targets cannot be read: a plan whose reach cannot be
    computed should not apply, and a new op class naming its target something other than
    `eid` must not pass by accident.
    """
    if isinstance(op, Merge):
        return (op.eid_a, op.eid_b)
    if isinstance(op, RenumberSequence):
        return (op.parent_eid,)
    eid = getattr(op, "eid", None)
    if not isinstance(eid, str) or not eid:
        raise UnreadableOp(f"{op.op} names no element this gate can read")
    return (eid,)


def out_of_scope(ops: list[RepairOp], finding: dict[str, Any] | None) -> str | None:
    """The reason the plan reaches outside its finding, or None.

    `None` means no scope to enforce, which is the ad-hoc callers that pass no
    finding. A finding that is present but names nothing is refused instead.
    """
    if finding is None:
        return None
    named = permitted_eids(finding)
    if not named:
        # A finding that names nothing cannot bound anything. Refusing is the
        # only safe reading: `finding is None` is the ad-hoc unscoped path, and
        # a supplied finding must not disable the gate by being empty.
        return "the finding names no element, so no plan can be scoped against it"
    for op in ops:
        upward = isinstance(op, RenumberSequence)
        if isinstance(op, RenumberSequence):
            # Upward reach without this escalates: a duplicate point inside a chapter
            # would license renumbering every article in it, clearing the finding and
            # passing every other gate. The renumbered children must be the kind the
            # finding is about.
            wanted = str(finding.get("kind") or "")
            if wanted and op.kind != wanted:
                return (
                    f"renumber_sequence renumbers {op.kind!r} children, but the "
                    f"finding is about {wanted!r}"
                )
        try:
            targets = edited_eids(op)
        except UnreadableOp as exc:
            return str(exc)
        for target in targets:
            if any(_related(target, n, allow_ancestor=upward) for n in named):
                continue
            return (
                f"{op.op} targets {target!r}, which the finding does not name "
                f"(permitted: {', '.join(sorted(named))})"
            )
    return None
