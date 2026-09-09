"""Phase 1, translation notes: the binding terminology and style sheet. The act is
read whole before any section is translated, so terminology is fixed up front,
which is EU and UN practice. The schema rejects blank entries so a malformed
response cannot coerce to an empty term list; every call returns a `NotesOutcome`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel

from codify.core.llm import LLMClient

# Callback for streamed partial notes, receives lists of terms found so far.
NotesPartial = Callable[[dict[str, Any]], None]

logger = structlog.get_logger()

_PROMPTS = Path(__file__).parent / "prompts"
NOTES_SYSTEM = (_PROMPTS / "translation_notes_system.txt").read_text()


NotesStatus = Literal["complete", "partial", "failed"]


class _DefinedTerm(BaseModel):
    source: str = ""
    target: str = ""
    note: str = ""


class _TermOfArt(BaseModel):
    source: str = ""
    target: str = ""
    rationale: str = ""
    false_friend_warning: str = ""


class _NamedEntity(BaseModel):
    source: str = ""
    target: str = ""
    note: str = ""


class _Ambiguity(BaseModel):
    location: str = ""
    issue: str = ""
    options: str = ""


class TranslationNotesSchema(BaseModel):
    """Loose schema on the LLM boundary: individual entries with blank
    ``source``/``target`` are accepted here and filtered downstream in
    ``_filter_blank_entries`` so one bad entry cannot evict a whole
    slab's worth of good ones."""

    target_language: str = ""
    defined_terms: list[_DefinedTerm]
    terms_of_art: list[_TermOfArt]
    named_entities: list[_NamedEntity]
    deontic_conventions: str = ""
    structural_conventions: str = ""
    ambiguities: list[_Ambiguity]


@dataclass
class NotesOutcome:
    """One notes-call result. `status` distinguishes a clean pass, a response the schema
    rejected, and a call that never returned. `notes` carries the merged dict callers
    consume, on `failed` the empty-defaults skeleton so body-fill keeps running.
    `error` is a short exception snippet for the flag.
    """

    notes: dict[str, Any]
    status: NotesStatus
    error: str | None = None


# Above this, the notes call is slabbed and the term lists merged, one
# whole-act call would overrun the model on million-char codes.
NOTES_MAX_CHARS = 350_000

_LIST_KEYS = ("defined_terms", "terms_of_art", "named_entities", "ambiguities")
_TERM_KEYS = ("defined_terms", "terms_of_art", "named_entities")


async def build_translation_notes(
    bluebell: str, *, target_language: str, llm: LLMClient, on_partial: NotesPartial | None = None
) -> NotesOutcome:
    """Produce the notes for an act, slabbing large documents and merging the per-slab
    term lists; `on_partial` receives them as they stream. Returns the merged
    `NotesOutcome`: `complete` iff every slab completed, `failed` iff every slab
    failed, `partial` otherwise.
    """
    if len(bluebell) <= NOTES_MAX_CHARS:
        return await _notes_call(
            bluebell, target_language=target_language, llm=llm, on_partial=on_partial
        )

    slabs = [bluebell[i : i + NOTES_MAX_CHARS] for i in range(0, len(bluebell), NOTES_MAX_CHARS)]
    logger.info("translation_notes_slabbed", slabs=len(slabs), chars=len(bluebell))
    outcomes = [
        await _notes_call(s, target_language=target_language, llm=llm, on_partial=on_partial)
        for s in slabs
    ]
    merged_notes = _merge_notes([o.notes for o in outcomes], target_language=target_language)
    status = _fold_status([o.status for o in outcomes])
    first_err = next((o.error for o in outcomes if o.error), None)
    return NotesOutcome(notes=merged_notes, status=status, error=first_err)


def _partial_terms(partial: object) -> dict[str, Any]:
    """Pull whatever term lists are complete so far out of a streamed partial."""
    data = partial.model_dump() if hasattr(partial, "model_dump") else partial
    if not isinstance(data, dict):
        return {}
    return {k: [t for t in (data.get(k) or []) if t] for k in _TERM_KEYS}


async def _notes_call(
    bluebell: str, *, target_language: str, llm: LLMClient, on_partial: NotesPartial | None = None
) -> NotesOutcome:
    """Build notes for one act or slab. `complete` filters blank entries individually, so
    one malformed entry never evicts a populated slab and `entries_dropped` counts them;
    `partial` when entries dropped or the dict is empty against source content; `failed`
    when no validated shape returned. `ValueError` is what `chat_schema` re-raises on
    `ValidationError`, so catching it is deliberate.
    """
    prompt = f"Target language: {target_language}\n\nThe act, in Bluebell format:\n\n{bluebell}"
    forward = (lambda p: on_partial(_partial_terms(p))) if on_partial else None
    try:
        parsed = await llm.chat_schema_stream(
            prompt, TranslationNotesSchema, on_partial=forward, system=NOTES_SYSTEM
        )
    except (TimeoutError, ValueError) as exc:
        # ValueError: LLM client re-raises schema/parse failures as this.
        # TimeoutError: async gateway timeouts.
        logger.warning("translation_notes_failed", error=str(exc)[:200])
        return NotesOutcome(
            notes=_empty_notes(target_language), status="failed", error=str(exc)[:200]
        )

    raw = parsed.model_dump()
    filtered, dropped = _filter_blank_entries(raw)
    if not filtered.get("target_language"):
        filtered["target_language"] = target_language
    filtered["entries_dropped"] = dropped
    status: NotesStatus = "partial" if dropped else "complete"
    return NotesOutcome(notes=filtered, status=status)


def _filter_blank_entries(notes: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Remove list entries with blank ``source``/``target`` (or
    ``location``/``issue`` on ambiguities) without evicting the whole
    slab. Returns ``(filtered_notes, dropped_count)``."""
    out = dict(notes)
    dropped = 0
    for key in _TERM_KEYS:
        entries = out.get(key) or []
        kept = [
            e
            for e in entries
            if (e.get("source") or "").strip() and (e.get("target") or "").strip()
        ]
        dropped += len(entries) - len(kept)
        out[key] = kept
    amb = out.get("ambiguities") or []
    kept_amb = [
        e for e in amb if (e.get("location") or "").strip() and (e.get("issue") or "").strip()
    ]
    dropped += len(amb) - len(kept_amb)
    out["ambiguities"] = kept_amb
    return out, dropped


def _empty_notes(target_language: str) -> dict[str, Any]:
    """Skeleton notes dict for degrade paths, same shape as a clean pass so
    downstream callers do not branch on outcome."""
    return {
        "target_language": target_language,
        "defined_terms": [],
        "terms_of_art": [],
        "named_entities": [],
        "deontic_conventions": "",
        "structural_conventions": "",
        "ambiguities": [],
        "entries_dropped": 0,
    }


def _fold_status(statuses: list[NotesStatus]) -> NotesStatus:
    """Fold per-slab statuses. All complete → complete; all failed →
    failed; anything mixed → partial."""
    if not statuses:
        return "failed"
    if all(s == "complete" for s in statuses):
        return "complete"
    if all(s == "failed" for s in statuses):
        return "failed"
    return "partial"


def _merge_notes(partials: list[dict[str, Any]], *, target_language: str) -> dict[str, Any]:
    """Merge per-slab notes: union the term lists (first occurrence wins on
    the source term), keep the first non-empty prose convention."""
    merged: dict[str, Any] = {"target_language": target_language}
    for key in _LIST_KEYS:
        seen: set[str] = set()
        items: list[Any] = []
        for part in partials:
            for entry in part.get(key, []):
                src = entry.get("source") or entry.get("location") or ""
                if src and src in seen:
                    continue
                seen.add(src)
                items.append(entry)
        merged[key] = items
    for key in ("deontic_conventions", "structural_conventions"):
        merged[key] = next((p[key] for p in partials if p.get(key)), "")
    return merged


def notes_to_prompt(notes: dict[str, Any]) -> str:
    """Render the notes as the context block prepended to each section call."""
    lines = [f"TRANSLATION NOTES (binding). Target language: {notes['target_language']}", ""]
    if notes.get("defined_terms"):
        lines.append("Defined terms (use the target verbatim, everywhere, exclusively):")
        for t in notes["defined_terms"]:
            lines.append(
                f"  - {t.get('source', '')} → {t.get('target', '')}  [{t.get('note', '')}]"
            )
    if notes.get("terms_of_art"):
        lines.append("Terms of art:")
        for t in notes["terms_of_art"]:
            warn = t.get("false_friend_warning") or ""
            warn = f"  AVOID: {warn}" if warn else ""
            lines.append(f"  - {t.get('source', '')} → {t.get('target', '')}{warn}")
    if notes.get("named_entities"):
        lines.append("Named entities:")
        for e in notes["named_entities"]:
            lines.append(
                f"  - {e.get('source', '')} → {e.get('target', '')}  [{e.get('note', '')}]"
            )
    if notes.get("deontic_conventions"):
        lines.append(f"Deontic conventions: {notes['deontic_conventions']}")
    if notes.get("structural_conventions"):
        lines.append(f"Structural conventions: {notes['structural_conventions']}")
    return "\n".join(lines)


__all__ = [
    "NOTES_MAX_CHARS",
    "NotesOutcome",
    "NotesStatus",
    "TranslationNotesSchema",
    "build_translation_notes",
    "notes_to_prompt",
]
