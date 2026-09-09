"""Deterministic quality signals for a single OCR page read."""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

import structlog

from codify.pipeline.enrich.scripts import ScriptPack

logger = structlog.get_logger()

__all__ = [
    "PageVerdict",
    "chars_per_ink",
    "divergence",
    "ink_ratio",
    "letter_spaced_runs",
    "measured",
    "split_words_the_rival_keeps",
    "numeral_parity",
    "presentation_form_share",
    "repeat_density",
    "score_page",
    "script_purity",
]

Verdict = Literal["accept", "reread", "escalate"]

# Below this a page carried no ink to read: a blank or near-blank scan, whose
# ratios are noise and whose empty read is a blank leaf, not a failed one. Shared
# with the combined-text marker so a page is blank in one place and blank in all.
BLANK_INK = 0.02
# Shortest text worth measuring shape on. A one-line decision is legitimately
# short and would read as degenerate on any density measure.
_MIN_CHARS = 40
_WORD_RE = re.compile(r"\S+")
# Extended Latin, not just ASCII: a damaged Albanian or German page splits accented
# letters too, and NBSP separates as readily as a space. Latin-1 Supplement and
# Latin Extended-A, minus the maths glyphs at U+00D7 and U+00F7.
_SPACED_LETTER = "[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f]"
_LETTER_WORD_RE = re.compile(r"[^\W\d_]+")
_WHITESPACE_RE = re.compile(r"\s+")
# Line breaks and table cell walls: a run never crosses either.
_SEGMENT_RE = re.compile(r"[|\n\r]+")
_SPACED_RUN_RE = re.compile(
    rf"(?:(?<=\s)|^)(?:{_SPACED_LETTER}[ \u00a0]){{3,}}{_SPACED_LETTER}(?=\s|$)"
)


def ink_ratio(image_bytes: bytes) -> float | None:
    """Fraction of the page that is dark, from a greyscale histogram.

    None only when the bytes will not decode as an image. Pillow missing is a
    broken install, not an unreadable page, so it raises.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            histogram = im.convert("L").histogram()
    except (OSError, UnidentifiedImageError):
        logger.warning("ink_ratio_undecodable_image", bytes=len(image_bytes))
        return None
    total = sum(histogram)
    if not total:
        return None
    return sum(histogram[:128]) / total


def chars_per_ink(text: str, ink: float | None) -> float | None:
    """Characters returned per unit of ink. A page dense with print that returns a
    paragraph has summarised rather than transcribed, the failure that moved this
    pipeline off the vision route once already. A page below the blank-ink floor
    abstains: an empty read there is a blank leaf, not a failure.
    """
    if ink is None or ink < BLANK_INK:
        return None
    return len(text.strip()) / ink


def repeat_density(text: str, n: int = 6) -> float | None:
    """Share of word n-grams that are repeats, None when the text is too short. A model
    that loses its place emits the same clause until its budget runs out. Word n-grams
    rather than characters, because Arabic legal prose repeats short character runs
    legitimately and long word runs almost never.
    """
    words = _WORD_RE.findall(text)
    if len(text.strip()) < _MIN_CHARS or len(words) < n * 2:
        return None
    grams = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
    counts = Counter(grams)
    return 1.0 - (len(counts) / len(grams))


def presentation_form_share(text: str) -> float | None:
    """Share of letters in the Arabic Presentation Forms blocks.

    OCR should emit standard Arabic. Presentation forms mean the read came back
    as isolated display glyphs, which reverse and detach in downstream parsing.
    """
    # Letters, not every non-space character. A page of article numbers and
    # brackets would otherwise dilute a wholly glyph-mangled read toward clean.
    letters = [c for c in text if c.isalpha()]
    if len(letters) < _MIN_CHARS:
        return None
    forms = sum(1 for c in letters if 0xFB50 <= ord(c) <= 0xFEFF)
    return forms / len(letters)


def _despace(run: str) -> str:
    """The word a run spells, folded for comparison against a whole-word read."""
    return run.replace(" ", "").replace("\u00a0", "").casefold()


def letter_spaced_runs(text: str) -> int:
    """Runs of four or more single Latin letters separated by spaces, which is
    what a damaged text layer makes of a word: `A l a m a t`. A count, not a
    rate: one run is a defect where a low rate over a long page is not."""
    return len(_SPACED_RUN_RE.findall(text))


# A run can spell more than one word (`S e r t i f i k a t H a k M i l i k`), so
# the rival is matched on joined word sequences up to this many words.
_MAX_RUN_WORDS = 8

# Above this length a despaced run is matched against the rival space-stripped too,
# catching runs that start mid-word or outrun the join window, where a coincidental
# substring is no real risk. Below it the join is required, so `d a r i` cannot
# match inside `daripada`.
_LOOSE_MATCH_CHARS = 12


def split_words_the_rival_keeps(text: str, rival: str) -> set[str]:
    """Words ``text`` spells as loose letters that ``rival`` spells whole.

    Counting runs is not enough: a rival rendering a table row as spaced letters would
    cancel out real damage elsewhere. It must be the same word, the rival must keep it
    whole, and a rival that also spells it spaced has no complaint. Latin script only.
    """
    split = {_despace(run) for run in _SPACED_RUN_RE.findall(text)}
    if not split:
        return set()
    split -= {_despace(run) for run in _SPACED_RUN_RE.findall(rival)}
    if not split:
        return set()
    joined = _joined_word_runs(rival)
    compact = _WHITESPACE_RE.sub("", rival).casefold()
    return {
        word
        for word in split
        if word in joined or (len(word) >= _LOOSE_MATCH_CHARS and word in compact)
    }


def _joined_word_runs(text: str) -> set[str]:
    """Every run of consecutive whole words, joined. Matching a despaced run against these
    rather than against space-stripped text keeps `d a r i` from matching inside
    `daripada`. Joined within a line and a table cell only, so a rival rendering four
    columns as `| A | B | C | D |` cannot offer `abcd` to a layer whose own padding
    collapsed to `A B C D`.
    """
    joined: set[str] = set()
    for segment in _SEGMENT_RE.split(text):
        words = [w.casefold() for w in _LETTER_WORD_RE.findall(segment)]
        joined |= {
            "".join(words[i:j])
            for i in range(len(words))
            for j in range(i + 1, min(i + _MAX_RUN_WORDS, len(words)) + 1)
        }
    return joined


def script_purity(text: str, pack: ScriptPack | None) -> float | None:
    """Share of letters belonging to the page's expected script.

    A read that drifts into another script has stopped transcribing. Digits and
    punctuation are excluded: a legal page is full of both in either script.
    """
    if pack is None:
        return None
    letters = [c for c in text if c.isalpha()]
    if len(letters) < _MIN_CHARS:
        return None
    return sum(1 for c in letters if pack.is_letter(c)) / len(letters)


def measured(signals: dict[str, Any]) -> list[str]:
    """The signals that returned a number. Empty means nothing could be judged.

    The verdict has to branch on this before it branches on thresholds: a page
    nothing could measure is unmeasured, not clean.
    """
    return sorted(k for k, v in signals.items() if v is not None)


def divergence(a: str, b: str) -> float | None:
    """How far apart two engines read the same page, 0 agree, 1 share nothing.

    Token Jaccard, so it ignores order and repetition. A proxy that localises
    doubt for a later calibration, not a measure of which read is better.
    """
    left, right = set(a.split()), set(b.split())
    if not left or not right:
        return None
    return 1.0 - len(left & right) / len(left | right)


# Digits in either script; Arabic-Indic and Persian forms fold to ASCII so a
# figure reads the same whichever engine transcribed it.
_DIGIT_RUN_RE = re.compile(r"[0-9٠-٩۰-۹][0-9٠-٩۰-۹.,]*")
_DIGIT_FOLD = {base + d: chr(0x30 + d) for base in (0x0660, 0x06F0) for d in range(10)}


def _numbers(text: str) -> list[str]:
    folded = text.translate(_DIGIT_FOLD)
    return [run.rstrip(".,").replace(",", "") for run in _DIGIT_RUN_RE.findall(folded)]


def numeral_parity(a: str, b: str) -> float | None:
    """Share of the fuller read's numbers the other also carries.

    A summarising read drops the amounts a faithful read keeps, and a Jaccard over all
    tokens buries that in the prose, so this looks only at figures. None only when
    neither read has one: a read dropping every figure the other kept is the case to
    catch, so it scores 0 rather than abstaining.
    """
    fuller, other = sorted((_numbers(a), _numbers(b)), key=len, reverse=True)
    if not fuller:
        return None
    remaining = Counter(other)
    kept = 0
    for number in fuller:
        if remaining[number] > 0:
            remaining[number] -= 1
            kept += 1
    return kept / len(fuller)


# Provisional, pending contrast-aware calibration against synthesised pages. Chosen
# to clear the clean C01-C04 cohort with no false positive (lowest chars_per_ink
# ~1e4, script_purity ~0.94, no presentation forms) while still flagging the failure
# modes it does not contain.
_CHARS_PER_INK_LOW = 5_000.0
_REPEAT_DENSITY_HIGH = 0.5
_PRESENTATION_FORM_HIGH = 0.3
_SCRIPT_PURITY_LOW = 0.85
_DIVERGENCE_HIGH = 0.7
_NUMERAL_PARITY_LOW = 0.8


@dataclass(frozen=True)
class PageVerdict:
    """One page's deterministic quality verdict and its reasons. `reasons` is
    machine-readable (`"chars_per_ink:low"`), so a consumer branches on the failure mode
    rather than a score. `signals` keeps every raw value, `None` included, so a later
    calibration reads real inputs.
    """

    verdict: Verdict
    reasons: list[str]
    signals: dict[str, float | None]


def score_page(
    text: str,
    *,
    ink: float | None = None,
    rival_text: str | None = None,
    divergence_score: float | None = None,
    pack: ScriptPack | None = None,
) -> PageVerdict:
    """Score one OCR page read into accept, reread or escalate. No model.

    `escalate` is a failure the read cannot be trusted through: summarising,
    degeneracy loop, glyph-mangling, script drift. `reread` is a softer doubt: readers
    disagree, a figure went missing, or nothing could be measured. A page nothing could
    measure is `reread`, never `accept`, because unmeasured is not clean.

    `ink` is `ink_ratio` of the page image, from the caller holding the render;
    `divergence_score` is the divergence already computed at ingest.
    """
    rival = rival_text or ""
    signals: dict[str, float | None] = {
        "chars_per_ink": chars_per_ink(text, ink),
        "repeat_density": repeat_density(text),
        "presentation_form_share": presentation_form_share(text),
        "script_purity": script_purity(text, pack),
        "divergence": divergence_score if divergence_score is not None else divergence(text, rival),
        "numeral_parity": numeral_parity(text, rival) if rival else None,
    }

    escalate, reread = [], []
    if (v := signals["chars_per_ink"]) is not None and v < _CHARS_PER_INK_LOW:
        escalate.append("chars_per_ink:low")
    if (v := signals["repeat_density"]) is not None and v > _REPEAT_DENSITY_HIGH:
        escalate.append("repeat_density:high")
    if (v := signals["presentation_form_share"]) is not None and v > _PRESENTATION_FORM_HIGH:
        escalate.append("presentation_form_share:high")
    if (v := signals["script_purity"]) is not None and v < _SCRIPT_PURITY_LOW:
        escalate.append("script_purity:low")
    if (v := signals["divergence"]) is not None and v > _DIVERGENCE_HIGH:
        reread.append("divergence:high")
    if (v := signals["numeral_parity"]) is not None and v < _NUMERAL_PARITY_LOW:
        reread.append("numeral_parity:low")

    if escalate:
        return PageVerdict("escalate", sorted(escalate + reread), signals)
    if reread:
        return PageVerdict("reread", sorted(reread), signals)
    if not measured(signals):
        return PageVerdict("reread", ["unmeasured"], signals)
    return PageVerdict("accept", [], signals)
