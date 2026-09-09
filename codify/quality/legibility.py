"""How readable a source text is, scored from its function-word rate.

A character-level proxy does not work: OCR that garbles letter shapes preserves
token lengths, so one-character-token share reads garbage as clean. Closed-class
words survive only when the letters did.
"""

from __future__ import annotations

import unicodedata

from codify.quality.lexicons import WordList

# Rate is per 1,000 tokens. Boundaries are the low end of each band.
BANDS: tuple[tuple[str, float], ...] = (
    ("good", 120.0),
    ("fair", 80.0),
    ("poor", 40.0),
    ("illegible", 0.0),
)

# Below this a rate is noise: a one-page decree can miss every function word.
MIN_TOKENS = 200

# Function words separate prose from lists. They do not separate a clean list
# from damaged text, and a legal corpus is full of indexes, fee schedules and
# one-line decisions that are legitimately low in prose. Content nouns die with
# the letter forms, so a document naming no instrument is damaged rather than
# terse. Both floors are calibrated in tests/fixtures/legibility/marked.jsonl;
# moving either means re-deriving them against that sample.
LEXICON_FLOOR = 12.0
PROSE_FLOOR = 80.0

# Guards direct `lexicon_rate` callers. `text_verdict` cannot reach it: it scores
# prose first against the higher `MIN_TOKENS`, so anything that gets as far as
# the lexicon already has more tokens than this.
MIN_LEXICON_TOKENS = 60

__all__ = [
    "BANDS",
    "LEXICON_FLOOR",
    "MIN_LEXICON_TOKENS",
    "MIN_TOKENS",
    "PROSE_FLOOR",
    "band_for",
    "function_word_rate",
    "lexicon_rate",
    "text_verdict",
    "verdict_from_rates",
]


def function_word_rate(text: str, words: WordList | None) -> float | None:
    """Function words per 1,000 tokens, or None when the text cannot be judged.

    Matched unfolded, unlike anchor recovery: folding would let garbled forms
    match. Callers pass text already through ``normalise_rtl_extract``.
    """
    if words is None or not words.function_words:
        return None
    tokens = _tokens(text, words)
    if len(tokens) < MIN_TOKENS:
        return None
    hits = sum(1 for token in tokens if token in words.function_words)
    return hits * 1000.0 / len(tokens)


def band_for(rate: float | None) -> str | None:
    """The band a rate falls in, or None when there is no rate to band."""
    if rate is None:
        return None
    for name, floor in BANDS:
        if rate >= floor:
            return name
    return BANDS[-1][0]


def lexicon_rate(text: str, words: WordList | None) -> float | None:
    """Legal content nouns per 1,000 tokens, or None when the text cannot be
    judged. Scored over the same tokens as ``function_word_rate`` against a
    lower floor, so a short document still has a vocabulary reading when it has
    no prose reading."""
    if words is None or not words.lexicon_words:
        return None
    tokens = _tokens(text, words)
    if len(tokens) < MIN_LEXICON_TOKENS:
        return None
    hits = sum(1 for token in tokens if token in words.lexicon_words)
    return hits * 1000.0 / len(tokens)


def verdict_from_rates(
    prose: float | None,
    lexicon: float | None,
    *,
    prose_floor: float | None = PROSE_FLOOR,
    lexicon_floor: float | None = LEXICON_FLOOR,
) -> str | None:
    """The verdict rule, over rates already measured. Separated so a calibration
    fixture can pin it without carrying the text the rates came from."""
    if prose is None or prose_floor is None:
        return None
    if prose >= prose_floor:
        return "prose"
    # No usable content-noun floor means no tie-break, so say nothing rather
    # than call a terse document damaged.
    if lexicon is None or lexicon_floor is None:
        return None
    return "damaged" if lexicon < lexicon_floor else "not_prose"


def text_verdict(text: str, words: WordList | None) -> str | None:
    """What the source text is, or None when the script or the length gives no
    grounds to say.

    Function-word density is the axis. High is prose. Low is either a list, a
    schedule or a one-line decision, or text whose letter forms did not survive,
    and those two are indistinguishable on that axis alone. Content nouns break
    the tie: a document that is merely terse still names the instruments it is
    about, where a corrupted one names nothing.

    ``not_prose`` is a question about scope, whether such material belongs in the
    corpus at all. ``damaged`` is a question about quality, and is the one worth
    acting on. Neither decides OCR: that is the page-level gate in
    ``pipeline.enrich.ocr``, which measures the same density against a lower
    floor and was calibrated separately.
    """
    return verdict_from_rates(
        function_word_rate(text, words),
        lexicon_rate(text, words),
        prose_floor=words.prose_floor if words else None,
        lexicon_floor=words.lexicon_floor if words else None,
    )


def _tokens(text: str, words: WordList) -> list[str]:
    """Runs of the language's own letters. Digits, punctuation and any other
    script are separators, so a bilingual page is scored on the language being
    judged. Combining marks are skipped: vocalised prose would otherwise split
    at every harakat."""
    tokens: list[str] = []
    current: list[str] = []
    for ch in text:
        if unicodedata.category(ch) == "Mn":
            continue
        if words.is_letter(ch):
            current.append(ch)
        elif current:
            tokens.append("".join(current).casefold())
            current = []
    if current:
        tokens.append("".join(current).casefold())
    return tokens
