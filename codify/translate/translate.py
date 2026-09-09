"""Anchor-driven translation: source AKN, per-eId LLM body-fill, source clone
patched with translated heading and `<p>` text. The LLM never sees structure: it
gets one prose body and heading per source eId and returns text keyed on the same
eId, and `translate/write.py` does the write, so counts match by construction.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from lxml import etree

from codify.akn.bluebell import akn_xml_to_bluebell
from codify.akn.io import parse_akn
from codify.core.llm import LLMClient
from codify.lang import NON_ASCII_DIGIT_CLASS, normalise_digits
from codify.observability import ProgressEvent
from codify.pipeline.enrich.arabic_normalise import fold_arabic_for_match
from codify.translate.anchors import walk_source
from codify.translate.audit import (
    alien_patterns_for,
    audit_translation,
    count_doubled_citation_nouns,
    front_matter_slot_defects,
    front_matter_source_retained,
    text_is_alien_dominated,
)
from codify.translate.notes import build_translation_notes, notes_to_prompt
from codify.translate.notes_audit import run_checks as run_notes_checks
from codify.translate.repair import MAX_REPAIR_ITERATIONS, repair_provision
from codify.translate.translate_bodies import (
    DEFAULT_BATCH_SIZE,
    TranslatedBlock,
    translate_bodies,
)
from codify.translate.write import (
    apply_translation_to_akn,
    conclusions_p_slots,
    preamble_p_slots,
    preface_p_slots,
)

logger = structlog.get_logger()

TRANSLATE_CONCURRENCY = 25

# Below this ratio of translated/source non-whitespace chars, flag the run.
# Catches gross loss (a whole untranslated schedule), not normal variance.
COVERAGE_FLOOR = 0.5


@dataclass
class TranslationResult:
    akn_xml: str
    notes: dict[str, Any]
    audit: dict[str, Any]
    flags: list[dict[str, str]] = field(default_factory=list)


_TITLE_SYSTEM = (
    "You translate the official title of a piece of legislation into the target "
    "language, rendered as that jurisdiction's statute drafters would write it. "
    "Output only the translated title, on a single line, with no quotes, notes or "
    "commentary."
)

_PREFACE_SYSTEM = (
    "You translate the preface of a piece of legislation into the target "
    "language, rendered as that jurisdiction's statute drafters would write it. "
    "The preface is the act's opening matter (long title and cover text). "
    "Return the translated preface as plain text; preserve paragraph breaks with "
    "blank lines. No headings, no quotes, no notes or commentary."
)

_PREAMBLE_SYSTEM = (
    "You translate the preamble of a piece of legislation into the target "
    "language, rendered as that jurisdiction's statute drafters would write it. "
    "The preamble carries the enacting formula and any recitals; keep the "
    "formal enacting register exactly. Return the translated preamble as plain "
    "text; preserve paragraph breaks with blank lines. No headings, no quotes, "
    "no notes or commentary."
)


_CONCLUSIONS_SYSTEM = (
    "You translate the concluding attestation of a piece of legislation into "
    "the target language, rendered as that jurisdiction's statute drafters "
    "would write it. The attestation carries the place of promulgation, the "
    "date or dates, and the signatory's name and office. Translate the place, "
    "the date and the office. Do NOT translate a person's name: transliterate "
    "it into the target script, and leave it as it stands where the scripts "
    "already agree. Return plain text, one line in for one line out. No "
    "headings, no quotes, no notes or commentary."
)


def _strip_code_fences(text: str) -> str:
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _extract_preface_lines(source_akn_xml: str) -> list[str]:
    """One string per preface `<p>` slot (direct `<p>`s + `<longTitle>` `<p>`s);
    empty when absent. Slot order shared with the writer's patcher."""
    try:
        root = etree.fromstring(source_akn_xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []
    return ["".join(p.itertext()).strip() for p in preface_p_slots(root)]


def _extract_conclusions_lines(source_akn_xml: str) -> list[str]:
    root = etree.fromstring(source_akn_xml.encode("utf-8"))
    return ["".join(p.itertext()).strip() for p in conclusions_p_slots(root)]


def _extract_preamble_lines(source_akn_xml: str) -> list[str]:
    """One string per preamble `<p>` slot (enacting formula + recitals);
    empty when absent. Slot order shared with the writer's patcher."""
    try:
        root = etree.fromstring(source_akn_xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []
    return ["".join(p.itertext()).strip() for p in preamble_p_slots(root)]


def _akn_text_content(akn_xml: str) -> str:
    """Concatenated text under `<preface>`, `<preamble>`, `<body>` and
    `<conclusions>`: the language content the audit ratio should measure.
    Excludes `<meta>` and structural markup."""
    try:
        root = etree.fromstring(akn_xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return ""
    parts: list[str] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        name = etree.QName(el).localname
        if name in {"preface", "preamble", "body", "conclusions"}:
            parts.append(" ".join(s.strip() for s in el.itertext() if s.strip()))
    return " ".join(parts)


async def translate_title(title: str, *, target_language: str, llm: LLMClient) -> str:
    title = (title or "").strip()
    if not title:
        return ""
    prompt = f"Target language: {target_language}\n\nTitle to translate:\n{title}"
    out = _strip_code_fences((await llm.chat(prompt, system=_TITLE_SYSTEM)).strip())
    return out.strip().strip('"') or title


_MAX_SLOT_RETRIES = 3


def _slot_translation_ok(source: str, cleaned: str, alien: list[re.Pattern[str]]) -> bool:
    """A retried slot is a real translation only if non-empty, marker-free, plausibly
    sized, not an echo, and not predominantly source script. An exact echo is refused
    for every target, so one with no alien-script table cannot accept it; script is
    judged by majority as the delivery gate judges it, so a proper noun does not revert.
    """
    from codify.translate.translate_bodies import _UNTRANSLATED_MARKER

    if not cleaned or _UNTRANSLATED_MARKER in cleaned:
        return False
    if _length_implausible(source, cleaned):
        return False
    if cleaned == " ".join(source.split()):
        return False
    if alien:
        # Count only source-script *letters*: the alien classes span whole
        # Unicode blocks, so digits and combining marks would otherwise inflate
        # the ratio and reject a short line that only kept source-script numerals.
        letters = sum(1 for c in cleaned if c.isalpha())
        alien_letters = sum(1 for c in cleaned if c.isalpha() and any(p.match(c) for p in alien))
        if letters and alien_letters / letters > 0.5:
            return False
    return True


async def _retry_backfilled_lines(
    lines: list[str],
    source_lines: list[str],
    *,
    location: str,
    system_prompt: str,
    target_language: str,
    llm: LLMClient,
) -> tuple[list[str], int]:
    """Second pass over under-returned slots, retrying each up to `_MAX_SLOT_RETRIES`
    and falling back to the verbatim source line when every attempt fails. A retry
    echoing the source script is refused rather than accepted on length, since
    retained Arabic reaches the reader. Returns `(lines, still_missing)`.
    """
    from codify.translate.audit import alien_patterns_for
    from codify.translate.translate_bodies import _UNTRANSLATED_MARKER

    alien = alien_patterns_for(target_language)
    fallback = 0
    for i, line in enumerate(lines):
        if _UNTRANSLATED_MARKER not in line:
            continue
        chosen = ""
        for attempt in range(_MAX_SLOT_RETRIES):
            # A model that echoed the source once tends to repeat it, so later
            # attempts spell out the requirement rather than just re-sampling.
            firm = (
                ""
                if attempt == 0
                else " Output only the translation, never the source text, no commentary."
            )
            try:
                raw = await llm.chat(
                    f"Target language: {target_language}\n\n"
                    f"Translate this single {location} line into {target_language}.{firm}\n"
                    f"{source_lines[i]}",
                    system=system_prompt,
                )
                cleaned = " ".join(_strip_code_fences(raw.strip()).split())
            except Exception as exc:  # noqa: BLE001, try again, then source below
                # Full traceback: a programming defect here must not read as a
                # transient LLM outage.
                logger.warning(
                    f"translate_{location}_line_retry_failed",
                    index=i,
                    attempt=attempt,
                    error=f"{type(exc).__name__}: {str(exc)[:160]}",
                    exc_info=True,
                )
                continue
            if _slot_translation_ok(source_lines[i], cleaned, alien):
                chosen = cleaned
                break
        if chosen:
            lines[i] = chosen
        else:
            lines[i] = source_lines[i]
            fallback += 1
    return lines, fallback


# A line with no Unicode letter is gazette furniture (stamp digits, footers,
# file numbers) and copies through verbatim: sent to the LLM it invites
# invented boilerplate. [^\W\d_] is a letter in any script, digits are not.
_LINGUISTIC_RUN_RE = re.compile(r"[^\W\d_]")


def _is_furniture_line(line: str) -> bool:
    return _LINGUISTIC_RUN_RE.search(line) is None


# Slots are addressed by key, not position, so a paragraph the model declines
# or merges affects that slot alone. The marker must open a line: unanchored,
# it also matches mid-sentence, truncating that slot and misfiling its tail.
_SLOT_MARK = re.compile(
    rf"^[ \t]*\[\[[ \t]*([0-9{NON_ASCII_DIGIT_CLASS}]+)[ \t]*\]\]", re.MULTILINE
)
# Batched so one truncated response cannot strand a long contents list.
_SLOT_BATCH = 40
# A translation is the same order of magnitude as its source; far outside the band
# is appended commentary or a fragment, both safer retried. The floor makes the
# ratio meaningful: seven Arabic characters become forty English, so short lines are exempt.
_SLOT_LEN_MAX_RATIO = 4.0
_SLOT_LEN_MIN_RATIO = 0.25
_SLOT_LEN_FLOOR = 80


def _slot_prompt(batch: list[tuple[int, str]], *, location: str, target_language: str) -> str:
    body = "\n\n".join(f"[[{i}]] {text}" for i, text in batch)
    return (
        f"Target language: {target_language}\n\n"
        f"Each {location} paragraph below is preceded by a [[n]] marker. Translate "
        f"the text of every paragraph and reproduce its marker exactly, in the same "
        f"form, at the START OF A LINE immediately before its translation. Emit one "
        f"paragraph per marker, separated by a blank line. Never merge two markers' "
        f"text, never omit a marker, never renumber, never add a marker that was not "
        f"given, and write nothing before the first marker or after the last "
        f"paragraph.\n\n"
        f"{body}"
    )


def _parse_slots(raw: str) -> tuple[dict[int, str], int]:
    """Map `[[n]]` markers to their translated text, returning `(by_key, repeated)`.
    Text runs marker to marker, so the mapping survives whatever separator the model
    chose. First occurrence wins and repeats are counted: a repeat means the model
    duplicated rather than translated, and the later copy would overwrite good text.
    """
    out: dict[int, str] = {}
    repeated = 0
    marks = list(_SLOT_MARK.finditer(raw))
    for j, m in enumerate(marks):
        end = marks[j + 1].start() if j + 1 < len(marks) else len(raw)
        key = int(normalise_digits(m.group(1)))
        text = " ".join(raw[m.end() : end].split())
        if not text:
            continue
        if key in out:
            repeated += 1
            continue
        out[key] = text
    return out, repeated


def _length_implausible(source: str, target: str) -> bool:
    """True when a translated slot's length is too far from its source's to be
    a translation of it. Short slots are exempt via the floor, where ratios are
    meaningless."""
    src_len, tgt_len = len(source.strip()), len(target.strip())
    if max(src_len, tgt_len) <= _SLOT_LEN_FLOOR:
        return False
    if not src_len:
        return False
    ratio = tgt_len / src_len
    return ratio > _SLOT_LEN_MAX_RATIO or ratio < _SLOT_LEN_MIN_RATIO


async def _translate_flat_lines(
    source_lines: list[str],
    *,
    location: str,
    system_prompt: str,
    target_language: str,
    llm: LLMClient,
) -> tuple[list[str], list[dict[str, str]]]:
    """Translate a flat block of prose, one line per source slot, `location` naming the
    region in flag codes. Each slot is addressed by a `[[n]]` key the response must
    echo, so a declined slot is retried alone and falls back to its source line, and
    nothing is positioned by arrival order. Furniture never reaches the model.
    """
    if not source_lines:
        return [], []
    furniture_idx = {i for i, line in enumerate(source_lines) if _is_furniture_line(line)}
    indexed = [(i, line) for i, line in enumerate(source_lines) if i not in furniture_idx]
    flags: list[dict[str, str]] = []
    if furniture_idx:
        flags.append(
            {
                "location": location,
                "issue": (
                    f"{len(furniture_idx)} non-linguistic {location} line(s) "
                    "copied verbatim (stamp digits / gazette furniture)"
                ),
                "code": f"translate_{location}_furniture_passthrough",
            }
        )
    if not indexed:
        return list(source_lines), flags
    wanted = {i for i, _ in indexed}
    by_index = dict(indexed)
    translations: dict[int, str] = {}
    call_failures = 0
    batches = 0
    from codify.translate.audit import alien_patterns_for

    stray_keys = 0
    repeated_keys = 0
    implausible = 0
    echoed = 0
    alien = alien_patterns_for(target_language)
    for start in range(0, len(indexed), _SLOT_BATCH):
        batch = indexed[start : start + _SLOT_BATCH]
        batches += 1
        try:
            raw = await llm.chat(
                _slot_prompt(batch, location=location, target_language=target_language),
                system=system_prompt,
            )
        except Exception as exc:
            logger.warning(
                f"translate_{location}_failed",
                error=f"{type(exc).__name__}: {str(exc)[:160]}",
                exc_info=True,
            )
            call_failures += 1
            continue
        # Accept only the keys this batch asked for: a batch renumbering its
        # answers from zero emits keys an earlier batch owns. An answered key
        # keeps its first answer.
        batch_keys = {i for i, _ in batch}
        parsed, repeats = _parse_slots(_strip_code_fences(raw.strip()))
        repeated_keys += repeats
        for key, text in parsed.items():
            if key not in batch_keys:
                stray_keys += 1
                continue
            if _length_implausible(by_index[key], text):
                implausible += 1
                continue
            if not _slot_translation_ok(by_index[key], text, alien):
                # The batch echoed the source for this slot. Leave it
                # unassigned so it takes a real retry rather than shipping
                # untranslated behind a plausible length.
                echoed += 1
                continue
            translations.setdefault(key, text)
    if not translations and not echoed:
        # Nothing usable came back, so the region keeps its source; report the
        # counts, since the operator asks how much is untranslated. Echo
        # rejections are excluded: those slots still have a retry below.
        return list(source_lines), flags + [
            {
                "location": location,
                "issue": (
                    f"{len(wanted)} {location} slot(s) retained source text: "
                    f"{call_failures} of {batches} batch(es) failed and no slot "
                    "was parseable from the rest"
                ),
                "code": f"translate_{location}_fallback",
            }
        ]
    from codify.translate.translate_bodies import _UNTRANSLATED_MARKER

    lines = [
        source_lines[i] if i in furniture_idx else translations.get(i, _UNTRANSLATED_MARKER)
        for i in range(len(source_lines))
    ]
    unmatched = sum(1 for i in wanted if i not in translations)
    parity_empty = 0
    parity_duplicate = 0
    # Scan a snapshot so stamping slot i cannot hide slot i+1's duplication
    # (an LLM collapsing three slots to one line must have both extras
    # retried, not just the first).
    snapshot = list(lines)
    prev_idx: int | None = None
    for i, line in enumerate(snapshot):
        if i in furniture_idx or _UNTRANSLATED_MARKER in line:
            continue
        if not line.strip() and source_lines[i].strip():
            lines[i] = _UNTRANSLATED_MARKER
            parity_empty += 1
        elif (
            prev_idx is not None
            and line.strip()
            and line == snapshot[prev_idx]
            and source_lines[i] != source_lines[prev_idx]
        ):
            lines[i] = _UNTRANSLATED_MARKER
            parity_duplicate += 1
        prev_idx = i
    fallback = 0
    if any(_UNTRANSLATED_MARKER in line for line in lines):
        lines, fallback = await _retry_backfilled_lines(
            lines,
            source_lines,
            location=location,
            system_prompt=system_prompt,
            target_language=target_language,
            llm=llm,
        )
    if unmatched:
        flags.append(
            {
                "location": location,
                "issue": (
                    f"{unmatched} of {len(wanted)} {location} slot(s) came back without "
                    "their marker and were retried individually"
                ),
                "code": f"translate_{location}_slot_unmatched",
            }
        )
    if call_failures:
        flags.append(
            {
                "location": location,
                "issue": (
                    f"{call_failures} of {batches} {location} slot batch(es) failed; "
                    "their slots were retried individually"
                ),
                "code": f"translate_{location}_batch_failed",
            }
        )
    if stray_keys or repeated_keys or implausible:
        flags.append(
            {
                "location": location,
                "issue": (
                    f"{location} response was malformed: {stray_keys} key(s) outside the "
                    f"batch, {repeated_keys} repeated, {implausible} implausibly sized; "
                    "each was refused and its slot retried"
                ),
                "code": f"translate_{location}_slot_malformed",
            }
        )
    if echoed:
        flags.append(
            {
                "location": location,
                "issue": (
                    f"{echoed} {location} slot(s) came back as the untranslated "
                    "source and were retried"
                ),
                "code": f"translate_{location}_slot_echoed",
            }
        )
    if parity_empty or parity_duplicate:
        flags.append(
            {
                "location": location,
                "issue": (
                    f"parity repair retried {parity_empty} empty and "
                    f"{parity_duplicate} duplicated {location} slot(s)"
                ),
                "code": f"translate_{location}_parity_repair",
            }
        )
    if fallback > 0:
        flags.append(
            {
                "location": location,
                "issue": (
                    f"{fallback} {location} line(s) failed translation after "
                    f"{_MAX_SLOT_RETRIES} attempts; source text retained"
                ),
                "code": f"translate_{location}_line_fallback_source",
            }
        )
    echoed = _echo_repeated_lines(source_lines, lines)
    if echoed:
        flags.append(
            {
                "location": location,
                "issue": (
                    f"{echoed} repeated {location} line(s) filled from the earlier "
                    "occurrence of the same source text"
                ),
                "code": f"translate_{location}_repeat_echoed",
            }
        )
    return lines, flags


def _echo_repeated_lines(source_lines: list[str], lines: list[str]) -> int:
    """Fill blank slots whose source text was already translated higher up. A gazette
    repeats its masthead down the front matter and the model returns the repeats
    blank, leaving a gap where the page has a title. The earlier rendering of an
    identical source line is right by construction.
    """
    seen: dict[str, str] = {}
    filled = 0
    for i, src in enumerate(source_lines):
        key = fold_arabic_for_match(src.strip())
        if not key:
            continue
        if lines[i].strip():
            seen.setdefault(key, lines[i])
        elif key in seen:
            lines[i] = seen[key]
            filled += 1
    return filled


async def _translate_preface(
    source_lines: list[str], *, target_language: str, llm: LLMClient
) -> tuple[list[str], list[dict[str, str]]]:
    return await _translate_flat_lines(
        source_lines,
        location="preface",
        system_prompt=_PREFACE_SYSTEM,
        target_language=target_language,
        llm=llm,
    )


async def _translate_conclusions(
    source_lines: list[str], *, target_language: str, llm: LLMClient
) -> tuple[list[str], list[dict[str, str]]]:
    return await _translate_flat_lines(
        source_lines,
        location="conclusions",
        system_prompt=_CONCLUSIONS_SYSTEM,
        target_language=target_language,
        llm=llm,
    )


async def _translate_preamble(
    source_lines: list[str], *, target_language: str, llm: LLMClient
) -> tuple[list[str], list[dict[str, str]]]:
    return await _translate_flat_lines(
        source_lines,
        location="preamble",
        system_prompt=_PREAMBLE_SYSTEM,
        target_language=target_language,
        llm=llm,
    )


async def translate_document(
    source_akn_xml: str,
    *,
    target_language: str,
    llm: LLMClient,
    concurrency: int = TRANSLATE_CONCURRENCY,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_notes: Callable[[dict[str, Any]], None] | None = None,
    on_notes_partial: Callable[[dict[str, Any]], None] | None = None,
    on_section: Callable[[str, str], None] | None = None,
    ocr_header_patterns: list[str] | None = None,
    source_language: str | None = None,
    judge_enabled: bool = False,
    judge_llm: LLMClient | None = None,
    judge_model: str | None = None,
    expression_date: str | None = None,
) -> TranslationResult:
    # Strip gazette running-headers from the source tree where the
    # jurisdiction ships patterns, so the LLM never sees the bleed. The
    # audit's post-translation sweep catches what survives.
    header_strip_count = 0
    if ocr_header_patterns:
        from codify.pipeline.enrich.arabic_normalise import strip_ocr_headers

        root = etree.fromstring(source_akn_xml.encode())
        header_strip_count = strip_ocr_headers(root, ocr_header_patterns)
        if header_strip_count:
            source_akn_xml = etree.tostring(root, encoding="unicode")

    doc = parse_akn(source_akn_xml)
    source_bluebell = akn_xml_to_bluebell(source_akn_xml)
    units, walk_summary = walk_source(doc, source_akn_xml)

    flags: list[dict[str, str]] = []
    if header_strip_count:
        flags.append(
            {
                "location": "document",
                "issue": (
                    f"{header_strip_count} OCR-header pattern match(es) stripped "
                    f"from source before translation"
                ),
                "code": "translate_ocr_headers_stripped",
            }
        )
    if walk_summary.skipped_without_eid:
        flags.append(
            {
                "location": "document",
                "issue": (
                    f"{walk_summary.skipped_without_eid} source element(s) without an "
                    f"eId skipped from translation (kinds: "
                    f"{', '.join(walk_summary.skipped_kinds)})"
                ),
                "code": "translate_skipped_without_eid",
            }
        )

    logger.info(
        "translation_started",
        units=len(units),
        skipped_no_eid=walk_summary.skipped_without_eid,
        chars=len(source_bluebell),
        target=target_language,
    )

    notes_outcome = await build_translation_notes(
        source_bluebell,
        target_language=target_language,
        llm=llm,
        on_partial=on_notes_partial,
    )
    notes = notes_outcome.notes
    notes_status = notes_outcome.status
    if notes_status != "complete":
        flags.append(
            {
                "location": "notes",
                "issue": (
                    f"translation-notes phase degraded to {notes_status}"
                    + (f": {notes_outcome.error}" if notes_outcome.error else "")
                ),
                "code": f"notes_{notes_status}",
            }
        )

    notes_findings, notes_severity = run_notes_checks(
        source_bluebell,
        notes,
        target_language=target_language,
        source_language=source_language,
    )
    if notes_findings:
        flags.extend(notes_findings)
    if notes_severity == "downgrade" and notes_status == "complete":
        # A well-formed response that fails a structural check (definitions
        # clause with no terms, wrong target language) downgrades to partial.
        notes_status = "partial"

    # Attach the notes_status to the dict handed to the caller so the run
    # stream carries the degradation on the same event that already ships
    # the terminology sheet; no separate protocol slot needed.
    notes["notes_status"] = notes_status
    if on_notes is not None:
        on_notes(notes)
    notes_block = notes_to_prompt(notes)

    source_preface_lines = _extract_preface_lines(source_akn_xml)
    preface_lines, preface_flags = await _translate_preface(
        source_preface_lines, target_language=target_language, llm=llm
    )
    flags.extend(preface_flags)

    source_preamble_lines = _extract_preamble_lines(source_akn_xml)
    preamble_lines, preamble_flags = await _translate_preamble(
        source_preamble_lines, target_language=target_language, llm=llm
    )
    flags.extend(preamble_flags)

    source_conclusions_lines = _extract_conclusions_lines(source_akn_xml)
    conclusions_lines, conclusions_flags = await _translate_conclusions(
        source_conclusions_lines, target_language=target_language, llm=llm
    )
    flags.extend(conclusions_flags)

    def _on_batch(done: int, total: int, label: str | None) -> None:
        if on_progress is not None:
            on_progress(
                ProgressEvent(
                    name="translate",
                    done=done,
                    total=total,
                    elapsed_s=0.0,
                    eta_s=None,
                    latest=label,
                ),
            )
        if on_section is not None and label is not None:
            on_section(label, label)

    # Reuse deontic-rich provisions across batches to keep drafting register
    # consistent. The pool is inert when no source pattern is registered.
    from codify.translate.exemplars import ExemplarPool

    exemplar_pool = ExemplarPool(target_language=target_language, source_language=source_language)

    body_outcome = await translate_bodies(
        units,
        target_language=target_language,
        notes_block=notes_block,
        llm=llm,
        concurrency=concurrency,
        batch_size=batch_size,
        on_progress=_on_batch,
        exemplar_pool=exemplar_pool,
    )
    flags.extend(body_outcome.flags)

    blocks = [
        TranslatedBlock(eid=b.eid, heading=b.heading, lines=list(b.lines))
        for b in body_outcome.response.bodies
    ]

    # Route blocks padded with [UNTRANSLATED] markers into targeted repair.
    from codify.translate.repair import RepairIssue
    from codify.translate.translate_bodies import _UNTRANSLATED_MARKER

    # Back-filled markers and source-script lines both route to repair: selecting
    # on markers alone leaves the residue for the delivery audit to find once no
    # retry can run. Uses the audit's own dominance test.
    _alien_patterns = alien_patterns_for(target_language)

    def _alien_line_issues(lines: list[str]) -> list[RepairIssue]:
        # Recomputed from the current lines each pass: a repair that still echoes
        # the source must keep the provision outstanding, not drop after one try.
        if not _alien_patterns:
            return []
        return [
            RepairIssue(
                code="untranslated_line",
                detail=(
                    f"Line index {i} of your output is still in the source script "
                    f"(left untranslated). Translate that line fully into "
                    f"{target_language}."
                ),
            )
            for i, line in enumerate(lines)
            if _UNTRANSLATED_MARKER not in line and text_is_alien_dominated(line, _alien_patterns)
        ]

    # Marker issues are a one-shot repair hint: the terminal sweep restores their
    # source line if repair does not, so they need no persistence across passes.
    _marker_issues_by_eid: dict[str, list[RepairIssue]] = {}
    for _b in blocks:
        marker_issues = [
            RepairIssue(
                code="untranslated_line",
                detail=(
                    f"Line index {i} of your output was back-filled with "
                    f"'{_UNTRANSLATED_MARKER}' because you returned fewer "
                    f"lines than the source has. Translate the corresponding "
                    f"source line at that position; return the full line "
                    f"count matching source."
                ),
            )
            for i, line in enumerate(_b.lines)
            if _UNTRANSLATED_MARKER in line
        ]
        if marker_issues:
            _marker_issues_by_eid[_b.eid] = marker_issues

    # Retry provisions whose sentinels or line markers did not round-trip.
    repair_pass_count = 0
    total_repairs = 0
    outstanding: dict[str, list[str]] = dict(body_outcome.sentinels_missing_by_eid)
    # Keep stray-only eids in the loop so they are not lost from the audit.
    outstanding_strays: dict[str, list[str]] = dict(body_outcome.stray_sentinels_by_eid)
    # Marker-only and source-script-residue eids also need a repair pass.
    for _b in blocks:
        if _b.eid in _marker_issues_by_eid or _alien_line_issues(_b.lines):
            outstanding.setdefault(_b.eid, [])
    if outstanding or outstanding_strays:
        unit_by_eid = {u.akn_eid: u for u in units}
        block_by_eid = {b.eid: b for b in blocks}
        for iteration in range(1, MAX_REPAIR_ITERATIONS + 1):
            if not outstanding and not outstanding_strays:
                break
            repair_pass_count = iteration
            next_outstanding: dict[str, list[str]] = {}
            next_strays: dict[str, list[str]] = {}
            for eid in outstanding.keys() | outstanding_strays.keys():
                unit = unit_by_eid.get(eid)
                block = block_by_eid.get(eid)
                manifest = body_outcome.manifests_by_eid.get(eid)
                if unit is None or block is None or manifest is None:
                    continue
                # Markers hint on iteration 1 only. Source-script residue is
                # recomputed each pass, so a repair that keeps echoing is
                # retried until it clears or the iterations run out.
                marker_issues = _marker_issues_by_eid.get(eid, []) if iteration == 1 else []
                alien_issues = _alien_line_issues(block.lines)
                extra_issues = (marker_issues + alien_issues) or None
                attempt = await repair_provision(
                    unit,
                    manifest,
                    prior_lines=list(block.lines),
                    prior_heading=block.heading,
                    target_language=target_language,
                    llm=llm,
                    missing_sentinels=outstanding.get(eid, []),
                    stray_sentinels=outstanding_strays.get(eid, []),
                    extra_issues=extra_issues,
                )
                total_repairs += 1
                block.lines = attempt.outcome_lines
                if attempt.outcome_heading is not None:
                    block.heading = attempt.outcome_heading
                if attempt.remaining_missing:
                    next_outstanding[eid] = attempt.remaining_missing
                if attempt.remaining_strays:
                    next_strays[eid] = attempt.remaining_strays
                # Persist the eid while source-script residue survives the repair.
                if _alien_line_issues(block.lines):
                    next_outstanding.setdefault(eid, [])
            outstanding = next_outstanding
            outstanding_strays = next_strays

        for eid, ids in outstanding.items():
            if not ids:
                # Source-script residue that outlasted repair carries no sentinel
                # ids; the delivery gate + body_alien audit already report it.
                continue
            flags.append(
                {
                    "location": eid,
                    "issue": (
                        f"numeric sentinel(s) still missing after "
                        f"{MAX_REPAIR_ITERATIONS} repair pass(es): "
                        f"{', '.join(ids)}"
                    ),
                    "code": "translate_sentinel_unrepaired",
                }
            )

    # Replace any remaining marker with its source line so it cannot reach
    # delivery; retained source text remains visible to the audit.
    _source_lines_by_eid = {u.akn_eid: (u.body_text or "").splitlines() for u in units}
    for _b in blocks:
        src = _source_lines_by_eid.get(_b.eid, [])
        swept = 0
        # Restore omitted slots before replacing markers so no text vanishes.
        if _b.eid in _marker_issues_by_eid and len(_b.lines) < len(src):
            deficit = len(src) - len(_b.lines)
            _b.lines.extend(src[len(_b.lines) :])
            swept += deficit
        for i, line in enumerate(_b.lines):
            if _UNTRANSLATED_MARKER in line:
                _b.lines[i] = src[i] if i < len(src) else ""
                swept += 1
        if swept:
            flags.append(
                {
                    "location": _b.eid,
                    "issue": f"{swept} line(s) failed translation and repair; source retained",
                    "code": "translate_body_line_fallback_source",
                }
            )

    akn_xml = apply_translation_to_akn(
        source_akn_xml,
        blocks,
        preface_lines,
        target_language=target_language,
        preamble_lines=preamble_lines,
        conclusions_lines=conclusions_lines,
        expression_date=expression_date,
    )

    # Use post-repair counts when the loop ran, otherwise the initial counts.
    if body_outcome.sentinels_missing_by_eid:
        final_missing_by_eid = outstanding
        final_missing_count = sum(len(v) for v in outstanding.values())
    else:
        final_missing_by_eid = body_outcome.sentinels_missing_by_eid
        final_missing_count = body_outcome.sentinels_missing
    # This map contains the surviving stray markers after repair.
    final_stray_by_eid = outstanding_strays

    # Preserve each source money surface and compare target occurrences as a
    # multiset, so duplicating one amount cannot hide a missing amount.
    source_money_surfaces_by_eid: dict[str, list[str]] = {}
    for eid, manifest in body_outcome.manifests_by_eid.items():
        surfaces = [tok.expected_target_surface for tok in manifest.tokens if tok.kind == "money"]
        if surfaces:
            source_money_surfaces_by_eid[eid] = surfaces

    # Audit the body before injecting notes, otherwise glossary text inflates
    # content coverage and defined-term checks.
    audit = audit_translation(
        _akn_text_content(source_akn_xml),
        _akn_text_content(akn_xml),
        notes,
        sentinels_expected=body_outcome.sentinels_expected,
        sentinels_missing=final_missing_count,
        sentinels_missing_by_eid=final_missing_by_eid,
        stray_sentinels=sum(len(v) for v in final_stray_by_eid.values()),
        stray_sentinels_by_eid=final_stray_by_eid,
        source_money_surfaces_by_eid=source_money_surfaces_by_eid,
        target_language=target_language,
        header_patterns=ocr_header_patterns or [],
        translated_akn_xml=akn_xml,
    )
    fm = front_matter_slot_defects(source_akn_xml, akn_xml)
    audit["front_matter_empty_slots"] = fm.empty_slots
    audit["front_matter_empty_slots_by_eid"] = fm.empty_by_eid
    audit["front_matter_duplicate_slots"] = fm.duplicate_slots
    audit["front_matter_repeated_furniture_slots"] = fm.repeated_furniture_slots
    # Report the citation-noun check without blocking delivery.
    doubled = count_doubled_citation_nouns(akn_xml, target_language)
    audit["doubled_citation_nouns"] = None if doubled is None else doubled[0]
    audit["doubled_citation_nouns_by_eid"] = {} if doubled is None else doubled[1]
    if doubled and doubled[0]:
        logger.warning(
            "translation_doubled_citation_nouns",
            count=doubled[0],
            eids=sorted(doubled[1]),
        )
    audit["front_matter_source_retained"] = front_matter_source_retained(
        source_akn_xml, akn_xml, target_language
    )
    audit["bodies_fallback_to_source"] = body_outcome.bodies_fallback_to_source
    audit["headings_fallback_to_source"] = body_outcome.headings_fallback_to_source
    # Denominator for the fallback-ratio hard fail; without it the ratio is
    # always 0.0 and the 5% ceiling never trips.
    audit["units_total"] = len(units)
    audit["skipped_without_eid"] = walk_summary.skipped_without_eid
    audit["repair_pass_count"] = repair_pass_count
    audit["total_repairs"] = total_repairs
    audit["exemplar_pool_active"] = body_outcome.exemplar_pool_active
    audit["exemplar_picks"] = body_outcome.exemplar_picks
    audit["exemplar_scans"] = body_outcome.exemplar_scans

    # Flag provisions whose target contains fewer than 80% of source clauses.
    from codify.translate.clause_parity import check_clause_parity

    unit_by_eid = {u.akn_eid: u for u in units}
    clause_gaps_by_eid: dict[str, float] = {}
    for block in blocks:
        src_unit = unit_by_eid.get(block.eid)
        if src_unit is None:
            continue
        src_body = src_unit.body_text or ""
        tgt_body = "\n".join(block.lines) if block.lines else ""
        ratio, gap = check_clause_parity(src_body, tgt_body, language=target_language)
        if gap:
            clause_gaps_by_eid[block.eid] = round(ratio, 3)
            flags.append(
                {
                    "location": block.eid,
                    "issue": (
                        f"clause count fell from {src_body[:60]!r}...; target "
                        f"has {ratio:.0%} of source clauses (below 80% threshold); "
                        "a subclause may have been dropped"
                    ),
                    "code": "translate_clause_gap",
                }
            )
    audit["clause_gaps_by_eid"] = clause_gaps_by_eid
    audit["notes_status"] = notes_status
    if notes_outcome.error:
        audit["notes_error"] = notes_outcome.error

    if audit.get("register_drifted"):
        counts = audit.get("deontic_counts") or {}
        rendered = ", ".join(f"{k!r} (n={v})" for k, v in counts.items())
        flags.append(
            {
                "location": "document",
                "issue": (
                    f"deontic register mixed across the target ({rendered}); "
                    "translation uses more than one drafting voice for the "
                    "same source obligation"
                ),
                "code": "register_deontic_mixed",
            }
        )

    # Notes stay in the result for callers; never inject the glossary into AKN.

    coverage = audit["content_coverage"]
    if coverage < COVERAGE_FLOOR:
        logger.warning("translation_low_coverage", coverage=coverage)
        flags.append(
            {
                "location": "document",
                "issue": f"low translation coverage ({coverage:.0%} of source) "
                "; a section may be untranslated or dropped",
                "code": "translate_low_coverage",
            }
        )

    # Optional judge phase: score a targeted sample and feed failures into
    # one additional repair pass.
    verdicts_by_eid: dict[str, dict[str, Any]] = {}
    judge_failed_count = 0
    judge_repairs = 0
    if judge_enabled:
        from codify.translate.judge import (
            issues_to_repair_details,
            judge_provision,
            select_sample,
        )
        from codify.translate.repair import RepairIssue
        from codify.translate.repair import repair_provision as _judge_repair

        flagged_eids = {
            f["location"]
            for f in flags
            if f.get("location") and f.get("code", "").startswith(("translate_", "notes_"))
        }
        sample = select_sample(
            units,
            flagged_eids=flagged_eids,
            per_chapter=3,
            cap=20,
            seed=f"judge-{target_language}",
        )
        block_by_eid = {b.eid: b for b in blocks}
        judge_client = judge_llm or llm
        unit_by_eid_for_repair = {u.akn_eid: u for u in units}
        for u in sample:
            block = block_by_eid.get(u.akn_eid)
            if block is None:
                continue
            tgt_text = "\n".join(block.lines) if block.lines else ""
            src_text = u.body_text or ""
            verdict = await judge_provision(
                eid=u.akn_eid,
                source_text=src_text,
                translated_text=tgt_text,
                notes_block=notes_block,
                target_language=target_language,
                llm=judge_client,
                model=judge_model,
            )
            if verdict is None:
                judge_failed_count += 1
                continue
            verdicts_by_eid[u.akn_eid] = verdict.model_dump()
            if verdict.overall != "fail":
                continue
            for issue in verdict.issues:
                flags.append(
                    {
                        "location": u.akn_eid,
                        "issue": f"judge {issue.dimension} defect: {issue.detail}",
                        "code": f"judge_{issue.dimension}",
                    }
                )
            # Feed judge findings into a targeted repair pass; structure and
            # sentinels remain protected by the repair prompt.
            manifest = body_outcome.manifests_by_eid.get(u.akn_eid)
            if manifest is None or not verdict.issues:
                continue
            details = issues_to_repair_details(verdict.issues)
            judge_repair_issues = [
                RepairIssue(code=code, detail=detail) for code, detail in details
            ]
            unit_for_repair = unit_by_eid_for_repair.get(u.akn_eid)
            if unit_for_repair is None:
                continue
            attempt = await _judge_repair(
                unit_for_repair,
                manifest,
                block.lines,
                block.heading,
                target_language=target_language,
                llm=judge_client,
                missing_sentinels=[],
                stray_sentinels=[],
                extra_issues=judge_repair_issues,
            )
            block.lines = attempt.outcome_lines
            if attempt.outcome_heading is not None:
                block.heading = attempt.outcome_heading
            judge_repairs += 1
    audit["judge_verdicts"] = verdicts_by_eid
    audit["judge_enabled"] = judge_enabled
    audit["judge_sample_size"] = len(verdicts_by_eid)
    audit["judge_failed_count"] = judge_failed_count
    audit["judge_repairs"] = judge_repairs
    if (
        judge_enabled
        and judge_failed_count
        and judge_failed_count >= max(1, len(verdicts_by_eid) // 3)
    ):
        flags.append(
            {
                "location": "document",
                "issue": (
                    f"judge LLM failed on {judge_failed_count} of "
                    f"{judge_failed_count + len(verdicts_by_eid)} sample calls; "
                    "adequacy signal is thin"
                ),
                "code": "judge_llm_failed",
            }
        )

    return TranslationResult(akn_xml=akn_xml, notes=notes, audit=audit, flags=flags)


__all__ = ["TranslationResult", "translate_document", "translate_title"]
