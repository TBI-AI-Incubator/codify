"""Orchestrate dossier findings, agent proposals and transactional application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from lxml import etree
from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded

from codify.pipeline.enrich.validator import validate_akn
from codify.repair.deps import RepairDeps
from codify.repair.edit_ops import ACTIONABLE_CHECKS, find_by_eid
from codify.repair.grounding import eid_to_page, spans_from_json
from codify.repair.ops import EditPlan
from codify.repair.sandbox import preview_plan

logger = structlog.get_logger()

AgentRun = Callable[[RepairDeps], Awaitable[Any]]  # (deps) -> EditPlan
ProgressHook = Callable[[int, int, dict[str, Any]], Awaitable[None]]  # (done, total, finding)
SettledHook = Callable[[int, dict[str, Any], str | None], Awaitable[None]]  # (i, outcome, risk)


@dataclass
class RepairOutcome:
    akn: str
    before: int
    after: int
    findings: list[dict[str, Any]] = field(default_factory=list)
    # One entry per plan the agent produced: the auditable proposal the
    # workflow persists (status applied for low/medium, pending for high).
    proposals: list[dict[str, Any]] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        return self.after < self.before

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            d = str(f.get("disposition", "unknown"))
            out[d] = out.get(d, 0) + 1
        return out


def _target_eid(finding: dict[str, Any]) -> str | None:
    eid = finding.get("eid")
    if isinstance(eid, str):
        return eid
    eids = finding.get("eids")
    if isinstance(eids, list) and eids:
        return str(eids[0])
    return None


def _is_budget_exhaustion(exc: Exception) -> bool:
    return isinstance(exc, UnexpectedModelBehavior) and "retries" in str(exc).lower()


def _salvage(deps: RepairDeps, exc: Exception) -> EditPlan | None:
    """A budget death (usage or revision) after an accepted preview adopts the
    proven plan, the sandbox re-runs on it below, so every gate still binds.
    An acceptance cannot go stale mid-finding (deterministic sandbox, immutable
    snapshot), and the revision budget also burns on text-only turns and
    malformed submissions, so exhaustion is no evidence against the recorded
    plan. Deliberately the LAST accepted preview, even when later previews
    explored elsewhere. Non-budget failures salvage nothing."""
    if not (isinstance(exc, UsageLimitExceeded) or _is_budget_exhaustion(exc)):
        return None
    accepted = deps.preview_state.get("accepted_plan_json")
    if not isinstance(accepted, str):
        return None
    try:
        return EditPlan.model_validate_json(accepted)
    except ValidationError as exc:
        # A recorded plan that no longer parses is an invariant violation;
        # discarding it without a trace is the failure salvage exists to fix.
        logger.warning("repair_salvage_unparseable", eid=deps.eid, error=str(exc))
        return None


async def repair_document(
    akn: str,
    findings: list[dict[str, Any]],
    *,
    agent_run: AgentRun,
    country: str,
    object_key: str,
    doctype: str = "act",
    page_text: str = "",
    raw_spans: list[dict[str, Any]] | None = None,
    version_id: str = "",
    eid_page: dict[str, int] | None = None,
    rival_text_by_page: dict[int, str] | None = None,
    page_nos: list[int] | None = None,
    source_sha256: str = "",
    source_text_mismatch: bool = False,
    closing_phrases: list[str] | None = None,
    on_progress: ProgressHook | None = None,
    on_settled: SettledHook | None = None,
) -> RepairOutcome:
    spans = spans_from_json(raw_spans)
    if eid_page is None:
        eid_page = (
            eid_to_page(page_text, spans, country=country, doctype=doctype) if page_text else {}
        )
    page_text_by_no = {s.page: page_text[s.start : s.end] for s in spans}
    rival_by_no = rival_text_by_page or {}

    phrases = list(closing_phrases or [])
    before = len(validate_akn(akn, closing_phrases=phrases))
    current = akn
    outcomes: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []

    async def settle(i: int, entry: dict[str, Any], risk: str | None = None) -> None:
        # Exactly one settle per finding; the hook rides the append.
        outcomes.append(entry)
        if on_settled is not None:
            await on_settled(i, entry, risk)

    for i, finding in enumerate(findings):
        check = str(finding.get("check", ""))
        eid = _target_eid(finding)
        if on_progress is not None:
            await on_progress(i, len(findings), finding)
        if check not in ACTIONABLE_CHECKS or eid is None:
            await settle(i, {"eid": eid, "check": check, "disposition": "skipped_non_actionable"})
            continue
        if check == "number_gap" and finding.get("likely") == "defect":
            # A defect-class gap is a structurer/OCR miss upstream, not a
            # repeal to annotate; sending it to the agent burns the whole
            # revision budget on a repair that would be a false claim.
            await settle(i, {"eid": eid, "check": check, "disposition": "skipped_non_actionable"})
            continue
        el = find_by_eid(etree.fromstring(current.encode("utf-8")), eid)
        if el is None:  # an earlier edit dissolved this eid
            await settle(i, {"eid": eid, "check": check, "disposition": "eid_gone"})
            continue
        page_no = eid_page.get(eid)
        deps = RepairDeps(
            finding=finding,
            eid=eid,
            subtree_xml=etree.tostring(el, encoding="unicode"),
            source_text=page_text_by_no.get(page_no if page_no is not None else -1, ""),
            page_no=page_no,
            object_key=object_key,
            country=country,
            doctype=doctype,
            version_id=version_id,
            akn_xml=current,
            doc_text=page_text,
            rival_text=rival_by_no.get(page_no if page_no is not None else -1, ""),
            spans=list(raw_spans or []),
            findings=findings,
            page_nos=list(page_nos or []),
            source_sha256=source_sha256,
            source_text_mismatch=source_text_mismatch,
            closing_phrases=list(closing_phrases or []),
        )
        salvaged = False
        try:
            plan = await agent_run(deps)
        except Exception as exc:  # noqa: BLE001, one bad finding must not lose good edits
            rescued = _salvage(deps, exc)
            if rescued is None:
                if _is_budget_exhaustion(exc):
                    disposition = "revision_budget_exhausted"
                elif isinstance(exc, UsageLimitExceeded):
                    disposition = "investigation_budget_exhausted"
                else:
                    disposition = "errored"
                # exc_info retains the full chain: the retry wrapper's message
                # alone is exactly what hid the gateway failure from the evaluator.
                logger.warning(
                    "repair_agent_error", eid=eid, check=check, error=repr(exc), exc_info=True
                )
                await settle(
                    i, {"eid": eid, "check": check, "disposition": disposition, "error": str(exc)}
                )
                continue
            plan, salvaged = rescued, True
            logger.warning("repair_budget_salvage", eid=eid, check=check, error=repr(exc))
        extra = {"salvaged": True} if salvaged else {}
        if not plan.ops:
            await settle(i, {"eid": eid, "check": check, "disposition": "empty_plan", **extra})
            continue

        # One sandbox pass is both the atomic apply and the audit record: the
        # agent's output validator already accepted this plan against the same
        # snapshot, so a rejection here is unexpected, not routine.
        report = preview_plan(
            current,
            plan,
            country=country,
            doctype=doctype,
            target_finding=finding,
            source_text=deps.evidence_window(),
            evidence=deps.source_evidence(),
            source_text_mismatch=deps.source_text_mismatch,
        )
        proposal = {
            "eid": eid,
            "check": check,
            "finding": finding,
            "ops": [op.model_dump() for op in plan.ops],
            "reasoning": plan.reasoning,
            "risk": report.risk,
            "audit": {**report.audit(), **extra},
        }
        if not report.ok:
            # The validator accepted this plan against the same inputs, so a
            # loop-stage rejection means state moved underneath it, unexpected.
            logger.warning("repair_ops_rejected", eid=eid, check=check, reasons=report.reasons)
            await settle(
                i,
                {
                    "eid": eid,
                    "check": check,
                    "disposition": "ops_rejected",
                    "reasons": report.reasons,
                    **extra,
                },
                report.risk,
            )
            continue
        if report.risk == "high":
            # Consequence beyond machine authority: queue, touch nothing.
            proposal["status"] = "pending"
            proposals.append(proposal)
            await settle(
                i,
                {"eid": eid, "check": check, "disposition": "queued_for_approval", **extra},
                "high",
            )
            continue
        current = report.xml
        proposal["status"] = "applied"
        proposals.append(proposal)
        await settle(
            i,
            {"eid": eid, "check": check, "disposition": "cleared", "reasons": [], **extra},
            report.risk,
        )

    return RepairOutcome(
        akn=current,
        before=before,
        after=len(validate_akn(current, closing_phrases=phrases)),
        findings=outcomes,
        proposals=proposals,
    )
