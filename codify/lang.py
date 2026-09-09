"""Language-code normalisation. The corpus standardises on ISO 639-3.

External inputs (LLM metadata extraction, translation requests, upstream
acquirers) sometimes carry ISO 639-1 two-letter codes. Normalise at every
assignment boundary so `Version.language` is always 639-3.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

# Single source of truth: (639-1, 639-3, lowercase English name) per language
# the corpus serves (EU official + EEA + candidates + workspace jurisdictions
# + common neighbours). ISO1_TO_3 / NAME_TO_3 are derived, so the two lookups
# cannot drift. Deliberately not pycountry/langcodes: ISO's own name data
# mis-resolves this vocabulary ("en" fuzzy-matches the En language, "Greek"
# is named "Modern Greek (1453-)"), and the inputs here are our own closed
# set, not arbitrary language names.
_LANGUAGES: tuple[tuple[str, str, str], ...] = (
    ("ar", "ara", "arabic"),
    ("az", "aze", "azerbaijani"),
    ("bg", "bul", "bulgarian"),
    ("bs", "bos", "bosnian"),
    ("cs", "ces", "czech"),
    ("cy", "cym", "welsh"),
    ("da", "dan", "danish"),
    ("de", "deu", "german"),
    ("el", "ell", "greek"),
    ("en", "eng", "english"),
    ("eo", "epo", "esperanto"),
    ("es", "spa", "spanish"),
    ("et", "est", "estonian"),
    ("fi", "fin", "finnish"),
    ("fr", "fra", "french"),
    ("ga", "gle", "irish"),
    ("he", "heb", "hebrew"),
    ("hr", "hrv", "croatian"),
    ("hu", "hun", "hungarian"),
    ("hy", "hye", "armenian"),
    ("id", "ind", "indonesian"),
    ("is", "isl", "icelandic"),
    ("it", "ita", "italian"),
    ("ja", "jpn", "japanese"),
    ("ka", "kat", "georgian"),
    ("kk", "kaz", "kazakh"),
    ("ky", "kir", "kyrgyz"),
    ("lb", "ltz", "luxembourgish"),
    ("lt", "lit", "lithuanian"),
    ("lv", "lav", "latvian"),
    ("mk", "mkd", "macedonian"),
    ("mt", "mlt", "maltese"),
    ("nl", "nld", "dutch"),
    ("no", "nor", "norwegian"),
    ("pl", "pol", "polish"),
    ("pt", "por", "portuguese"),
    ("ro", "ron", "romanian"),
    ("ru", "rus", "russian"),
    ("sk", "slk", "slovak"),
    ("sl", "slv", "slovenian"),
    ("sq", "sqi", "albanian"),
    ("sr", "srp", "serbian"),
    ("sv", "swe", "swedish"),
    ("tr", "tur", "turkish"),
    ("uk", "ukr", "ukrainian"),
    ("uz", "uzb", "uzbek"),
)

ISO1_TO_3: dict[str, str] = {iso1: iso3 for iso1, iso3, _ in _LANGUAGES}
NAME_TO_3: dict[str, str] = {name: iso3 for _, iso3, name in _LANGUAGES}
_ISO3: frozenset[str] = frozenset(iso3 for _, iso3, _name in _LANGUAGES)


def to_iso639_3(code: str | None) -> str:
    """Normalise a language code, locale tag, or English language name to
    ISO 639-3.

    Two-letter (639-1) codes and English names are mapped; locale tags
    ("en-US") fold on their primary subtag; three-letter codes pass through
    as 639-3, with a warning when off-table (legitimate rare codes like
    "lat" pass, typos surface in logs). Empty/None → "". Anything else
    raises ValueError: silently passing names through is how
    `versions.language='english'` rows were minted.
    """
    if not code:
        return ""
    c = code.strip().lower()
    if c in ISO1_TO_3:
        return ISO1_TO_3[c]
    if c in NAME_TO_3:
        return NAME_TO_3[c]
    if len(c) == 3 and c.isalpha():
        if c not in _ISO3:
            logger.warning("iso639_3_passthrough_unverified", code=c)
        return c
    primary = c.replace("_", "-").split("-", 1)[0]
    if primary != c and primary in ISO1_TO_3:
        return ISO1_TO_3[primary]
    raise ValueError(f"unknown language code or name: {code!r}")


# Non-Latin decimal digits → ASCII, for number/eId normalisation. Covers the
# scripts the corpus carries (Arabic-Indic + extended) plus common neighbours.
_DIGIT_TRANS = str.maketrans(
    {
        **{chr(0x0660 + i): str(i) for i in range(10)},  # Arabic-Indic ٠-٩
        **{chr(0x06F0 + i): str(i) for i in range(10)},  # Extended Arabic-Indic ۰-۹
        **{chr(0x09E6 + i): str(i) for i in range(10)},  # Bengali ০-৯
        **{chr(0x0966 + i): str(i) for i in range(10)},  # Devanagari ०-९
        **{chr(0x0E50 + i): str(i) for i in range(10)},  # Thai ๐-๙
    }
)


def normalise_digits(text: str) -> str:
    """Map non-Latin decimal digits to ASCII (Arabic-Indic ٣ → 3)."""
    return text.translate(_DIGIT_TRANS)


# Regex character-class body of every digit normalise_digits folds. Detectors
# build their pattern from this so they stay in lockstep with the fold rather
# than hand-maintaining a narrower parallel range that silently skips scripts.
NON_ASCII_DIGIT_CLASS = "".join(map(chr, _DIGIT_TRANS))
