"""Targeted repair for provisions that failed the deterministic audit: numeric-slot
loss, stray sentinels, header bleed. One LLM call per provision sees the
sentinel-encoded source, the prior attempt marked where sentinels went missing and
the issue list, and is told to fix only those. Two passes, then a give-up flag.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from codify.core.llm import LLMClient
from codify.translate.anchors import SourceUnit
from codify.translate.numeric_extract import (
    NumericManifest,
    decode_sentinels,
    encode_sentinels,
    filter_real_missing,
    find_stray_sentinels,
)

logger = structlog.get_logger()

_PROMPTS = Path(__file__).parent / "prompts"
REPAIR_SYSTEM_PROMPT = (_PROMPTS / "translate_body_repair_system.txt").read_text()

MAX_REPAIR_ITERATIONS = 3


class RepairIssue(BaseModel):
    """One deterministic finding to fix in a repair pass."""

    code: str  # e.g. "sentinel_missing", "stray_sentinel", "header_bleed"
    detail: str  # human-readable detail (e.g. "⟨N005⟩ must appear")


class RepairedBlock(BaseModel):
    """Structured LLM response for the repair pass."""

    eid: str
    heading: str | None = None
    lines: list[str] = Field(default_factory=list)


@dataclass
class RepairAttempt:
    """One repair-pass result on a provision."""

    eid: str
    outcome_lines: list[str]
    outcome_heading: str | None
    remaining_missing: list[str]
    remaining_strays: list[str]


def _build_issues(
    manifest: NumericManifest, missing_ids: list[str], strays: list[str]
) -> list[RepairIssue]:
    """Assemble the per-issue list handed to the LLM: one entry per dropped
    sentinel that must be restored, then one entry per unique stray."""
    by_id = {t.sentinel_id: t for t in manifest.tokens}
    issues: list[RepairIssue] = []
    for mid in missing_ids:
        tok = by_id.get(mid)
        if tok is None:
            continue
        issues.append(
            RepairIssue(
                code="sentinel_missing",
                detail=(
                    f"Sentinel {manifest.sentinel_for(tok)} must appear in your "
                    f"output. It stands for {tok.kind!r} token with source surface "
                    f"{tok.surface!r} (decoded target surface: "
                    f"{tok.expected_target_surface!r})."
                ),
            )
        )
    for s in sorted(set(strays)):
        issues.append(
            RepairIssue(
                code="stray_sentinel",
                detail=(
                    f"Remove the stray sentinel {s} from your output — the "
                    f"source did not contain this marker."
                ),
            )
        )
    return issues


def _build_repair_prompt(
    unit: SourceUnit,
    manifest: NumericManifest,
    prior_translation: list[str],
    prior_heading: str | None,
    target_language: str,
    issues: list[RepairIssue],
) -> str:
    """Assemble the user prompt for a repair pass."""
    encoded_source = encode_sentinels(unit.body_text or "", manifest)
    payload = {
        "eid": unit.akn_eid,
        "kind": unit.kind,
        "heading_source": unit.heading,
        "body_source_with_sentinels": encoded_source,
        "prior_translation": {
            "heading": prior_heading,
            "lines": prior_translation,
        },
        "issues_to_fix": [issue.model_dump() for issue in issues],
    }
    return (
        f"Target language: {target_language}\n\n"
        f"Repair the translation of this single provision. Fix ONLY the "
        f"issues listed under `issues_to_fix`. Keep everything else "
        f"identical to `prior_translation`.\n\n"
        f"Provision:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


async def repair_provision(
    unit: SourceUnit,
    manifest: NumericManifest,
    prior_lines: list[str],
    prior_heading: str | None,
    *,
    target_language: str,
    llm: LLMClient,
    missing_sentinels: list[str],
    stray_sentinels: list[str],
    extra_issues: list[RepairIssue] | None = None,
) -> RepairAttempt:
    """Run one repair pass on a provision. Returns the corrected translation plus a
    diagnosis of any remaining audit failures. ``extra_issues`` lets the judge phase
    inject semantic findings (``judge_coverage``, ``judge_fidelity``) alongside the
    sentinel-driven items; the repair prompt sees them as one list.
    """
    issues = _build_issues(manifest, missing_sentinels, stray_sentinels)
    if extra_issues:
        issues.extend(extra_issues)
    prompt = _build_repair_prompt(
        unit, manifest, prior_lines, prior_heading, target_language, issues
    )
    try:
        response = await llm.chat_schema(prompt, RepairedBlock, system=REPAIR_SYSTEM_PROMPT)
    except Exception as exc:
        logger.warning(
            "translate_repair_failed",
            eid=unit.akn_eid,
            error=f"{type(exc).__name__}: {str(exc)[:160]}",
        )
        return RepairAttempt(
            eid=unit.akn_eid,
            outcome_lines=prior_lines,
            outcome_heading=prior_heading,
            remaining_missing=missing_sentinels,
            remaining_strays=stray_sentinels,
        )

    # Decode after the check: `filter_real_missing` needs the RAW output, since
    # decoded text has every marker replaced by its target surface and so reports
    # all of them missing.
    raw_joined = "\n".join(response.lines)
    decoded_lines: list[str] = []
    candidate_missing: list[str] = []
    remaining_strays: list[str] = []
    for line in response.lines:
        decoded, missing = decode_sentinels(line, manifest, target_language=target_language)
        decoded_lines.append(decoded)
        candidate_missing.extend(missing)
        remaining_strays.extend(find_stray_sentinels(decoded))

    return RepairAttempt(
        eid=unit.akn_eid,
        outcome_lines=decoded_lines,
        outcome_heading=response.heading,
        remaining_missing=filter_real_missing(manifest, candidate_missing, raw_joined),
        remaining_strays=remaining_strays,
    )


__all__ = [
    "MAX_REPAIR_ITERATIONS",
    "REPAIR_SYSTEM_PROMPT",
    "RepairAttempt",
    "RepairIssue",
    "RepairedBlock",
    "repair_provision",
]
