"""Tokenisation for the lexical retrieval arm.

Postgres indexes `provisions.search_tokens`, not `provisions.text`, so
tokenisation quality is this module's property rather than whichever stemmers
a Postgres major ships. The same function must run at index and at query time:
applied to one side only it makes recall worse, and silently.
"""

from __future__ import annotations

import threading
import unicodedata
from functools import lru_cache
from typing import Final

import regex
import structlog
from Stemmer import Stemmer

from codify.lang import normalise_digits, to_iso639_3
from codify.pipeline.enrich.arabic_normalise import fold_arabic_for_match
from codify.quality.sentinels import is_placeholder_only

# Bump on any change that moves `tokenise`'s output, including its imports and
# PyStemmer. `provisions.search_pipeline_version` records what produced a row,
# and `test_output_is_frozen_for_the_declared_version` fails if this is not.
TOKENISER_VERSION: Final = 2

logger = structlog.get_logger()

# Digits are kept for article numbers and years; marks are kept because they
# are vowels in Indic scripts, and excluding them split कानून into three.
# ponytail: no dictionary segmentation, so CJK and Thai are one run per span.
# 45 Japanese provisions corpus-wide; revisit with ICU if that grows.
_WORD = regex.compile(r"[\p{L}\p{N}\p{M}]+")

# Casefolding is not locale-aware: Turkish İ folds to `i` plus a combining dot,
# so indexed `İHALE` never met a query for `ihale`. Nothing else emits that.
_DOTTED_I = regex.compile("i\u0307")

# Hebrew niqqud, cantillation and the geresh/gershayim used in acronyms.
_HEBREW_MARKS = regex.compile(r"[֑-ׇ׳״]")

# Snowball, via PyStemmer. Arabic is deliberately absent: its Snowball stemmer
# fails the round-trip test that light stemming exists to pass (see _light10).
_SNOWBALL: Final[dict[str, str]] = {
    "eng": "english",
    "pol": "polish",
    "rus": "russian",
    "ell": "greek",
    "fra": "french",
    "spa": "spanish",
    "deu": "german",
    "ita": "italian",
    "por": "portuguese",
    "nld": "dutch",
    "ron": "romanian",
    "tur": "turkish",
    "ind": "indonesian",
    "srp": "serbian",
    "lit": "lithuanian",
    "hun": "hungarian",
    "fin": "finnish",
    "swe": "swedish",
    "dan": "danish",
    "ces": "czech",
    "est": "estonian",
    "hye": "armenian",
    "eus": "basque",
    "cat": "catalan",
    "gle": "irish",
    "hin": "hindi",
    "nep": "nepali",
    "nor": "norwegian",
    "tam": "tamil",
    "yid": "yiddish",
    "fas": "persian",
}

# Thread-local: a PyStemmer instance holds state and must not be shared. A
# sync query route runs in the threadpool, and corruption is silent.
_LOCAL = threading.local()


# `versions.language` should be 639-3 but carries 639-1 strays: 1,214 rows are
# `en`, one `lt`. Unfolded they tokenise differently from their siblings, so a
# query reaches one and not the other. Bounded: the query side is
# caller-supplied.
@lru_cache(maxsize=256)
def _fold_language(language: str) -> str:
    """639-3, or the input unchanged when it cannot be resolved."""
    try:
        return to_iso639_3(language)
    except ValueError:
        # A bad label must not fail an ingest; it costs stemming, not tokens.
        logger.warning("tokenise_unknown_language", language=language)
        return language


def _snowball_for(language: str) -> Stemmer | None:
    """Cached per-thread PyStemmer instance, or None where we stem nothing."""
    algorithm = _SNOWBALL.get(language)
    if algorithm is None:
        return None
    cache: dict[str, Stemmer] = getattr(_LOCAL, "stemmers", None) or {}
    if algorithm not in cache:
        cache[algorithm] = Stemmer(algorithm)
        _LOCAL.stemmers = cache
    return cache[algorithm]


# Arabic light stemming, ported from Lucene's ArabicStemmer (Apache 2.0). The
# property required is agreement, not linguistic correctness: stem(clitic + X)
# must equal stem(X), which Postgres's Snowball Arabic fails.

# Conjunction و is stripped before the article, so وللنقيب reaches the same
# stem as للنقيب. Lucene strips one prefix total and misses that class.
_AR_ARTICLES: Final = ("وال", "بال", "كال", "فال", "ال", "لل")
_AR_SUFFIXES: Final = ("ها", "ان", "ات", "ون", "ين", "يه", "ية", "ه", "ة", "ي")

# Shortest stem an affix strip may leave. Measured over 14,107 attested pairs:
# two-stage at 3 round-trips 97.4%, Lucene's single-prefix rule 96.4%, a floor
# of 4 only 95.0%. Raising it protects وزير but loses more pairs than it saves.
_AR_MIN_STEM: Final = 2
_AR_MIN_STEM_CONJUNCTION: Final = 3


def _light10(token: str) -> str:
    """Strip the conjunction, then one article prefix, then one suffix."""
    if (
        token.startswith("و")
        and not token.startswith("وال")
        and len(token) - 1 >= _AR_MIN_STEM_CONJUNCTION
    ):
        token = token[1:]
    for prefix in _AR_ARTICLES:
        if token.startswith(prefix) and len(token) - len(prefix) >= _AR_MIN_STEM:
            token = token[len(prefix) :]
            break
    for suffix in _AR_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _AR_MIN_STEM:
            token = token[: -len(suffix)]
            break
    return token


def _normalise(text: str, language: str) -> str:
    """Script-level folding, before segmentation."""
    text = unicodedata.normalize("NFKC", text)
    text = normalise_digits(text)
    if language == "ara":
        # Folds harakat, tatweel, alef/ya/ta-marbuta variants.
        return fold_arabic_for_match(text)
    if language == "heb":
        return _HEBREW_MARKS.sub("", text)
    # No generic combining-mark strip: NFKC has already composed the Latin,
    # Greek and Cyrillic diacritics it aimed at, and what stays uncomposed is
    # Indic vowel signs, which are semantic. Stripping them made कानून कानन.
    return text


def tokenise(text: str, language: str) -> list[str]:
    """Index-time and query-time tokens for `text` in ISO 639-3 `language`.

    One token out per token in, in source order, with no dedupe and no stopword
    removal, so positional queries stay possible later.
    """
    if not text:
        return []
    language = _fold_language(language)
    tokens = _WORD.findall(_normalise(text, language))
    if not tokens:
        return []
    # casefold, not lower: handles ß and the Greek final sigma.
    tokens = [_DOTTED_I.sub("i", t.casefold()) for t in tokens]
    if language == "ara":
        return [_light10(t) for t in tokens]
    stemmer = _snowball_for(language)
    if stemmer is None:
        return tokens
    return [stemmer.stemWord(t) for t in tokens]


def tokenise_to_text(text: str, language: str) -> str:
    """Space-joined form, as stored in `provisions.search_tokens`.

    A unit that is nothing but a non-transcription marker tokenises to nothing:
    both lexical arms read this column, and a filename is not an answer. Guarded
    here rather than at each write path, of which there are four.
    """
    if is_placeholder_only(text):
        return ""
    return " ".join(tokenise(text, language))


__all__ = ["TOKENISER_VERSION", "tokenise", "tokenise_to_text"]
