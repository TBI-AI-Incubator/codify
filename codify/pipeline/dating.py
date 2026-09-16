"""One year-and-date resolution. The URI year, the work date and the stored year
are three readings of one answer, and resolved apart they drifted."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, NamedTuple

import structlog

from codify.calendar import (
    ERA_NAMED_CALENDARS,
    CalendarConversionError,
    canonical_year_and_date,
    declares_local_date_grammar,
    labelled_year_as_gregorian,
    local_date_from_text,
    month_stating_this_year,
    normalise_calendar,
    reads_as_a_gregorian_year,
    reform_shift,
    sole_year_token,
    title_year_as_gregorian,
    title_year_token,
    to_gregorian_year,
    year_from_calendar,
)
from codify.frbr import UNKNOWN_YEAR, identity_from_title
from codify.jurisdictions import try_load_config
from codify.lang import normalise_digits

logger = structlog.get_logger()

_ISO_DATE = re.compile(r"^([0-9]{1,4})-([0-9]{2})-([0-9]{2})(?![0-9])")


class Dating(NamedTuple):
    """One document's year and date: the URI segment, the work date, and the
    same year as a number for storage."""

    uri_year: str
    raw_date: str
    stored_year: int | None


def _local_year_to_gregorian(
    local_year: str, cfg: Any, country: str, month: int | None = None
) -> int | None:
    """A local year as Gregorian, by the jurisdiction's own rule first: a config
    may declare an epoch the calendar's name does not imply. The month settles."""
    rule = cfg.frbr.calendar_conversion if cfg is not None and cfg.frbr is not None else None
    if rule is None:
        return year_from_calendar(local_year, getattr(cfg, "calendar", ""), country)
    try:
        converted = to_gregorian_year(local_year, country, month=month)
    except (CalendarConversionError, LookupError):
        logger.warning("local_year_unconverted", country=country, raw=local_year)
        return None
    if month is None:
        return converted
    return converted + reform_shift(
        rule, int(sole_year_token(normalise_digits(local_year)) or 0), month
    )


def _grid_is_gregorian(cfg: Any) -> bool:
    """Whether the jurisdiction declares its months and days are the Gregorian
    ones, so only a year needs converting."""
    rule = cfg.frbr.calendar_conversion if cfg is not None and cfg.frbr is not None else None
    return bool(rule is not None and rule.month_day_is_gregorian)


def _month_day(raw_date: str) -> tuple[int, int] | None:
    """The month and day an ISO date names, read without its year: 29 February
    is a day of one calendar's leap year and not the other's."""
    found = _ISO_DATE.match(normalise_digits(raw_date))
    if found is None:
        return None
    month, day = int(found.group(2)), int(found.group(3))
    return (month, day) if 1 <= month <= 12 and 1 <= day <= 31 else None


def _month_of(raw_date: str) -> int | None:
    """The month an ISO date names, or None."""
    parts = _month_day(raw_date)
    return parts[0] if parts else None


def _built_date(gregorian_year: int, month_day: tuple[int, int]) -> str:
    """The date, or "" when the parts do not form one on that year."""
    try:
        return date(gregorian_year, *month_day).isoformat()
    except ValueError:
        return ""


def _rebased_date(raw_date: str, gregorian_year: int) -> str:
    """`raw_date` on `gregorian_year`, or "" when the two do not form a date.
    Keeping it would leave a local date beside a converted year."""
    parts = _month_day(raw_date)
    return _built_date(gregorian_year, parts) if parts is not None else ""


def _year_int(year: str) -> int | None:
    """The year as a URI segment carries it, or None. Four ASCII digits exactly,
    the two sentinels `is_citable_work_uri` refuses excluded."""
    if len(year) != 4 or not year.isascii() or not year.isdecimal():
        return None
    return None if year in (UNKNOWN_YEAR, "0000") else int(year)


def _stored(uri_year: str) -> int | None:
    """The URI year as a number, or None where it is not a whole run of ASCII
    digits: an unconverted local year reaches here as the text that it is."""
    return int(uri_year) if uri_year.isascii() and uri_year.isdecimal() else None


def _year_from_fields(metadata: dict[str, Any], country: str, title: str) -> str:
    """The year the model's own fields state, converted where it labelled them
    local, or the one the title states when they state none."""
    raw_year, raw_date = canonical_year_and_date(metadata)
    cal = normalise_calendar(metadata.get("calendar"))
    candidate = raw_year or (raw_date.split("-")[0] if "-" in raw_date else raw_date)
    if candidate and cal and cal != "gregorian":
        converted = labelled_year_as_gregorian(
            candidate, cal, country, month_stating_this_year(metadata, candidate)
        )
        if converted is not None:
            return str(converted)
        if cal in ERA_NAMED_CALENDARS and not reads_as_a_gregorian_year(candidate):
            # The refusal has to reach the URI, or the number it declined to
            # read is filed as the year anyway.
            logger.warning(
                "year_calendar_unconverted", raw=candidate, calendar=cal, country=country
            )
            return ""
    if raw_year:
        # Whole runs only, digits or not: "12024" is not a five-digit year.
        if raw_year.isdigit() and sole_year_token(raw_year):
            return raw_year
        # One whole 3-4 digit run is a year; anything else is not.
        digits = sole_year_token(raw_year)
        logger.warning(
            "year_calendar_unconverted", raw=raw_year, calendar=cal, country=country, year=digits
        )
        return digits
    if raw_date:
        # Converted only where the model said the date is local. A date it called
        # Gregorian, or left unlabelled, is taken as written: converting one
        # again puts a year no document states in the URI.
        if cal and cal != "gregorian":
            try:
                return str(to_gregorian_year(raw_date.split("-")[0], country))
            except Exception:  # noqa: BLE001, S110
                pass
        if "-" in raw_date:
            return raw_date.split("-")[0]
    if title:
        # The title of a document in a local calendar states a local year.
        token = title_year_token(title, metadata.get("number"))
        return title_year_as_gregorian(token, country) if token else token
    return ""


def resolve_dating(
    metadata: dict[str, Any],
    *,
    country: str,
    title: str = "",
    source_text: str = "",
) -> Dating:
    """The year and date one document is filed under: the model's fields, a date
    the source states and a title grammar, read in one place for every caller."""
    model_title = str(metadata.get("title") or "")
    cfg = try_load_config(country) if country else None
    year = _year_from_fields(metadata, country, model_title or title)
    raw_date = canonical_year_and_date(metadata)[1]
    # Non-Gregorian raw dates stay in the local calendar; the FRBR URI year
    # must be Gregorian, so blank the date and rely on the resolved year. The
    # month and day carry over only where the calendar declares its grid is the
    # Gregorian one; on any other grid they name days of that grid.
    stated_date = raw_date
    cal = normalise_calendar(metadata.get("calendar")) or "gregorian"
    keeps_month_day = _grid_is_gregorian(cfg)
    local_month_day = _month_day(raw_date) if cal != "gregorian" and keeps_month_day else None
    # Carried only where the date and the title name one year. A date stating
    # another year is about another document, and rebuilding it on the title's
    # year would invent a date neither states.
    local_date_year = sole_year_token(normalise_digits(raw_date))
    if cal != "gregorian":
        raw_date = ""
    source_date: date | None = None
    stated_month: int | None = None
    # Run whenever the text and a grammar allow: a field holding a non-date
    # would otherwise hide a date the document itself states.
    if source_text and declares_local_date_grammar(country):
        # Deterministic, and ahead of the model, which reports the year and
        # drops the day.
        stated = local_date_from_text(source_text, country)
        if stated is not None:
            source_date = stated
            raw_date = stated.isoformat()
            # The month the document states, kept for the year conversion: a
            # local year that began mid-year straddles two Gregorian ones, and a
            # work date disagreeing with the URI year is dropped downstream.
            stated_month = stated.month
    # An instrument series that numbers nothing states its identity in its title.
    title_rule = cfg.frbr.title_identity if cfg is not None and cfg.frbr is not None else None
    identity = identity_from_title(model_title, title_rule) if title_rule else None
    # Stated, not merely present: a field naming no year run says nothing, and
    # blocking the conversion on it leaves a local year in the URI.
    stated_by_model = any(
        sole_year_token(normalise_digits(str(metadata.get(field) or "")))
        for field in ("year", "date")
    )
    # A model year whose run equals the title's is that local year read twice.
    # Compared on the run, since a model decorates the field with the era, and on
    # the date too: a model dating a document in the local calendar while calling
    # it Gregorian states the same local year in another field.
    model_runs = {
        sole_year_token(normalise_digits(str(metadata.get(field) or "")))
        for field in ("year", "date")
    }
    converted_from_title = False
    local_year = identity.year if identity is not None else ""
    echoes_title = bool(local_year) and local_year in model_runs
    date_echoes_title = echoes_title and sole_year_token(stated_date) == local_year
    if (
        identity is not None
        and identity.year
        and (_year_int(year) is None or not stated_by_model or echoes_title)
    ):
        # The month of whichever date the document gave. A local month settles
        # the conversion whether or not its grid matches the Gregorian one.
        # Only where converting with it lands on the date the source states:
        # a cue date naming another year dates another document.
        source_month = stated_month
        if source_date is not None and stated_month is not None:
            with_month = _local_year_to_gregorian(identity.year, cfg, country, month=stated_month)
            source_month = stated_month if with_month == source_date.year else None
        month = source_month
        if month is None:
            month = month_stating_this_year(metadata, identity.year) or _month_of(raw_date)
        converted = _local_year_to_gregorian(
            identity.year, cfg, country, month=month if date_echoes_title else source_month
        )
        # Through the gate the metadata year passes: a three-digit local year,
        # or a larger offset, gives a number no URI can carry. Cleared when it
        # does not, or the unconverted local value stays and reaches the URI.
        year = str(converted) if converted is not None and _year_int(str(converted)) else ""
        converted_from_title = bool(year)
        # Never over a date read off the document itself, which is exact.
        if source_date is None and year:
            if local_month_day is not None and local_date_year == identity.year:
                # Local by its own label; only its year needed converting.
                raw_date = _built_date(int(year), local_month_day)
            elif date_echoes_title:
                # Local without saying so. On any other month grid the parts name
                # days of that grid, so there is nothing to rebase.
                raw_date = _rebased_date(raw_date, int(year)) if keeps_month_day else ""
    if local_month_day is not None and not converted_from_title and source_date is None:
        # A date the model labelled local states its own year, and nothing else
        # converts it where no title grammar is declared.
        stated_local = sole_year_token(stated_date)
        converted = (
            _local_year_to_gregorian(stated_local, cfg, country, local_month_day[0])
            if stated_local
            else None
        )
        if converted is not None and _year_int(str(converted)):
            # The date's own year names the date. It names the document only
            # where no field already did, or one metadata would answer twice.
            raw_date = _built_date(converted, local_month_day)
            if _year_int(year) is None:
                year = str(converted)
    if source_date is not None and not converted_from_title:
        # An exact date off the document settles the year nothing else converted.
        year = str(source_date.year)
    if _year_int(year) is None and raw_date:
        # Without this the URI takes the unknown-year placeholder while the
        # document carries its own date.
        year = raw_date[:4] if _year_int(raw_date[:4]) is not None else year
    return Dating(year, raw_date, _stored(year))


__all__ = ["Dating", "resolve_dating"]
