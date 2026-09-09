"""Preview a repair plan with transactional, semantic and grounding checks."""

from __future__ import annotations

from lxml import etree
from pydantic import BaseModel, Field

from codify.akn import AKN_NS
from codify.akn.eids import ensure_unique_eids
from codify.akn.structure_diff import diff_structure
from codify.akn.vocabulary import provision_text
from codify.pipeline.enrich.regions import overlap, words
from codify.pipeline.enrich.validator import validate_akn
from codify.repair.edit_ops import SourceEvidence, apply_plan
from codify.repair.ops import (
    EditPlan,
    RepairOp,
    RiskClass,
    SetBody,
    plan_risk,
)
from codify.repair.scope import edited_eids

GROUNDING_FLOOR = 0.3


class TextDelta(BaseModel):
    """One element whose text the plan changed, with the full auditable
    surfaces: after an in-place apply the stored version no longer holds the
    before state, so this record is the only place it survives."""

    eid: str
    before_chars: int
    after_chars: int
    before: str = ""
    after: str = ""
    grounding: float | None = None  # set only for text a SetBody introduced


class SandboxReport(BaseModel):
    """Structured acceptance or rejection, plus the diff either way."""

    ok: bool
    xml: str = ""  # the accepted post-image; empty on rejection
    reasons: list[str] = Field(default_factory=list)
    structure_changes: list[str] = Field(default_factory=list)
    text_deltas: list[TextDelta] = Field(default_factory=list)
    findings_before: int = 0
    findings_after: int = 0
    # Consequence class of the plan; "escalated" records that evidence quality
    # (no text grounding, drifted source) raised it beyond the ops' own classes.
    risk: RiskClass = "low"
    escalated: bool = False

    def audit(self) -> dict[str, object]:
        """The proposal row's auditable form: everything but the post-image."""
        return self.model_dump(exclude={"xml"})

    def reason_text(self) -> str:
        return "; ".join(self.reasons)


def own_text(el: etree._Element) -> str:
    """Text belonging to this element proper, descendants with their own eId
    are their own spans at ANY depth (an eId-less wrapper like <content> does
    not adopt its eId-bearing children's text), so a child edit does not
    cascade up the ancestry."""
    return " ".join("".join(_own_parts(el)).split())


def _own_parts(el: etree._Element) -> list[str]:
    parts = [el.text or ""]
    for child in el:
        if isinstance(child.tag, str) and child.get("eId") is None:
            parts.extend(_own_parts(child))
        parts.append(child.tail or "")
    return parts


def _text_map(root: etree._Element) -> dict[str, str]:
    meta = root.find(f".//{{{AKN_NS}}}meta")
    inside_meta = set(meta.iter()) if meta is not None else set()
    return {
        eid: own_text(el)
        for el in root.iter()
        if isinstance(el.tag, str) and (eid := el.get("eId")) and el not in inside_meta
    }


def _meta_bytes(root: etree._Element) -> bytes:
    meta = root.find(f".//{{{AKN_NS}}}meta")
    return etree.tostring(meta) if meta is not None else b""


def _named_eids(ops: list[RepairOp]) -> set[str]:
    """The eIds a plan names. One definition, shared with the scope gate: this
    was a second copy of the same rule with the opposite failure mode."""
    named: set[str] = set()
    for op in ops:
        named.update(edited_eids(op))
    return named


def _touched_eids(root: etree._Element, ops: list[RepairOp]) -> set[str]:
    """Every eId a plan may legitimately alter: the named targets and all their
    descendants."""
    named = _named_eids(ops)
    touched: set[str] = set()
    for eid in named:
        hits = root.xpath(".//*[@eId=$e]", e=eid)
        if not hits:
            continue
        for el in hits[0].iter():
            if isinstance(el.tag, str) and el.get("eId"):
                touched.add(el.get("eId", ""))
    return touched | named


def grounding_score(introduced: str, source_text: str) -> float | None:
    """`regions.overlap` with an abstain: None when either side has no words,
    so absent evidence never reads as a zero score."""
    if not source_text.strip() or not words(introduced):
        return None
    return overlap(introduced, source_text)


def preview_plan(
    current_xml: str,
    plan: EditPlan,
    *,
    country: str,
    doctype: str = "act",
    target_finding: dict[str, object] | None = None,
    source_text: str = "",
    evidence: SourceEvidence | None = None,
    source_text_mismatch: bool = False,
) -> SandboxReport:
    """Apply the plan to a copy and report acceptance with a semantic diff.

    ``source_text`` is the finding's page evidence (own read plus rival read);
    empty means no page evidence, and grounding abstains rather than gates:
    but an ungrounded text-introducing plan escalates to high risk, as does a
    drifted source (``source_text_mismatch``).
    """
    if not plan.ops:
        return SandboxReport(ok=False, reasons=["empty_plan"])

    res = apply_plan(
        current_xml,
        plan.ops,
        country=country,
        doctype=doctype,
        target_finding=target_finding,
        evidence=evidence,
    )
    if not res.ok:
        return SandboxReport(ok=False, reasons=[f"apply_rejected: {res.error}"])

    # Both sides normalised: apply_plan measures on the ensure_unique_eids image,
    # so an un-normalised before would report phantom eId differences.
    base_xml, _ = ensure_unique_eids(current_xml)
    before_root = etree.fromstring(base_xml.encode("utf-8"))
    after_root = etree.fromstring(res.xml.encode("utf-8"))

    reasons: list[str] = []
    if _meta_bytes(before_root) != _meta_bytes(after_root):
        reasons.append("meta_modified")

    before_text = _text_map(before_root)
    after_text = _text_map(after_root)
    touched = _touched_eids(before_root, plan.ops)
    for eid, before in before_text.items():
        after = after_text.get(eid)
        if after is None or after == before:
            continue
        if eid not in touched:
            reasons.append(f"unchanged_span_modified: {eid}")

    # The audit surface: each NAMED target's whole provision text, before and
    # after. Whole-subtree (not own-text) so a filled body's added paragraphs
    # are captured whatever eIds the splice minted for them.
    deltas: list[TextDelta] = []
    for eid in sorted(_named_eids(plan.ops)):
        before_el = before_root.xpath(".//*[@eId=$e]", e=eid)
        after_el = after_root.xpath(".//*[@eId=$e]", e=eid)
        before_full = " ".join(provision_text(before_el[0]).split()) if before_el else ""
        after_full = " ".join(provision_text(after_el[0]).split()) if after_el else ""
        if before_full == after_full:
            continue
        deltas.append(
            TextDelta(
                eid=eid,
                before_chars=len(before_full),
                after_chars=len(after_full),
                before=before_full,
                after=after_full,
            )
        )

    ungrounded = False
    for op in plan.ops:
        if isinstance(op, SetBody):
            score = grounding_score(op.bluebell, source_text)
            for delta in deltas:
                if delta.eid == op.eid:
                    delta.grounding = score
            if score is None:
                ungrounded = True
                continue
            if score < GROUNDING_FLOOR:
                reasons.append(f"insufficient_grounding: {op.eid} scored {score:.2f}")
    # A SetBody on a duplicate_number rewrites VALID text; grounding bars only
    # fabrication, not an unfaithful reword, so queue it for a human.
    rewrites_valid = str((target_finding or {}).get("check", "")) == "duplicate_number" and any(
        isinstance(op, SetBody) for op in plan.ops
    )
    escalated = ungrounded or source_text_mismatch or rewrites_valid

    return SandboxReport(
        ok=not reasons,
        xml=res.xml if not reasons else "",
        reasons=reasons,
        structure_changes=diff_structure(base_xml, res.xml) or [],
        text_deltas=deltas,
        findings_before=len(
            validate_akn(base_xml, closing_phrases=evidence.closing_phrases if evidence else None)
        ),
        findings_after=len(
            validate_akn(res.xml, closing_phrases=evidence.closing_phrases if evidence else None)
        ),
        risk=plan_risk(plan.ops, escalate=escalated),
        escalated=escalated,
    )
