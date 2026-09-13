"""Local-calendar → Gregorian conversion for FRBR URIs.

FRBR URIs always use Gregorian years. Non-Gregorian jurisdictions store
the local date as an FRBRalias; this module converts them.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, cast

from .jurisdictions import CalendarConversion, load_config, try_load_config


class CalendarConversionError(ValueError):
    pass


def to_gregorian_year(local_year: str | int, country_code: str, month: int | None = None) -> int:
    """Convert a local-calendar year to Gregorian. A named jurisdiction with no
    config raises: reading a Hijri year as Gregorian is a plausible wrong date."""
    if not country_code:
        # Nothing to resolve; an absent config raises rather than defaulting.
        return _coerce_int(local_year)
    cfg = load_config(country_code)
    if cfg.frbr is None or cfg.frbr.calendar_conversion is None:
        return _coerce_int(local_year)

    return _apply_rule(local_year, cfg.frbr.calendar_conversion, month=month)


def _coerce_int(v: str | int) -> int:
    if isinstance(v, int):
        return v
    # Handle Japanese era names like "令和4" or "Reiwa 4"
    cleaned = re.sub(r"[^\d]", "", str(v).strip())
    if not cleaned:
        raise CalendarConversionError(f"cannot parse year {v!r}")
    return int(cleaned)


_SIGNED_INT_RE = re.compile(r"^[+-]?\d+$")


def _apply_rule(local_year: str | int, rule: CalendarConversion, *, month: int | None) -> int:
    kind = rule.kind
    # Every calendar here begins at one. Parse a numeric string before the check,
    # or "0" and "-5" pass a guard the integers fail. A non-numeric value is an
    # era name and belongs to the branch that reads it.
    numeric = str(local_year).strip()
    if _SIGNED_INT_RE.match(numeric) and int(numeric) < 1:
        raise CalendarConversionError(f"{local_year!r} is not a year")

    if kind == "epoch_offset":
        if rule.epoch_year is None:
            raise CalendarConversionError("epoch_offset requires epoch_year")
        year_only = _leading_year(str(local_year))
        if year_only is None or year_only < 1:
            raise CalendarConversionError(f"no year in {local_year!r}")
        return year_only + rule.epoch_year

    if kind == "bikram_samvat":
        offset = rule.offset if rule.offset is not None else 57
        ny_month = rule.new_year_month or 4
        year = _coerce_int(local_year)
        if month is not None and month < ny_month:
            return year - offset
        return year - offset + 1

    if kind == "buddhist":
        epoch = rule.epoch_year if rule.epoch_year is not None else -543
        return _coerce_int(local_year) + epoch

    if kind == "era_table":
        return _convert_era_table(local_year, rule)

    if kind == "hijri_lunar":
        return _convert_hijri_lunar(_coerce_int(local_year))

    if kind == "hijri_solar":
        # Solar Hijri ≈ Gregorian - 621 (approximation; varies by month)
        return _coerce_int(local_year) + 621

    if kind == "ethiopian":
        # Ethiopian calendar is 7-8 years behind Gregorian.
        # After Sept 11 (Meskerem 1): EC + 8 = GC. Before: EC + 7 = GC.
        year = _coerce_int(local_year)
        if month is not None and month < 9:
            return year + 8
        return year + 7

    raise CalendarConversionError(f"unknown calendar conversion kind: {kind}")


def year_from_calendar(year: str | int, calendar: str, country: str = "") -> int | None:
    """Convert a year + calendar to Gregorian. Returns None if the calendar
    isn't supported, or if it needs a jurisdiction's era table and no country
    was given to find one. Uses Muharram 1 / Farvardin 1 / Meskerem 1 / etc. for
    the conversion since FRBR URIs only need the year."""
    cal = normalise_calendar(calendar)
    if cal in ("minguo", "roc", "taiwan"):
        # Before the coercion, which concatenated every digit of a native date and
        # returned early on 民國元年, where 元 is the first year.
        year_only = _leading_year(str(year))
        if year_only is None or year_only < 1:
            return None
        return year_only + 1911
    if cal in ("japanese_era",):
        # Before the int coercion: this arm's input is "Reiwa 5", not a number,
        # and the era table belongs to the jurisdiction rather than this module.
        if not country or str(year).strip().lstrip("-").isdigit():
            # A bare number names no era, and Showa 5 is not Reiwa 5. Without a
            # country there is no table to read either.
            return None
        # to_gregorian_year coerces a bare number when a jurisdiction declares
        # no rule, which would read "Reiwa 5" on a Gregorian country as 5.
        cfg = try_load_config(country)
        rule = cfg.frbr.calendar_conversion if cfg and cfg.frbr else None
        # Any rule is not enough: a fixed offset would read an era year as its own.
        if rule is None or rule.kind != "era_table":
            return None
        try:
            return to_gregorian_year(year, country)
        except (CalendarConversionError, LookupError):
            return None
    try:
        n = _coerce_int(year)
    except CalendarConversionError:
        return None
    # Every calendar here counts from one; zero and below are not years.
    if n < 1:
        return None
    if cal in ("", "gregorian"):
        return n
    if cal in ("hijri", "hijri_lunar", "lunar_hijri", "islamic"):
        # hijridate uses Umm al-Qura (accurate but only covers 1343-1500 AH);
        # fall back to convertdate's tabular Islamic for older years.
        try:
            from hijridate import Hijri

            return Hijri(n, 1, 1).to_gregorian().year
        except (OverflowError, ImportError):
            from convertdate import islamic  # type: ignore[import-untyped]

            return cast(int, islamic.to_gregorian(n, 1, 1)[0])
    if cal in ("hijri_solar", "solar_hijri", "persian", "jalali"):
        from convertdate import persian

        return cast(int, persian.to_gregorian(n, 1, 1)[0])
    if cal in ("ethiopian", "ethiopic"):
        # Ethiopian year begins on Meskerem 1 (≈ 11 Sept Gregorian); the
        # overlap year is EC + 7 before that date and EC + 8 after, so
        # for FRBR year purposes the predominant Gregorian year is EC + 8.
        return n + 8
    if cal in ("coptic",):
        from convertdate import coptic

        return cast(int, coptic.to_gregorian(n, 1, 1)[0])
    if cal in ("hebrew",):
        from convertdate import hebrew

        return cast(int, hebrew.to_gregorian(n, 7, 1)[0])  # Tishrei 1, civil new year
    if cal in ("buddhist", "buddhist_era", "thai"):
        return n - 543
    return None


def _convert_hijri_lunar(hijri_year: int) -> int:
    """Lunar Hijri → Gregorian. Kept for back-compat with `_apply_rule`."""
    result = year_from_calendar(hijri_year, "hijri")
    if result is None:  # only when hijridate fails to import
        return round(hijri_year * 0.970229 + 621.5643)
    return result


def _era_start_year(era: dict[str, Any]) -> int:
    """Extract the Gregorian start year from an era entry."""
    if "start_gregorian" in era:
        return int(era["start_gregorian"])
    start = era.get("start", "")
    if start:
        return int(str(start)[:4])
    return 0


# Calendars whose year means nothing without its era name, so a conversion that
# fails has no number to fall back on: 6 is not a year, it is part of one.
ERA_NAMED_CALENDARS = frozenset({"japanese_era"})


_KANJI_DIGITS = {
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def kanji_number(text: str) -> int | None:
    """A kanji numeral below 100, the range an era year occupies. 六 is 6, 十 is
    10, 二十三 is 23. None for anything that is not one, malformed sequences of
    accepted characters included: 十十 and 二三十 are not numbers."""
    if not text or any(c not in _KANJI_DIGITS and c != "十" for c in text):
        return None
    if text.count("十") > 1:
        return None
    if "十" not in text:
        # A bare numeral is one digit; 二三 is not twenty-three.
        return _KANJI_DIGITS[text] if len(text) == 1 else None
    tens, _, units = text.partition("十")
    if len(tens) > 1 or len(units) > 1:
        return None
    high = _KANJI_DIGITS[tens] if tens else 1
    low = _KANJI_DIGITS[units] if units else 0
    return high * 10 + low


# The year in a native date: 元 for the first year, else the digits before a year
# marker, else the first run.
_YEAR_RUN_RE = re.compile(r"元\s*年|(\d+)\s*(?:年|年度)|(\d+)")


def normalise_calendar(value: str | None) -> str:
    """The calendar name as every reader should see it. Four callers lowercased
    it and three did not strip, so a padded value took a different branch in
    each."""
    return (value or "").strip().lower()


def _leading_year(text: str) -> int | None:
    m = _YEAR_RUN_RE.search(text)
    if m is None:
        return None
    # A run preceded by a minus is not a year: "-5" read the 5 and dropped the sign.
    if m.start() and text[m.start() - 1] == "-":
        return None
    digits = m.group(1) or m.group(2)
    return int(digits) if digits else 1


def sole_year_token(raw: str) -> str:
    """The one whole 3-4 digit run in a string, or "". Shared so the URI year and
    the stored year cannot disagree about what a raw year holds."""
    runs = re.findall(r"(?<!\d)\d{3,4}(?!\d)", raw)
    return runs[0] if len(runs) == 1 else ""


def title_year_token(title: str, number: object = "") -> str:
    """The title's one whole year run, or "" when the same digits are the
    document number: "Act No. 2010" numbered 2010 names no year."""
    token = sole_year_token(title)
    if not reads_as_a_gregorian_year(token) or token == str(number or "").strip():
        return ""
    return token


def reads_as_a_gregorian_year(value: str | int) -> bool:
    """A four-digit year in a range no era reaches. Japanese eras count to about
    64, so a number this size is a Gregorian year the model mislabelled, and
    refusing it would lose a year that is plainly right."""
    text = str(value).strip()
    return text.isdigit() and len(text) == 4 and 1000 <= int(text) <= 2999


def _folded(text: str) -> str:
    """Case- and diacritic-insensitive, so the prompt's "Showa" reaches a table
    spelling it "Shōwa"."""
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()


def _composed(text: str) -> str:
    """NFC, so a decomposed input has the same length as the prefix matched
    against it: slicing a decomposed "Shōwa" by 5 leaves "a 64"."""
    return unicodedata.normalize("NFC", text)


def _convert_era_table(local_year: str | int, rule: CalendarConversion) -> int:
    """Japanese/Chinese era table → Gregorian."""
    if not rule.eras:
        raise CalendarConversionError("era_table requires eras list")

    year_str = _composed(str(local_year).strip())

    # Try matching era name in the input (e.g., "令和4", "Reiwa 4")
    for era in rule.eras:
        era_name = era.get("name", "")
        era_abbrev = era.get("abbrev", "")
        start = _era_start_year(era)

        for prefix in [_composed(era_name), _composed(era_abbrev)]:
            if prefix and _folded(year_str).startswith(_folded(prefix)):
                num_part = year_str[len(prefix) :].strip()
                if num_part:
                    # The leading run only. Stripping every non-digit read
                    # "令和6年法律第1号" as era year 61, four decades out.
                    m = re.match(r"\d+|元|[〇一二三四五六七八九十]+", num_part)
                    if m is None:
                        raise CalendarConversionError(f"no era year in {year_str!r}")
                    numeral = m.group()
                    if numeral == "元":
                        n: int | None = 1
                    elif numeral.isdigit():
                        n = int(numeral)
                    else:
                        n = kanji_number(numeral)
                    if n is None or n < 1:
                        raise CalendarConversionError(f"no era year in {year_str!r}")
                    converted = start + n - 1
                    # An era year past its successor's first year is not that era's:
                    # Heisei 32 would be 2020, three years after Heisei ended. The
                    # boundary year itself stays valid, since Showa 64 and Heisei 1
                    # are both 1989 under the predominant-year rule.
                    successors = [
                        _era_start_year(e) for e in rule.eras if _era_start_year(e) > start
                    ]
                    if successors and converted > min(successors):
                        raise CalendarConversionError(f"{year_str!r} is past the end of that era")
                    return converted
                # The era alone names no year: "Reiwa" is not Reiwa 1.
                raise CalendarConversionError(f"no era year in {year_str!r}")

    # A bare number is an era-local year in the current era, which is the only
    # era it can be. A name this table does not carry is not: assuming the latest
    # era there turned 昭和64 into 2082, so refuse rather than answer confidently.
    if not year_str.lstrip("-").isdigit():
        raise CalendarConversionError(f"no era in this jurisdiction's table matches {year_str!r}")
    latest = max(rule.eras, key=_era_start_year)
    return _era_start_year(latest) + _coerce_int(local_year) - 1
