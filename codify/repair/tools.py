"""Read-only, evidence-bounded tools for the repair agent."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import regex
import structlog
from lxml import etree
from pydantic_ai import Agent, BinaryContent, RunContext

from codify.akn.structure_diff import structure_of
from codify.pipeline.enrich.ocr import PageSpan
from codify.repair.deps import RepairDeps
from codify.repair.edit_ops import find_by_eid, validate_body_bluebell
from codify.repair.grounding import offset_to_page, spans_from_json
from codify.repair.ops import EditPlan
from codify.repair.sandbox import own_text
from codify.repair.sandbox import preview_plan as sandbox_preview

if TYPE_CHECKING:
    from codify.repair.evidence import EvidenceStore

logger = structlog.get_logger()

_MAX_HITS = 20
_MAX_OUTLINE = 400
_MAX_FINDINGS = 40
_MAX_PATTERN = 200
# Wall-clock one search tool call gets. The pattern comes from the model.
_SEARCH_BUDGET_S = 2.0


def _timed_out(pattern: str) -> str:
    logger.warning("repair_search_timed_out", pattern=pattern[:200])
    return (
        f"search gave up after {_SEARCH_BUDGET_S:g}s; the pattern is too slow. "
        "Try a simpler one: a literal substring, or anchored alternation "
        "without nested quantifiers."
    )


def _capped(lines: list[str], cap: int) -> str:
    if len(lines) > cap:
        return "\n".join(lines[:cap]) + f"\n... {len(lines) - cap} more suppressed"
    return "\n".join(lines)


def _compile(pattern: str) -> regex.Pattern[str] | str:
    if len(pattern) > _MAX_PATTERN:
        return f"pattern longer than {_MAX_PATTERN} chars"
    try:
        return regex.compile(pattern, regex.IGNORECASE)
    except regex.error as exc:
        return f"bad pattern: {exc}"


def _matcher(rx: regex.Pattern[str]) -> Callable[[str], bool]:
    """One budget shared across every candidate. Raises TimeoutError once spent."""
    deadline = time.monotonic() + _SEARCH_BUDGET_S

    def match(text: str) -> bool:
        remaining = deadline - time.monotonic()
        # Raise when the budget is gone rather than granting a fresh sliver: a
        # floor turns one budget into one-per-candidate, and a gazette has tens
        # of thousands of lines.
        if remaining <= 0:
            raise TimeoutError("search budget exhausted")
        return bool(rx.search(text, timeout=remaining))

    return match


def _page_span(deps: RepairDeps, page: int) -> PageSpan | None:
    return next((s for s in spans_from_json(deps.spans) if s.page == page), None)


def _known_pages(deps: RepairDeps) -> list[int]:
    return sorted(set(deps.page_nos) or {s.page for s in spans_from_json(deps.spans)})


def _subtree(deps: RepairDeps, eid: str) -> str:
    if not deps.akn_xml:
        return "no document workspace available"
    el = find_by_eid(etree.fromstring(deps.akn_xml.encode("utf-8")), eid)
    if el is None:
        return f"eId {eid!r} not found"
    return str(etree.tostring(el, encoding="unicode"))


def register_tools(agent: Agent[RepairDeps, EditPlan], store: EvidenceStore) -> None:
    @agent.tool
    def show_subtree(ctx: RunContext[RepairDeps], eid: str = "") -> str:
        """The AKN fragment for an element, the flagged one when eid is omitted."""
        if not eid or eid == ctx.deps.eid:
            return ctx.deps.subtree_xml
        return _subtree(ctx.deps, eid)

    @agent.tool
    def source_text(ctx: RunContext[RepairDeps]) -> str:
        """The OCR'd text of the flagged element's source page."""
        return ctx.deps.source_text

    @agent.tool
    def show_siblings(ctx: RunContext[RepairDeps], eid: str = "") -> str:
        """A container with ALL its child units and their numbers, the flagged
        element's parent when eid is omitted. Use for duplicate_number to see
        the whole collapsed-numbering run before renumber_sequence."""
        if eid:
            return _subtree(ctx.deps, eid)
        if not ctx.deps.akn_xml:
            return "no parent context available"
        el = find_by_eid(etree.fromstring(ctx.deps.akn_xml.encode("utf-8")), ctx.deps.eid)
        parent = el.getparent() if el is not None else None
        if parent is None:
            return "no parent context available"
        return str(etree.tostring(parent, encoding="unicode"))

    @agent.tool
    def show_outline(ctx: RunContext[RepairDeps]) -> str:
        """The document skeleton: every provision's eId, kind, number and heading."""
        nodes = structure_of(ctx.deps.akn_xml) if ctx.deps.akn_xml else None
        if not nodes:
            return "no document workspace available"
        depth: dict[str | None, int] = {None: -1}
        lines = []
        for n in nodes:
            depth[n.eid] = depth.get(n.parent_eid, 0) + 1
            label = " ".join(x for x in (n.tag, n.num or "", n.heading or "") if x)
            lines.append(f"{'  ' * depth[n.eid]}{n.eid}: {label}")
        return _capped(lines, _MAX_OUTLINE)

    @agent.tool
    def search_akn(ctx: RunContext[RepairDeps], pattern: str) -> str:
        """Search the document's text (regex, case-insensitive). Returns eId + line."""
        if not ctx.deps.akn_xml:
            return "no document workspace available"
        rx = _compile(pattern)
        if isinstance(rx, str):
            return rx
        root = etree.fromstring(ctx.deps.akn_xml.encode("utf-8"))
        match = _matcher(rx)
        hits = []
        try:
            for el in root.iter():
                if not isinstance(el.tag, str) or not el.get("eId"):
                    continue
                own = own_text(el)
                if match(own):
                    hits.append(f"{el.get('eId')}: {own[:200]}")
        except TimeoutError:
            return _timed_out(pattern)
        return _capped(hits, _MAX_HITS) or "no matches"

    @agent.tool
    def search_source(ctx: RunContext[RepairDeps], pattern: str) -> str:
        """Search the full OCR'd source text (regex). Returns page + matching line."""
        if not ctx.deps.doc_text:
            return "no source text available"
        rx = _compile(pattern)
        if isinstance(rx, str):
            return rx
        spans = spans_from_json(ctx.deps.spans)
        match = _matcher(rx)
        hits = []
        offset = 0
        try:
            for line in ctx.deps.doc_text.splitlines(keepends=True):
                if match(line):
                    page = offset_to_page(spans, offset)
                    hits.append(f"p{page if page is not None else '?'}: {line.strip()[:200]}")
                offset += len(line)
        except TimeoutError:
            return _timed_out(pattern)
        return _capped(hits, _MAX_HITS) or "no matches"

    @agent.tool
    def read_page(ctx: RunContext[RepairDeps], page: int) -> str:
        """The OCR'd text of one source page."""
        span = _page_span(ctx.deps, page)
        if span is None:
            return f"page {page} not in evidence; pages: {_known_pages(ctx.deps)}"
        return ctx.deps.doc_text[span.start : span.end]

    @agent.tool
    async def compare_reads(ctx: RunContext[RepairDeps], page: int) -> str:
        """Both engines' reads of one page, with divergence, the read verdict and
        the page's layout blocks (index + type, for view_region). Use when the
        primary OCR text looks wrong, the rival read may be better."""
        try:
            read = await store.page_read(ctx.deps.version_id, page)
        except Exception as exc:  # noqa: BLE001, a store outage must not kill the run
            logger.warning("repair_page_read_failed", page=page, error=str(exc))
            return f"page read temporarily unavailable ({type(exc).__name__})"
        if read is None:
            return f"no retained read for page {page}"
        metrics = read.get("metrics") or {}
        head = (
            f"engine={read.get('engine')} model={read.get('model')} "
            f"divergence={read.get('divergence')} verdict={metrics.get('verdict', '')} "
            f"reasons={metrics.get('reasons', [])}"
        )
        blocks = ((read.get("layout") or {}).get("blocks") or [])[:40]
        if blocks:
            listing = "; ".join(
                f"[{i}] {b.get('type', '?')}: {str(b.get('content', ''))[:60]!r}"
                for i, b in enumerate(blocks)
            )
            head += f"\nblocks: {listing}"
        rival = str(read.get("rival_text") or "").strip()
        return (
            f"{head}\n--- primary read ---\n{read.get('text', '')}"
            f"\n--- rival read ---\n{rival or '(no rival read)'}"
        )

    @agent.tool
    async def view_source_page(
        ctx: RunContext[RepairDeps], page: int | None = None
    ) -> BinaryContent | str:
        """The rendered scan of a source page (the flagged element's page when
        omitted), read the original when the OCR text is garbled or missing."""
        page_no = page if page is not None else ctx.deps.page_no
        if page_no is None or not ctx.deps.object_key:
            return "no source page is mapped for this element; use source_text"
        # Bounded by the dossier's page set (which includes empty-text reads:
        # a page whose OCR failed still has its scan as evidence), not by the
        # span table alone.
        known = _known_pages(ctx.deps)
        if known and page_no not in known:
            return f"page {page_no} not in evidence; pages: {known}"
        try:
            png = await store.render_page(ctx.deps.object_key, page_no)
        except Exception as exc:  # noqa: BLE001, an unavailable scan must not fail the run
            logger.warning(
                "repair_render_failed",
                object_key=ctx.deps.object_key,
                page=page_no,
                error=str(exc),
            )
            return f"source page unavailable ({type(exc).__name__}); use source_text"
        return BinaryContent(data=png, media_type="image/png")

    @agent.tool
    async def view_region(
        ctx: RunContext[RepairDeps], page: int, block_index: int
    ) -> BinaryContent | str:
        """One layout block of a page, cropped from a high-resolution re-render.
        Use compare_reads first to see which blocks exist."""
        if not ctx.deps.object_key or not ctx.deps.version_id:
            return "no source document available"
        try:
            png = await store.render_region(
                ctx.deps.version_id, ctx.deps.object_key, page, block_index
            )
        except ValueError as exc:  # the model's mistake: bad index, no layout
            return f"region unavailable ({exc})"
        except Exception as exc:  # noqa: BLE001, infra failure, not a missing region
            logger.warning(
                "repair_render_failed",
                object_key=ctx.deps.object_key,
                page=page,
                error=str(exc),
            )
            return f"region temporarily unavailable ({type(exc).__name__})"
        return BinaryContent(data=png, media_type="image/png")

    @agent.tool
    def list_findings(ctx: RunContext[RepairDeps], eid: str = "") -> str:
        """Every validation finding on the document, optionally filtered to eIds
        containing the given fragment. Use to see what a change might interact with."""
        lines = []
        for f in ctx.deps.findings:
            f_eid = str(f.get("eid") or ",".join(f.get("eids") or []))
            if eid and eid not in f_eid:
                continue
            lines.append(f"{f.get('check')} [{f.get('severity')}] {f_eid}: {f.get('message', '')}")
        return _capped(lines, _MAX_FINDINGS) or "no findings"

    @agent.tool
    def check_bluebell(ctx: RunContext[RepairDeps], bluebell: str) -> str:
        """Validate a proposed Bluebell body before committing. Returns 'ok' or
        the reason. Uses the exact checks _set_body applies, so an 'ok' can't be
        rejected differently at apply."""
        err = validate_body_bluebell(
            ctx.deps.subtree_xml, bluebell, country=ctx.deps.country, doctype=ctx.deps.doctype
        )
        return "ok" if not err else f"error: {err}"

    @agent.tool
    def preview_plan(ctx: RunContext[RepairDeps], plan_json: str) -> str:
        """Test a candidate plan against a copy of the document. Pass the SAME
        JSON string you will give submit_plan: {"ops": [...], "reasoning":
        "..."}. Returns the acceptance verdict, rejection reasons and the
        semantic diff. Preview once; when accepted, call submit_plan.

        A string argument by design: the discriminated op union cannot ride a
        tool schema through the gateway (the same constraint that makes the
        final output a plan_json string on submit_plan)."""
        if not ctx.deps.akn_xml:
            return "no document workspace available"
        # Re-previewing an accepted plan is the observed loop; short-circuit it
        # with fresh wording (an identical reply would feed the repetition rut).
        if plan_json == ctx.deps.preview_state.get("accepted_plan_json"):
            return (
                "already accepted; call submit_plan with this JSON now — "
                "further previews only waste your budget"
            )
        try:
            plan = EditPlan.model_validate_json(plan_json)
        except Exception as exc:  # noqa: BLE001, a bad payload is feedback, not a crash
            return f"could not parse the plan: {exc}"
        if not plan.ops:
            ctx.deps.preview_state["accepted_plan_json"] = plan_json
            return 'abstention accepted — call submit_plan with {"ops": []} now'
        try:
            report = sandbox_preview(
                ctx.deps.akn_xml,
                plan,
                country=ctx.deps.country,
                doctype=ctx.deps.doctype,
                target_finding=ctx.deps.finding,
                source_text=ctx.deps.evidence_window(),
                evidence=ctx.deps.source_evidence(),
                source_text_mismatch=ctx.deps.source_text_mismatch,
            )
        except Exception as exc:  # noqa: BLE001, the tool reports, never raises
            logger.warning("repair_preview_failed", eid=ctx.deps.eid, error=repr(exc))
            return f"preview failed ({type(exc).__name__}: {exc}); revise or abstain"
        lines = [f"verdict: {'accepted' if report.ok else 'rejected'}"]
        if report.reasons:
            lines.append("reasons: " + report.reason_text())
        lines += [f"structure: {c}" for c in report.structure_changes[:10]]
        lines += [
            f"text: {d.eid} {d.before_chars}->{d.after_chars} chars"
            + (f" grounding={d.grounding:.2f}" if d.grounding is not None else "")
            for d in report.text_deltas[:10]
        ]
        lines.append(f"findings: {report.findings_before} -> {report.findings_after}")
        if report.ok:
            ctx.deps.preview_state["accepted_plan_json"] = plan_json
            lines.append(
                "accepted — now call submit_plan with this exact JSON. Do not preview again."
            )
        return "\n".join(lines)
