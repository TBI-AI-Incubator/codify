"""Country names in a law's own language, from published name tables.

`countries.json` is built by `scripts/build_country_names.py`; nothing is fetched here.
Not covered: languages whose country names inflect, country groups and TARIC areas.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

import regex

from codify.lang import to_iso639_3

_DATA = Path(__file__).with_name("countries.json")


@cache
def _load() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(_DATA.read_text(encoding="utf-8"))
    return loaded


def _names() -> dict[str, dict[str, list[str]]]:
    names: dict[str, dict[str, list[str]]] = _load()["names"]
    return names


def languages() -> frozenset[str]:
    """The ISO 639-3 languages the table names countries in."""
    return frozenset(_names())


def country_names(language: str) -> dict[str, tuple[str, ...]]:
    """ISO 3166-1 alpha-2 code to the country's names in `language`, standard name first."""
    return {code: tuple(names) for code, names in _table(to_iso639_3(language)).items()}


def countries_named(text: str, language: str) -> list[tuple[str, str]]:
    """(code, name as matched, folded: NFC, one apostrophe and hyphen, single spaces) for
    each country `text` names in `language`, most named first. A name matches as
    written or in capitals, not in any other case."""
    pattern, codes, capitals_only = _matcher(to_iso639_3(language))
    folded = _fold(text)
    hits: Counter[str] = Counter()
    written: dict[str, Counter[str]] = {}
    # A refused match still spends its text, so no shorter name inside it counts.
    for match in pattern.finditer(folded):
        found = " ".join(match.group().split())
        code = codes[found]
        if code is None or not _joins(folded, match.start(), match.end(), found in capitals_only):
            continue
        hits[code] += 1
        written.setdefault(code, Counter())[found] += 1
    ranked = sorted(hits, key=lambda code: (-hits[code], code))
    return [(code, written[code].most_common(1)[0][0]) for code in ranked]


def _table(language: str) -> dict[str, list[str]]:
    names = _names()
    if language not in names:
        raise ValueError(f"no country names in {language!r}; the table has {sorted(names)}")
    return names[language]


_MARKS = str.maketrans({"'": "’", "‘": "’", "ʼ": "’", "‐": "-", "‑": "-"})


def name_forms(name: str) -> tuple[str, str]:
    """The forms a name matches in: folded as text is, as written and in capitals."""
    form = " ".join(_fold(name).split())
    return form, form.upper()


def _fold(text: str) -> str:
    """NFC without format characters (a soft hyphen), and one apostrophe and one hyphen."""
    visible = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return unicodedata.normalize("NFC", visible).translate(_MARKS)


def _joins(text: str, start: int, end: int, capitals: bool) -> bool:
    """Whether each hyphen beside a match has a capital across it, as in "EU-Japan". A
    capitals form takes none: in text set in capitals every letter is one."""
    for hyphen, far in ((start - 1, start - 2), (end, end + 1)):
        if 0 <= hyphen < len(text) and text[hyphen] == "-":
            if capitals or not (0 <= far < len(text) and text[far].isupper()):
                return False
    return True


@cache
def _matcher(language: str) -> tuple[regex.Pattern[str], dict[str, str | None], frozenset[str]]:
    codes: dict[str, str | None] = {}
    capitals_only: set[str] = set()
    named: list[tuple[str, str | None]] = [
        (place, None) for place in _load()["not_countries"].get(language, [])
    ]
    named += [(name, code) for code, names in _table(language).items() for name in names]
    for name, code in named:
        written, capitals = name_forms(name)
        codes.setdefault(written, code)
        if capitals not in codes:
            codes[capitals] = code
            capitals_only.add(capitals)
    # Longest first, so "Hong Kong SAR China" is one match and not also China.
    forms = sorted(codes, key=lambda form: (-len(form), form))
    alternation = "|".join(re.escape(form).replace(r"\ ", r"\s+") for form in forms)
    # `regex`, not `re`: a footnote marker glued to a name (China¹) is not a word character.
    pattern = regex.compile(rf"(?<!\w)(?:{alternation})(?!\w)")
    return pattern, codes, frozenset(capitals_only)
