"""Stage helpers shared by the event-generator pipeline and the durable ingest workflow."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

import structlog

from codify.calendar import (
    ERA_NAMED_CALENDARS,
    CalendarConversionError,
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
from codify.frbr import (
    UNKNOWN_YEAR,
    UncitableFrbrUri,
    identity_from_title,
    law_number_token,
    series_number,
)
from codify.jurisdictions import (
    JurisdictionConfig,
    load_config,
    placeholder_statuses_for_code,
)
from codify.lang import normalise_digits, to_iso639_3
from codify.pipeline.enrich.akn_meta import normalise_akn_meta
from codify.pipeline.enrich.amendments import lift_amendment_markup
from codify.pipeline.enrich.asides import emit_marginal_notes
from codify.pipeline.enrich.cobalt import enrich_akn
from codify.pipeline.enrich.conclusions import emit_conclusions
from codify.pipeline.enrich.enacting import emit_enacting_formula
from codify.pipeline.enrich.hcontainers import postprocess_hcontainers
from codify.pipeline.enrich.inline_markup import emit_inline_markup
from codify.pipeline.enrich.metadata import number_from_title
from codify.pipeline.enrich.notes import emit_authorial_notes
from codify.pipeline.enrich.placeholder_status import mark_placeholder_status
from codify.pipeline.enrich.references import emit_references
from codify.pipeline.enrich.regions import (
    Region,
    RegionVocabulary,
    vocabulary_for_jurisdiction,
)

if TYPE_CHECKING:
    from codify.core.llm import LLMClient

logger = structlog.get_logger()


def resolve_year(metadata: dict[str, Any], country: str, *, title: str = "") -> str:
    """Resolve a Gregorian year for FRBR URI use. Converts non-Gregorian
    years when the metadata's `calendar` field flags one."""
    raw_year = str(metadata.get("year") or "")
    raw_date = str(metadata.get("date") or "")
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


def _fold_language(raw: str | None, source: str, jurisdiction: str | None) -> str | None:
    """to_iso639_3 that logs and returns None on unfoldable values, so junk
    LLM detections and config sentinels ("both", "both_equal") never crash
    ingest; resolution falls through to the next candidate."""
    try:
        return to_iso639_3(raw) or None
    except ValueError:
        logger.warning("language_unrecognised", raw=raw, source=source, jurisdiction=jurisdiction)
        return None


def resolve_language(metadata: dict[str, Any], cfg: Any) -> str:
    """LLM-detected language → jurisdiction authoritative → the sole config
    language. A multilingual config can't disambiguate one expression, so it
    defaults to "eng" with a warning. Warns on off-corpus detections too, without
    blocking ingest."""
    jur = getattr(cfg, "code", None)
    detected = _fold_language(
        (metadata.get("language") or "").strip().lower() or None, "metadata", jur
    )
    if detected and cfg is not None and cfg.languages and detected not in cfg.languages:
        logger.warning(
            "metadata_language_off_corpus",
            detected=detected,
            jurisdiction_languages=cfg.languages,
        )
    if detected:
        return detected
    if cfg is not None:
        if cfg.authoritative_language:
            folded = _fold_language(cfg.authoritative_language, "authoritative_language", jur)
            if folded:
                return folded
        # Config can only name the language of a single-language jurisdiction;
        # a multilingual config list is unordered and cannot stand in for a
        # given expression's language, which callers supply via metadata.
        if cfg.languages and len(cfg.languages) == 1:
            folded = _fold_language(cfg.languages[0], "config_languages", jur)
            if folded:
                return folded
        if cfg.languages:
            logger.warning(
                "language_unresolved_defaulting_eng", jurisdiction_languages=cfg.languages
            )
    return "eng"


def _local_year_to_gregorian(
    local_year: str, cfg: Any, country: str, month: int | None = None
) -> int | None:
    """A local-calendar year as Gregorian, by the jurisdiction's own rule first:
    a config may declare an epoch the calendar's name does not imply. The month
    settles a year that began mid-year and so straddles two Gregorian ones."""
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


_ISO_DATE = re.compile(r"^([0-9]{1,4})-([0-9]{2})-([0-9]{2})(?![0-9])")


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


def draft_number(source: bytes | str) -> str:
    """Content-addressed FRBR number for un-numbered documents (drafts):
    deterministic across re-ingests so expression_uri idempotency holds.
    Accepts raw bytes or an already-computed sha256 hex digest."""
    sha = source if isinstance(source, str) else hashlib.sha256(source).hexdigest()
    return "draft-" + sha[:12]


@dataclass(frozen=True)
class Descriptors:
    """Everything parse/enrich need that comes out of metadata extraction."""

    title: str
    raw_date: str
    number: str
    year: str
    language: str
    doctype: str


# The enacting formula and any recitals sit at the top of the source, so a
# prefix is enough to test a preamble rule against without waiting for the
# document to be structured.
_PREAMBLE_PREFIX_CHARS = 2000


def preamble_from_akn(akn_xml: str) -> str | None:
    """The opening matter of a stored document, for classifying a law whose
    source text is gone. A derivative of the source preamble, not the source.
    ``None`` when the stored XML will not parse, so a caller counting how often
    the preamble decided a class cannot mistake unreadable for absent."""
    from lxml.etree import XMLSyntaxError

    from codify.akn._schema import AKN_NS, parse_xml

    if not akn_xml.strip():
        return ""
    try:
        root = parse_xml(akn_xml)
    except XMLSyntaxError as exc:
        logger.warning("akn_unparseable_for_classification", error=str(exc)[:200])
        return None
    # Preamble before preface: the classification rules match recitals, and a
    # long preface would otherwise fill the cap on its own.
    parts = [
        " ".join("".join(el.itertext()).split())
        for tag in ("preamble", "preface")
        for el in root.iter(f"{{{AKN_NS}}}{tag}")
    ]
    return "\n".join(p for p in parts if p)[:_PREAMBLE_PREFIX_CHARS]


def reclassify_stored_law(
    cfg: JurisdictionConfig | None,
    *,
    title: str,
    year: int | None,
    akn_xml: str,
) -> tuple[str | None, str]:
    """The class a stored law's own metadata implies, and the signal that
    decided it. ``None`` when nothing decided it, so the caller leaves the row
    alone rather than stamping it with the jurisdiction default, which is the
    silent fallback this reclassification exists to remove."""
    if cfg is None:
        return None, "no_config"
    if not title.strip():
        return None, "no_title"
    preamble = preamble_from_akn(akn_xml)
    if preamble is None:
        return None, "akn_unparseable"
    year_s = str(year or "")
    resolved = match_document_class(cfg, title=title, raw_date="", year=year_s, source=preamble)
    if resolved is None:
        return None, "no_rule_matched"
    if not preamble:
        return resolved, "title_and_year"
    bare = match_document_class(cfg, title=title, raw_date="", year=year_s, source="")
    return resolved, "preamble" if resolved != bare else "title_and_year"


def _unambiguous_date(raw_date: str, year: str) -> date | None:
    """The document's date, or ``None`` when only a year is known. A bare year cannot place a
    document either side of a bound falling inside it, and guessing 1 January would
    classify a whole year by the regime that ended in June.
    """
    try:
        return date.fromisoformat(raw_date[:10])
    except ValueError:
        return None


def resolve_doctype(
    cfg: JurisdictionConfig | None,
    *,
    title: str,
    raw_date: str,
    year: str,
    source: bytes | str,
    requested: str | None = None,
) -> str:
    """The caller's declared class, else a classification rule, else the default.

    The caller wins: overriding it structured a civil-law code as an act, where
    the chapter numbers collide. An undeclared class is refused, not ignored.
    """
    if cfg is None:
        return requested or "act"
    if requested:
        if requested not in cfg.document_classes:
            raise ValueError(
                f"jurisdiction {cfg.code!r} declares no document class {requested!r}; "
                f"known classes: {sorted(cfg.document_classes)}"
            )
        logger.info("doctype_from_caller", country=cfg.code, doctype=requested)
        return requested
    matched = match_document_class(cfg, title=title, raw_date=raw_date, year=year, source=source)
    if matched:
        logger.info("doctype_from_classification_rule", country=cfg.code, doctype=matched)
        return matched
    return cfg.default_document_class


def match_document_class(
    cfg: JurisdictionConfig,
    *,
    title: str,
    raw_date: str,
    year: str,
    source: bytes | str,
) -> str | None:
    """The class a rule chose, or None when none matched. Separated from
    `resolve_doctype` so a caller can tell a decision from a fallback."""
    text = source.decode("utf-8", "ignore") if isinstance(source, bytes) else source
    doc_date = _unambiguous_date(raw_date, year)
    if doc_date is None and year.isdigit():
        # A year alone still settles a bound that lies outside it entirely,
        # which is how the pre-2007 and post-2007 PS rules are written.
        doc_date = _year_only_date(cfg, int(year))
    return cfg.classify_document_class(
        title=title, preamble=text[:_PREAMBLE_PREFIX_CHARS], doc_date=doc_date
    )


def _year_only_date(cfg: JurisdictionConfig, year: int) -> date | None:
    """A date standing for the whole year, or ``None`` if any rule bound falls
    inside it and the year alone cannot decide which side the document is on."""
    rules = cfg.structuring.classification_rules if cfg.structuring else []
    for rule in rules:
        for bound in (rule.date_from, rule.date_until):
            if bound is not None and bound.year == year:
                return None
    return date(year, 1, 1)


def citable_number(value: str) -> str:
    """A path-safe law number from a model answer or a title, or "". A
    `{number}/{series}/{year}` citation is reduced first, so a court case number reaches
    the guard as the number it starts with rather than being discarded whole.
    """
    return law_number_token(series_number(value) or value)


def resolve_descriptors(
    metadata: dict[str, Any],
    *,
    jurisdiction_code: str,
    source_bytes: bytes | str,
    fallback_stem: str,
    requested_doctype: str | None = None,
    classification_text: str | None = None,
) -> Descriptors:
    model_title = str(metadata.get("title", "") or "")
    title = model_title or fallback_stem
    raw_date = str(metadata.get("date", "") or "")
    number = str(metadata.get("number") or "")
    year = resolve_year(metadata, jurisdiction_code, title=title)
    cfg = load_config(jurisdiction_code)
    # Non-Gregorian raw dates stay in the local calendar; the FRBR URI year
    # must be Gregorian, so blank the date and rely on the resolved year. The
    # month and day carry over only where the calendar declares its grid is the
    # Gregorian one; on any other grid they name days of that grid.
    cal = normalise_calendar(metadata.get("calendar")) or "gregorian"
    keeps_month_day = _grid_is_gregorian(cfg)
    local_month_day = _month_day(raw_date) if cal != "gregorian" and keeps_month_day else None
    # Carried only where the date and the title name one year. A date stating
    # another year is about another document, and rebuilding it on the title's
    # year would invent a date neither states.
    local_date_year = sole_year_token(normalise_digits(raw_date))
    if cal != "gregorian":
        raw_date = ""
    # The extracted text, never the source bytes: on the scanned route those are
    # the PDF file.
    source_text = classification_text if classification_text is not None else source_bytes
    source_date: date | None = None
    stated_month: int | None = None
    # Run whenever the text and a grammar allow: a field holding a non-date
    # would otherwise hide a date the document itself states.
    if isinstance(source_text, str) and declares_local_date_grammar(jurisdiction_code):
        # Deterministic, and ahead of the model, which reports the year and
        # drops the day.
        stated = local_date_from_text(source_text, jurisdiction_code)
        if stated is not None:
            source_date = stated
            raw_date = stated.isoformat()
            # The month the document states, kept for the year conversion: a
            # local year that began mid-year straddles two Gregorian ones, and a
            # work date disagreeing with the URI year is dropped downstream.
            stated_month = stated.month
    # An instrument series that numbers nothing states its identity in its title.
    title_rule = cfg.frbr.title_identity if cfg.frbr is not None else None
    identity = identity_from_title(model_title, title_rule) if title_rule else None
    # A year the model never stated came from the title, so it is local whatever
    # script its digits are in.
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
    date_echoes_title = (
        echoes_title
        and sole_year_token(normalise_digits(str(metadata.get("date") or ""))) == local_year
    )
    if (
        identity is not None
        and identity.year
        and (_year_int(year) is None or not stated_by_model or echoes_title)
    ):
        # The month of whichever date the document gave, the source's or the
        # model's, since a year that began mid-year needs one to settle.
        # The month of whichever date the document gave. A local month settles
        # the conversion whether or not its grid matches the Gregorian one.
        month = stated_month
        if month is None:
            month = month_stating_this_year(metadata, identity.year) or _month_of(raw_date)
        converted = _local_year_to_gregorian(
            identity.year,
            cfg,
            jurisdiction_code,
            month=month if date_echoes_title else stated_month,
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
        stated_local = sole_year_token(normalise_digits(str(metadata.get("date") or "")))
        converted = (
            _local_year_to_gregorian(stated_local, cfg, jurisdiction_code, local_month_day[0])
            if stated_local
            else None
        )
        if converted is not None and _year_int(str(converted)):
            year = str(converted)
            raw_date = _built_date(int(year), local_month_day)
    if source_date is not None and not converted_from_title:
        # An exact date off the document settles the year nothing else converted.
        year = str(source_date.year)
    if _year_int(year) is None and raw_date:
        # Without this the URI takes the unknown-year placeholder while the
        # document carries its own date.
        year = raw_date[:4] if _year_int(raw_date[:4]) is not None else year
    doctype = resolve_doctype(
        cfg,
        title=title,
        raw_date=raw_date,
        year=year,
        source=classification_text if classification_text is not None else source_bytes,
        requested=requested_doctype,
    )
    document_class = cfg.get_document_class(doctype)
    title_for_number = model_title
    if document_class and document_class.number_has_year_prefix:
        # Compact only numeric year/serial typography for this declared class.
        serial_pattern = r"(?<![\w-])([0-9]{4})\s*[-–]\s*([0-9]+)(?![\w-])"
        number = re.sub(serial_pattern, r"\1-\2", number)
        title_for_number = re.sub(serial_pattern, r"\1-\2", model_title)
    # The model sometimes returns the whole parenthetical a number sat in, routing
    # code and all. An unusable number is no number: fall through to the
    # content-addressed fallback rather than formatting it into an uncitable path.
    #
    # The title is a second source, as `date` is for the year, but only a title the
    # model actually read qualifies: a filename stem is not evidence of a number, and
    # an amending act's short title states the number of the act it amends.
    # The generic inference reads only the number inside a title, so every second
    # edition of a numberless series would collide. A stated number still wins.
    from_title = (
        ""
        if metadata.get("is_amendment") or identity is not None
        else number_from_title(title_for_number)
    )
    number = citable_number(number) or citable_number(from_title)
    # `4/2016` is a fine citation and a bad path segment: as the document's own
    # number it would split the FRBR path in two. A cited number keeps its
    # slash; this one cannot.
    number = number.replace("/", "-")
    if not number and identity is not None:
        number = identity.slug
    if not number:
        number = draft_number(source_bytes)
    if document_class and document_class.number_has_year_prefix:
        prefix, separator, serial = number.partition("-")
        if separator and prefix == year and serial.isascii() and serial.isdecimal():
            number = serial
    language = resolve_language(metadata, cfg)
    return Descriptors(
        title=title,
        raw_date=raw_date,
        number=number,
        year=year,
        language=language,
        doctype=doctype,
    )


async def run_enrich_passes(
    akn_xml: str,
    *,
    llm: LLMClient,
    jurisdiction_code: str,
    desc: Descriptors,
    on_pass: Callable[[str], None] | None = None,
    skip_external_refs: bool = False,
    regions: dict[int, list[Region]] | None = None,
    work_uri: str | None = None,
) -> str:
    """The enrich sequence, each pass independent; failures log and skip so partial
    enrichment still ships a valid Document. `work_uri` pins identity to one the caller
    already holds, for a re-read of a stored law; absent it, cobalt derives as before.
    """
    try:
        vocab = vocabulary_for_jurisdiction(jurisdiction_code, desc.year)
    except Exception as exc:  # noqa: BLE001
        # A bad config pattern costs the two region passes, not the sequence.
        logger.warning("region_vocabulary_failed", jurisdiction=jurisdiction_code, error=str(exc))
        vocab = RegionVocabulary()
    sync_passes: list[tuple[str, Callable[[str], str]]] = [
        (
            "cobalt",
            lambda x: enrich_akn(
                x,
                title=desc.title,
                country=jurisdiction_code,
                doctype=desc.doctype,
                year=desc.year,
                number=desc.number,
                work_uri=work_uri,
            ),
        ),
        # After cobalt, which rewrites `FRBRWork/FRBRdate` from the year-only work URI
        # and would undo this. `expression_undated` because nothing on this lane reads
        # an expression date off the source, so cobalt writes the day we ran.
        (
            "akn_meta",
            lambda x: normalise_akn_meta(x, work_date=desc.raw_date, expression_undated=True),
        ),
        # The same markers the row flag reads, so a unit the pool excludes says so
        # in the document too.
        (
            "placeholders",
            lambda x: mark_placeholder_status(x, placeholder_statuses_for_code(jurisdiction_code)),
        ),
        ("amendments", lift_amendment_markup),
        (
            "enacting",
            lambda x: emit_enacting_formula(
                x, jurisdiction_code, desc.doctype, desc.raw_date or None
            ),
        ),
        ("hcontainers", lambda x: postprocess_hcontainers(x, jurisdiction_code, desc.doctype)),
        ("references", lambda x: emit_references(x, jurisdiction_code)),
        # Both read regions and run before external refs, so a lifted note is
        # never linkified as though it were provision text.
        ("conclusions", lambda x: emit_conclusions(x, vocab=vocab, regions=regions)),
        ("notes", lambda x: emit_authorial_notes(x, vocab=vocab, regions=regions)),
        ("asides", lambda x: emit_marginal_notes(x, regions=regions)),
    ]
    for name, fn in sync_passes:
        try:
            akn_xml = fn(akn_xml)
        except UncitableFrbrUri:
            # Every other pass is decoration this document can ship without.
            # An identity nobody can cite is the document, so it propagates.
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("enrich_pass_failed", pass_name=name, error=str(exc))
            continue
        if on_pass is not None:
            on_pass(name)

    try:
        akn_xml = await emit_inline_markup(
            akn_xml,
            jurisdiction_code,
            desc.doctype,
            llm,
            skip_external_refs=skip_external_refs,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich_pass_failed", pass_name="inline_markup", error=str(exc))  # noqa: S106
    else:
        if on_pass is not None:
            on_pass("inline_markup")
    return akn_xml


__all__ = [
    "Descriptors",
    "citable_number",
    "draft_number",
    "resolve_descriptors",
    "match_document_class",
    "preamble_from_akn",
    "reclassify_stored_law",
    "resolve_doctype",
    "resolve_language",
    "resolve_year",
    "run_enrich_passes",
]
