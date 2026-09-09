"""Translate anchored body units while preserving source eIds.

Silent-degrade paths increment counters and emit structured flags so fallback
text cannot be mistaken for a successful translation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from codify.core.llm import LLMClient, is_connection_class_error
from codify.pipeline.enrich.scaffold import BodyBlock, BodyFillResponse
from codify.translate.anchors import SourceUnit
from codify.translate.batching import group_by_chapter
from codify.translate.exemplars import ExemplarPool
from codify.translate.numeric_extract import (
    NumericManifest,
    decode_sentinels,
    encode_sentinels,
    extract_tokens,
    filter_real_missing,
    find_stray_sentinels,
)

logger = structlog.get_logger()

_PROMPTS = Path(__file__).parent / "prompts"
BODY_FILL_PROMPT = (_PROMPTS / "translate_body_system.txt").read_text()

DEFAULT_BATCH_SIZE = 8


class TranslatedBlock(BaseModel):
    eid: str
    heading: str | None = None
    lines: list[str] = Field(default_factory=list)


class TranslatedBatchResponse(BaseModel):
    blocks: list[TranslatedBlock] = Field(default_factory=list)


@dataclass
class BodyFillOutcome:
    response: BodyFillResponse
    flags: list[dict[str, str]] = field(default_factory=list)
    bodies_fallback_to_source: int = 0
    headings_fallback_to_source: int = 0
    # Sentinel round-trip stats aggregated across all provisions in this
    # run. Downstream audit uses these to compute numeric-slot recall.
    sentinels_expected: int = 0
    sentinels_missing: int = 0
    stray_sentinels: int = 0
    # Per-eId map: eId -> list of sentinel IDs that failed to round-trip.
    # Fed into the repair loop so it knows exactly which numbers to fix.
    sentinels_missing_by_eid: dict[str, list[str]] = field(default_factory=dict)
    # Per-eId list of the actual sentinel markers (`⟨N001⟩` etc.) that
    # survived decoding. The persist-step safety raise names the eids so
    # an operator can go straight to the offending provision.
    stray_sentinels_by_eid: dict[str, list[str]] = field(default_factory=dict)
    # Per-eId numeric manifest so the repair loop can drive targeted fixes
    # without re-extracting tokens.
    manifests_by_eid: dict[str, NumericManifest] = field(default_factory=dict)
    # Exemplar-pool activity across all chapters.
    exemplar_picks: int = 0
    exemplar_scans: int = 0
    exemplar_pool_active: bool = False


def _serialise_batch_for_prompt(
    batch: list[SourceUnit], manifests: dict[str, NumericManifest]
) -> str:
    """Serialise a batch to JSON for the LLM prompt. Each unit's ``body``
    is sentinel-encoded from the corresponding manifest so numeric tokens
    are protected during translation."""
    payload = []
    for u in batch:
        body_source = u.body_text or ""
        manifest = manifests[u.akn_eid]
        encoded_body = encode_sentinels(body_source, manifest)
        payload.append(
            {
                "eid": u.akn_eid,
                "kind": u.kind,
                "heading": u.heading,
                "body": encoded_body,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_batch_manifests(
    batch: list[SourceUnit], *, start_id: int
) -> tuple[dict[str, NumericManifest], int]:
    """Extract numeric tokens for each unit in a batch. Returns
    ``(eid -> manifest, next_start_id)``. IDs are globally unique across
    the batch so a stray-sentinel sweep can attribute cleanly."""
    manifests: dict[str, NumericManifest] = {}
    for u in batch:
        m = extract_tokens(u.body_text or "", start_id=start_id)
        manifests[u.akn_eid] = m
        start_id += len(m.tokens)
    return manifests, start_id


_UNTRANSLATED_MARKER = "[UNTRANSLATED]"


def _fallback_block(u: SourceUnit) -> TranslatedBlock:
    # Mark each line untranslated rather than copying source verbatim, so an
    # omitted or batch-failed unit enters the repair loop instead of shipping
    # source the body-alien gate rejects. Under-returns already route this way.
    n = len(u.body_text.splitlines()) if u.body_text else 0
    return TranslatedBlock(
        eid=u.akn_eid,
        heading=u.heading,
        lines=[_UNTRANSLATED_MARKER] * n,
    )


def normalise_line_count(llm_lines: list[str], source_lines: list[str]) -> tuple[list[str], int]:
    """Clamp LLM output to the source line count, returning `(lines, backfilled)`, the
    count of trailing `[UNTRANSLATED]` sentinels appended on an under-return. Deficit
    was historically padded with verbatim source, silent residue no audit could
    count; the sentinel is visible and countable.
    """
    target = len(source_lines)
    actual = len(llm_lines)
    if target == 0 or actual == 0 or actual == target:
        return llm_lines, 0
    if actual > target:
        head = llm_lines[: target - 1]
        tail = " ".join(line.strip() for line in llm_lines[target - 1 :] if line.strip())
        return [*head, tail], 0
    deficit = target - actual
    return [*llm_lines, *[_UNTRANSLATED_MARKER] * deficit], deficit


async def translate_bodies(
    units: list[SourceUnit],
    *,
    target_language: str,
    notes_block: str,
    llm: LLMClient,
    concurrency: int = 25,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: Callable[[int, int, str | None], None] | None = None,
    exemplar_pool: ExemplarPool | None = None,
) -> BodyFillOutcome:
    flags: list[dict[str, str]] = []
    bodies_fallback = 0
    headings_fallback = 0
    sentinels_expected = 0
    sentinels_missing = 0
    stray_sentinels = 0
    sentinels_missing_by_eid: dict[str, list[str]] = {}
    stray_sentinels_by_eid: dict[str, list[str]] = {}

    if not units:
        return BodyFillOutcome(response=BodyFillResponse(bodies=[]))

    # Chapter-locality grouping: each chapter's batches iterate serially so
    # exemplars from batch N of a chapter feed batch N+1 of the same
    # chapter; chapters race the outer semaphore for wall-clock parallelism.
    groups = group_by_chapter(units, batch_size)
    all_batches: list[list[SourceUnit]] = [b for g in groups for b in g.batches]
    semaphore = asyncio.Semaphore(concurrency)
    done_count = 0
    total = len(all_batches)

    # Extract numeric manifests once per batch, up front, so IDs are
    # allocated in reading order across the whole document.
    global_id_counter = 0
    batch_manifests: dict[int, dict[str, NumericManifest]] = {}
    manifests_by_eid: dict[str, NumericManifest] = {}
    for idx, b in enumerate(all_batches):
        m, global_id_counter = _build_batch_manifests(b, start_id=global_id_counter)
        batch_manifests[idx] = m
        manifests_by_eid.update(m)
        for eid_m in m.values():
            sentinels_expected += len(eid_m.tokens)

    async def _one(
        idx: int,
        batch: list[SourceUnit],
        pool: ExemplarPool | None,
    ) -> tuple[list[TranslatedBlock], bool]:
        """Translate one batch. Returns ``(blocks, llm_failed)`` so the
        chapter loop can gate exemplar-pool feed on a real LLM response
        (a fallback batch echoes source and must not seed the pool)."""
        nonlocal done_count, bodies_fallback, headings_fallback
        nonlocal sentinels_missing, stray_sentinels
        manifests = batch_manifests[idx]
        async with semaphore:
            exemplar_block = pool.render_block() if pool is not None else ""
            preamble = f"{exemplar_block}\n\n" if exemplar_block else ""
            user_prompt = (
                f"{notes_block}\n\n{preamble}Target language: {target_language}\n\n"
                f"Translate every entry in this batch. Return one TranslatedBlock per "
                f"source eid, in the same order. Translate `heading` (if non-null) and "
                f"`body` text only; never invent or drop entries. Sentinel markers "
                f"of the form ⟨N###⟩ inside body text must be copied verbatim into "
                f"your output.\n\n"
                f"Batch:\n{_serialise_batch_for_prompt(batch, manifests)}"
            )
            try:
                response = await llm.chat_schema(
                    user_prompt,
                    TranslatedBatchResponse,
                    system=BODY_FILL_PROMPT,
                )
            except Exception as exc:
                # A gateway outage would degrade every unit to `[UNTRANSLATED]`
                # source text while the run still reported `succeeded`. Fail
                # instead; per-unit fallback stays for genuine model refusals.
                if is_connection_class_error(exc):
                    raise
                logger.warning(
                    "translate_batch_failed",
                    batch=idx,
                    units=len(batch),
                    error=f"{type(exc).__name__}: {str(exc)[:160]}",
                )
                for u in batch:
                    flags.append(
                        {
                            "location": u.akn_eid,
                            "issue": "untranslated (LLM batch failed); source text retained",
                            "code": "translate_batch_fallback",
                        }
                    )
                bodies_fallback += len(batch)
                return [_fallback_block(u) for u in batch], True

        done_count += 1
        if on_progress is not None:
            label = batch[0].akn_eid if batch else None
            on_progress(done_count, total, label)

        by_eid = {b.eid: b for b in response.blocks}
        out: list[TranslatedBlock] = []
        for u in batch:
            block = by_eid.get(u.akn_eid)
            if block is None:
                logger.warning("translate_block_missing", eid=u.akn_eid, batch=idx)
                flags.append(
                    {
                        "location": u.akn_eid,
                        "issue": "untranslated; LLM omitted this unit; source text retained",
                        "code": "translate_block_missing",
                    }
                )
                bodies_fallback += 1
                out.append(_fallback_block(u))
                continue

            heading = block.heading
            if heading is None and u.heading:
                logger.info("translate_heading_null_fallback", eid=u.akn_eid)
                flags.append(
                    {
                        "location": u.akn_eid,
                        "issue": "translation returned null heading; source heading retained",
                        "code": "translate_heading_fallback",
                    }
                )
                headings_fallback += 1
                heading = u.heading

            lines = block.lines
            if not lines and u.body_text:
                flags.append(
                    {
                        "location": u.akn_eid,
                        "issue": "translation returned empty body; source text retained",
                        "code": "translate_body_empty",
                    }
                )
                bodies_fallback += 1
                lines = u.body_text.splitlines()
            elif u.body_text:
                source_lines = u.body_text.splitlines()
                if len(lines) != len(source_lines):
                    logger.info(
                        "translate_line_count_normalised",
                        eid=u.akn_eid,
                        llm=len(lines),
                        source=len(source_lines),
                    )
                    lines, backfilled = normalise_line_count(lines, source_lines)
                    if backfilled > 0:
                        # Sentinel-padded deficits count toward fallback ratio.
                        bodies_fallback += backfilled
                        flags.append(
                            {
                                "location": u.akn_eid,
                                "issue": (
                                    f"LLM returned {len(lines) - backfilled}/"
                                    f"{len(source_lines)} lines; "
                                    f"{backfilled} back-filled with [UNTRANSLATED]"
                                ),
                                "code": "translate_body_line_backfill",
                            }
                        )

            # Decode sentinels and track missing IDs per eId for targeted repair.
            manifest = manifests[u.akn_eid]
            if manifest.tokens:
                # Inspect raw output before substitution so reordered markers
                # remain distinguishable from markers absent altogether.
                raw_joined = "\n".join(lines)
                decoded_lines: list[str] = []
                candidate_missing: list[str] = []
                for line in lines:
                    decoded, missing = decode_sentinels(
                        line, manifest, target_language=target_language
                    )
                    decoded_lines.append(decoded)
                    candidate_missing.extend(missing)
                real_missing = filter_real_missing(manifest, candidate_missing, raw_joined)
                if real_missing:
                    sentinels_missing_by_eid[u.akn_eid] = real_missing
                    sentinels_missing += len(real_missing)
                    flags.append(
                        {
                            "location": u.akn_eid,
                            "issue": (
                                f"numeric-token sentinel(s) not preserved: "
                                f"{', '.join(real_missing)}"
                            ),
                            "code": "translate_sentinel_missing",
                        }
                    )
                lines = decoded_lines

            # Sweep after decoding so stray markers inherited from another unit
            # are counted once, including units with no source numeric tokens.
            strays = [s for line in lines for s in find_stray_sentinels(line)]
            if strays:
                stray_sentinels += len(strays)
                unique_strays = sorted(set(strays))
                stray_sentinels_by_eid[u.akn_eid] = unique_strays
                flags.append(
                    {
                        "location": u.akn_eid,
                        "issue": f"stray sentinel(s) in output: {', '.join(unique_strays)}",
                        "code": "translate_sentinel_stray",
                    }
                )

            out.append(TranslatedBlock(eid=u.akn_eid, heading=heading, lines=lines))
        return out, False

    # Each chapter forks a fresh `ExemplarPool`, so a bad exemplar cannot cross;
    # batches run serially within one so N feeds N+1, while the outer gather races
    # the semaphore across them. Fallback batches feed nothing: an LLM failure
    # emits `[UNTRANSLATED]`, and pooling that teaches later batches to skip work.
    pool_picks_total = 0
    pool_scans_total = 0

    def _fresh_pool() -> ExemplarPool | None:
        if exemplar_pool is None:
            return None
        return ExemplarPool(
            target_language=exemplar_pool.target_language,
            source_language=exemplar_pool.source_language,
            capacity=exemplar_pool.capacity,
        )

    # Precompute a stable batch index per (group, position) so the
    # per-chapter loop can index into batch_manifests without an id() dict.
    group_batch_offsets: list[int] = []
    running = 0
    for g in groups:
        group_batch_offsets.append(running)
        running += len(g.batches)

    async def _one_chapter(
        group_idx: int, group_batches: list[list[SourceUnit]]
    ) -> list[list[TranslatedBlock]]:
        nonlocal pool_picks_total, pool_scans_total
        pool = _fresh_pool()
        out: list[list[TranslatedBlock]] = []
        base = group_batch_offsets[group_idx]
        for offset, batch in enumerate(group_batches):
            idx = base + offset
            blocks, llm_failed = await _one(idx, batch, pool)
            out.append(blocks)
            if pool is not None and not llm_failed:
                pool.pick_from(
                    [{"eid": u.akn_eid, "body": u.body_text or ""} for u in batch],
                    [{"eid": b.eid, "lines": b.lines} for b in blocks],
                )
        if pool is not None:
            pool_picks_total += pool.picks
            pool_scans_total += pool.scans
        return out

    results_by_group = await asyncio.gather(
        *[_one_chapter(i, g.batches) for i, g in enumerate(groups)]
    )
    results = [batch_result for group in results_by_group for batch_result in group]

    # `BodyBlock.model_construct` skips the ingest-side strict validator;
    # `TranslatedBlock` ran its own translation-tuned check above.
    body_blocks = [
        BodyBlock.model_construct(eid=tb.eid, heading=tb.heading, lines=tb.lines)
        for batch_result in results
        for tb in batch_result
    ]
    return BodyFillOutcome(
        response=BodyFillResponse(bodies=body_blocks),
        flags=flags,
        bodies_fallback_to_source=bodies_fallback,
        headings_fallback_to_source=headings_fallback,
        exemplar_picks=pool_picks_total,
        exemplar_scans=pool_scans_total,
        exemplar_pool_active=(exemplar_pool is not None and exemplar_pool.active),
        sentinels_expected=sentinels_expected,
        sentinels_missing=sentinels_missing,
        stray_sentinels=stray_sentinels,
        sentinels_missing_by_eid=sentinels_missing_by_eid,
        stray_sentinels_by_eid=stray_sentinels_by_eid,
        manifests_by_eid=manifests_by_eid,
    )


__all__ = [
    "BODY_FILL_PROMPT",
    "BodyFillOutcome",
    "DEFAULT_BATCH_SIZE",
    "TranslatedBatchResponse",
    "TranslatedBlock",
    "translate_bodies",
]
