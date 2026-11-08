"""Country names in a law's own language, from published name tables.

`countries.json` is built by `scripts/build_country_names.py`; nothing is fetched here.
Not covered: languages whose country names inflect, country groups and TARIC areas.
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

import regex

from codify.lang import to_iso639_3

_DATA = Path(__file__).with_name("countries.json")
# A hyphen bounds a name only where a capital stands across it, as in "EU-Japan".
_BEFORE = r"(?<!\w)(?<![^\p{Lu}]-)(?<!^-)"
_AFTER = r"(?!\w)(?!-(?!\p{Lu}))"


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
    pattern, codes = _matcher(to_iso639_3(language))
    hits: Counter[str] = Counter()
    written: dict[str, Counter[str]] = {}
    for match in pattern.finditer(_fold(text)):
        found = " ".join(match.group().split())
        code = codes[found]
        if code is None:
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


@cache
def _matcher(language: str) -> tuple[regex.Pattern[str], dict[str, str | None]]:
    codes: dict[str, str | None] = {}
    for place in _load()["not_countries"].get(language, []):
        codes.update(dict.fromkeys(name_forms(place)))
    for code, names in _table(language).items():
        for name in names:
            for form in name_forms(name):
                codes.setdefault(form, code)
    # Longest first, so "Hong Kong SAR China" is one match and not also China.
    forms = sorted(codes, key=len, reverse=True)
    alternation = "|".join(regex.escape(form).replace(r"\ ", r"\s+") for form in forms)
    return regex.compile(rf"{_BEFORE}(?:{alternation}){_AFTER}"), codes
