"""Jurisdiction configuration, machine-readable profiles for the pipeline."""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import unicodedata
from datetime import date
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

import structlog
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

from codify.data_paths import data_dir

# Atlas models live in a sibling module. The three container models are used by
# JurisdictionConfig's annotations; the rest are re-exports so
# `from codify.jurisdictions import <name>` keeps resolving.
from .jurisdictions_atlas import (
    BindingRegime,
    BindingRegimeKind,  # noqa: F401  (re-export)
    DirectEffectLevel,  # noqa: F401  (re-export)
    MembershipStatus,  # noqa: F401  (re-export)
    ReportingCadence,  # noqa: F401  (re-export)
    ResidualLaw,
    SupranationalMembership,
    SuspensionPeriod,  # noqa: F401  (re-export)
    canonicalise_body,  # noqa: F401  (re-export)
)

logger = structlog.get_logger()

# _STRICT for models the pipeline reads at inference time; _LOOSE for
# documentation-carrier models that aggregate jurisdiction metadata.
_STRICT = ConfigDict(extra="forbid", populate_by_name=True)
_LOOSE = ConfigDict(extra="allow", populate_by_name=True)


JURISDICTIONS_DIR = data_dir(__file__, "jurisdictions")


Tradition = Literal[
    "civil_law",
    "common_law",
    "islamic",
    "customary",
    "hindu",
    "confucian",
    "jewish",
    "socialist",
    "sui_generis",
]
Calendar = Literal[
    "gregorian",
    "lunar_hijri",
    "solar_hijri",
    "ethiopian",
    # Not one "imperial era": these convert differently. Minguo is year + 1911;
    # a Japanese era year needs the era name, since Showa 5 is not Reiwa 5.
    "japanese_era",
    "minguo",
    "buddhist_era",
    "dual",
]
HierarchyLevel = Literal["higher", "basic", "subdivision", "grouping", "presentational"]
# How a level is recognised in the text when it carries no hierarchy keyword.
# `caption`: a closed vocabulary of heading lines. `bracketed_decimal`: "[1.1]".
MarkerForm = Literal[
    "caption",
    "bracketed_decimal",
    # Outline markers, for annex content that numbers itself without a keyword:
    # "A. Latar Belakang", "1. RKUPH", "2) Penilaian".
    "upper_letter_period",
    "upper_roman_period",
    "lower_letter_period",
    "arabic_period",
    "alphanumeric_period",
    "arabic_closing_paren",
    "lower_letter_closing_paren",
    # The operative subdivision of an article, printed as "(1) " with no
    # keyword of its own.
    "parenthesized_arabic",
    # A body container headed by a bare Roman numeral and an all-caps title,
    # "II. ORCHARD COUNCILS", taken only in an I, II, III sequence.
    "upper_roman_title",
]
ValidationStatus = Literal["production", "draft", "stub"]
JurisdictionType = Literal["national", "supranational", "subnational", "crown_dependency"]

# AKN 3.0 hierarchical element names, restricted to those actually used
# across the corpus. A few less-common but valid ones (preamble, recital)
# are structural wrappers, not hierarchy members, so they're not here.
AknElement = Literal[
    "book",
    "part",
    "title",
    "subtitle",
    "tome",
    "chapter",
    "subchapter",
    "section",
    "subsection",
    "division",
    "subdivision",
    "subpart",
    "article",
    "paragraph",
    "subparagraph",
    "clause",
    "subclause",
    "point",
    "indent",
    "alinea",
    "rule",
    "subrule",
    "item",
    "attachment",
    "hcontainer",
    "proviso",
]

# Bluebell keyword set sourced from the akn-ontology skill + corpus usage.
BluebellKeyword = Literal[
    "BOOK",
    "PART",
    "TITLE",
    "SUBTITLE",
    "TOME",
    "CHAPTER",
    "SUBCHAPTER",
    "SECTION",
    "SUBSECTION",
    "DIVISION",
    "SUBDIVISION",
    "SUBPART",
    "ARTICLE",
    "PARAGRAPH",
    "SUBPARAGRAPH",
    "CLAUSE",
    "SUBCLAUSE",
    "POINT",
    "INDENT",
    "ALINEA",
    "RULE",
    "SUBRULE",
    "PROVISO",
    "TRANSITIONAL",
    "CROSSHEADING",
    "PART_HEADING",
]

# AKN 3.0 TLC classes (all 10, per the ontology).
TlcClass = Literal[
    "TLCPerson",
    "TLCOrganization",
    "TLCRole",
    "TLCObject",
    "TLCConcept",
    "TLCEvent",
    "TLCLocation",
    "TLCProcess",
    "TLCTerm",
    "TLCReference",
]


class HierarchyEntry(BaseModel):
    model_config = _LOOSE

    local_term: str = Field(
        description=(
            'The level\'s name as the source prints it, such as "Article" or "Chapter". '
            "Marker detection and the eId builder match it in the text."
        )
    )
    akn_element: AknElement = Field(
        description=("The AKN element this level becomes, such as `article` or `chapter`.")
    )
    level: HierarchyLevel = Field(
        description=(
            "Where the level sits: `higher` containers, the single `basic` unit and its "
            "`subdivision`s are ordered; `grouping` and `presentational` levels may appear "
            "anywhere."
        )
    )
    bluebell_keyword: BluebellKeyword | None = Field(
        default=None,
        description=(
            "The Bluebell keyword for this level. Unset uses the AKN element name in capitals."
        ),
    )
    numbering: str | None = Field(
        default=None,
        description=(
            "How the level is numbered, such as arabic or roman. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    eid_abbrev: str = Field(
        default="",
        description=(
            "Overrides the element's canonical eId prefix. Set it only where the element cannot"
            " imply one (an hcontainer, or two local terms on one element) or where the "
            "publisher's own AKN uses another prefix."
        ),
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    local_terms: dict[str, str] | None = Field(
        default=None,
        description=(
            "The level's local names, keyed by ISO 639-3 language code, or by \"script:<ISO "
            '15924 code>" for a script variant.'
        ),
    )
    akn_name: str | None = Field(
        default=None,
        description=(
            "The element name a publisher's own AKN uses in hyphenated eIds for this level "
            "(`section` in `section-55-2`), where it differs from `akn_element`."
        ),
    )
    marker_form: MarkerForm | None = Field(
        default=None,
        description=(
            "For genres whose levels carry no keyword, such as a judgment that numbers its "
            'paragraphs "[1.1]" and never names the level. Unset keeps keyword matching.'
        ),
    )
    captions: list[str] = Field(
        default_factory=list,
        description="The closed caption vocabulary a `caption` level answers to.",
    )

    @model_validator(mode="after")
    def _captions_match_marker_form(self) -> "HierarchyEntry":
        # A caption level with no vocabulary matches nothing, and the keyword
        # path is already off, so the level would vanish with no error.
        if self.marker_form == "caption" and not [c for c in self.captions if c.strip()]:
            raise ValueError(
                f"hierarchy level {self.local_term!r} is caption-marked but declares no captions"
            )
        if self.marker_form != "caption" and self.captions:
            raise ValueError(
                f"hierarchy level {self.local_term!r} declares captions "
                "without marker_form='caption'"
            )
        return self


def _check_hierarchy_levels(where: str, hierarchy: list[HierarchyEntry]) -> None:
    """One basic level, and no higher level after a basic or subdivision one.

    Only `higher`, `basic` and `subdivision` order the sequence; `grouping` and
    `presentational` are orthogonal and may appear anywhere."""
    basic = [h for h in hierarchy if h.level == "basic"]
    if len(basic) > 1:
        raise ValueError(
            f"{where}: {len(basic)} hierarchy entries with "
            f"level=basic (expected at most 1): {[h.local_term for h in basic]}"
        )
    seen_basic = seen_subdivision = False
    for h in hierarchy:
        if h.level == "higher" and (seen_basic or seen_subdivision):
            raise ValueError(
                f"{where}: higher-level entry '{h.local_term}' "
                f"appears after a basic/subdivision entry"
            )
        seen_basic = seen_basic or h.level == "basic"
        seen_subdivision = seen_subdivision or h.level == "subdivision"


class HContainer(BaseModel):
    model_config = _LOOSE

    local_term: str = Field(
        description=(
            "Heading text that identifies the container: a proxy element whose heading starts "
            "with it, ignoring case, is renamed to an `hcontainer`."
        )
    )
    name: str = Field(description=("The `name` attribute the resulting `hcontainer` carries."))
    description: str = Field(
        default="",
        description=(
            "Free-text description of the container. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    bluebell_proxy: BluebellKeyword = Field(
        default="SUBSECTION",
        description=(
            "The Bluebell keyword the structurer uses for this container before it is renamed."
        ),
    )
    requires_postprocessing: bool = Field(
        default=True,
        description=(
            "Whether the proxy element is renamed to an `hcontainer` after structuring. False "
            "leaves it as the proxy element."
        ),
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    numbering: str | None = Field(
        default=None,
        description=(
            "How the container is numbered. Recorded for reference; the pipeline does not read it."
        ),
    )
    label: str | None = Field(
        default=None,
        description=(
            "Display label for the container. Recorded for reference; the pipeline does not "
            "read it."
        ),
    )
    akn_element: AknElement | None = Field(
        default=None,
        description=(
            "The AKN element the container corresponds to, where one exists. Recorded for "
            "reference; the pipeline does not read it."
        ),
    )
    level: str | None = Field(
        default=None,
        description=(
            "Where the container sits in the hierarchy. Recorded for reference; the pipeline "
            "does not read it."
        ),
    )
    position: str | None = Field(
        default=None,
        description=(
            "Where the container appears in the document. Recorded for reference; the pipeline "
            "does not read it."
        ),
    )
    bluebell_keyword: BluebellKeyword | None = Field(
        default=None,
        description=(
            "A Bluebell keyword for the container. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )


class DocumentClass(BaseModel):
    model_config = _LOOSE

    label: str = Field(description=('Display name of the class, such as "Act" or "Regulation".'))
    short_label: str | None = Field(
        default=None,
        description=(
            "Compact label for the class in the interface and in PDF exports. Falls back to the"
            " title-cased doctype when unset."
        ),
    )
    akn_element: str = Field(
        default="act",
        description=(
            "The AKN document element for the class, such as `act`. `doc` or `statement` "
            "selects an open structure rather than the legislative hierarchy."
        ),
    )
    bluebell_compatible: bool = Field(
        default=True,
        description=(
            "Whether the class can be expressed in Bluebell. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    basic_unit: str = Field(
        default="section",
        description=(
            "The element that carries the class's numbered provisions, such as `section` or "
            "`article`. It is given to the structurer and used by the anchor coverage check."
        ),
    )
    number_has_year_prefix: bool = Field(
        default=False,
        description=(
            "True where a printed serial may open with the instrument's year; a matching year "
            "prefix is kept out of the URI number."
        ),
    )
    number_source: Literal["stated", "title_identity"] = Field(
        default="stated",
        description=(
            '"title_identity" makes the URI number the title digest, never a stated serial, for'
            ' classes whose serials repeat across issuers. "stated" uses the printed serial.'
        ),
    )
    hierarchy: list[HierarchyEntry] = Field(
        default_factory=list,
        description=(
            "The class's structural levels. Higher levels must come before the basic unit and "
            "its subdivisions."
        ),
    )
    hcontainers: list[HContainer] = Field(
        default_factory=list,
        alias="hcontainers",
        description=(
            "Containers with no AKN element of their own, structured under a proxy keyword and "
            "renamed to `hcontainer` afterwards."
        ),
    )
    direct_effect: bool | None = Field(
        default=None,
        description=(
            "Whether instruments of this class apply without domestic transposition. Only a "
            "supranational jurisdiction may set it to true."
        ),
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    extends: str | None = Field(
        default=None,
        description=(
            "Another class of this jurisdiction whose fields this one lays over. One level."
        ),
    )
    search_rank: int | None = Field(
        default=None,
        description=(
            "Rank of the class in the legal hierarchy, used to order a title search: higher "
            "outranks lower on a close match, and unset means no weighting. Ranks: 4 "
            "constitutional, 3 primary legislation, 2 executive decree, 1 subordinate "
            "regulation or decision, 0 implementing instructions."
        ),
    )
    akn_subtype: str | None = Field(
        default=None,
        description=(
            "AKN subtype for the class. Recorded for reference; the pipeline does not read it."
        ),
    )
    local_label: str | None = Field(
        default=None,
        description=(
            "The class's name in the local language. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    prompt_variant: str | None = Field(
        default=None,
        description=(
            "Name of a structuring prompt variant for the class. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    frbr_subtype: str | None = Field(
        default=None,
        description=(
            "FRBR subtype of the class's works, returned by the MCP `get_jurisdiction` tool. "
            "URI minting does not read it: it uses the doctype or `uri_patterns`."
        ),
    )
    authoritative_language: str | None = Field(
        default=None,
        description=(
            "Authoritative language for this class, for legal systems whose classes are enacted"
            " in different languages. Recorded for reference; the pipeline reads only the "
            "jurisdiction's."
        ),
    )


#: The Gregorian month each kind's new year falls in when the config names none.
_DEFAULT_NEW_YEAR_MONTH = {"bikram_samvat": 4, "ethiopian": 9}


class CalendarConversion(BaseModel):
    """Deterministic conversion rule for non-Gregorian local calendars.

    kind selects which optional fields apply:

    - epoch_offset  single-epoch calendars. Gregorian = local_year + epoch_year,
                    where epoch_year is an integer offset (Buddhist Era -543).
    - era_table     named era segments with boundaries.
    - hijri_lunar   Lunar calendar, ~354 days. Needs codify.calendar, not an offset.
    - hijri_solar   Solar calendar, Gregorian year length.
    - ethiopian     ~7-8 year offset, 13-month year.
    - buddhist      Buddhist Era calendar, epoch_year -543.
    - bikram_samvat 57-year offset, month-sensitive (Chaitra straddles the
                    mid-April new year).
    """

    model_config = _STRICT

    kind: Literal[
        "epoch_offset",
        "era_table",
        "hijri_lunar",
        "hijri_solar",
        "ethiopian",
        "buddhist",
        "bikram_samvat",
    ] = Field(
        description=("Which conversion applies; it selects the optional fields that are read.")
    )
    epoch_year: int | None = Field(
        default=None,
        description=(
            "Integer added to the local year to give the Gregorian year (for example -543 for "
            "the Buddhist calendar, or 1911 where year 1 is 1912 CE). None for calendars "
            "without a fixed offset, such as hijri, ethiopian, era_table and bikram_samvat."
        ),
    )
    eras: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            'Era table for an era-based calendar, for example [{"name": "Reiwa", "start": '
            '"2019-05-01"}].'
        ),
    )
    new_year_month: int | None = Field(
        default=None,
        description=(
            "Gregorian month in which the local year begins, for conversions that depend on the"
            " month (Bikram Samvat begins in mid-April)."
        ),
    )
    new_year_day: int | None = Field(
        default=None,
        description=(
            "Day the local year begins in its new-year month, for month-sensitive conversions "
            "of a date on the Gregorian grid. Unset keeps the calendar's built-in boundary, a "
            "band of days around the new year within which the day does not decide the year. "
            "A date on the calendar's own grid always uses the built-in boundary."
        ),
    )
    offset: int | None = Field(
        default=None,
        description=(
            "For month-sensitive conversions: the years to subtract from the local year before "
            "the new-year day; on or after it, `offset + 1`."
        ),
    )
    month_names: list[str] = Field(
        default_factory=list,
        description=(
            "Month names in the local calendar's own order, index 0 = month 1. Present only "
            "where a dated line is read off the source text."
        ),
    )
    month_day_is_gregorian: bool = Field(
        default=False,
        description=(
            "Whether the months and days coincide with the Gregorian ones, so only the year "
            "converts. False for any calendar with a grid of its own."
        ),
    )
    date_cues: list[str] = Field(
        default_factory=list,
        description=(
            "Phrases that introduce the date a document was made. A dated line elsewhere in "
            "the text is some other instrument's."
        ),
    )
    year_particles: list[str] = Field(
        default_factory=list,
        description='Words marking a year as belonging to this calendar ("B.E.", "A.H.").',
    )
    new_year_reform_year: int | None = Field(
        default=None,
        description=(
            "First local year that began on 1 January. Before it the year began in "
            "`new_year_month`, so a date in an earlier month falls in the next Gregorian year."
        ),
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )

    @model_validator(mode="after")
    def _date_grammar_is_usable(self) -> "CalendarConversion":
        """A blank month renumbers the calendar and a blank cue compiles to a
        pattern matching the start of every document."""
        # On the folded form, since the lookup folds its key.
        names = [n.strip().casefold() for n in self.month_names]
        if self.month_names and (not all(names) or len(set(names)) != len(names)):
            raise ValueError("month_names must be non-blank and distinct")
        if self.date_cues and not any(c.strip() for c in self.date_cues):
            raise ValueError("date_cues must carry at least one non-blank cue")
        if self.month_day_is_gregorian and self.month_names and len(self.month_names) != 12:
            # Named at all, the months of a Gregorian grid are twelve; any other
            # count renumbers them. Unnamed, the grid serves the metadata path.
            raise ValueError("month_names on a Gregorian grid must be exactly 12")
        if self.new_year_month is not None and not 1 <= self.new_year_month <= 12:
            raise ValueError("new_year_month must be a month, 1 to 12")
        if self.new_year_day is not None:
            # Against the month it will be read in: a day that month never has
            # would file every date of the month on one side.
            month = self.new_year_month or _DEFAULT_NEW_YEAR_MONTH.get(self.kind, 1)
            longest = max(calendar.monthrange(year, month)[1] for year in (2023, 2024))
            if not 1 <= self.new_year_day <= longest:
                raise ValueError(f"new_year_day must be a day of month {month}, 1 to {longest}")
        if self.new_year_reform_year is not None and self.new_year_reform_year < 1:
            # Every calendar here counts from one.
            raise ValueError("new_year_reform_year must be a year")
        if self.new_year_reform_year is not None and not self.new_year_month:
            # Without one the shift can never fire, so the field reads as set
            # and does nothing.
            raise ValueError("new_year_reform_year needs new_year_month")
        if self.kind == "era_table" and (self.month_names or self.date_cues):
            # The grammar captures a bare number, which an era table reads as a
            # year of its latest era.
            raise ValueError(
                "an era_table conversion cannot carry a date grammar (month_names, date_cues)"
            )
        return self


#: Hex characters of the digest a title-derived segment carries; six (2^24)
#: collided on ordinary titles.
SLUG_DIGEST_CHARS = 12


class TitleIdentity(BaseModel):
    """How a jurisdiction that numbers nothing derives an identity from a title.
    Every field is a literal `codify.frbr.identity_from_title` reads."""

    model_config = _STRICT

    strip_prefixes: list[str] = Field(
        default_factory=list,
        description="Document-kind words opening a short title, longest first.",
    )
    year_particles: list[str] = Field(
        default_factory=list,
        description="Words marking the year that follows as the instrument's own.",
    )

    @model_validator(mode="after")
    def _literals_are_not_blank(self) -> "TitleIdentity":
        """A blank particle matches everywhere, so any run of digits would read
        as the year and any parenthetical as an edition."""
        for name in (
            "strip_prefixes",
            "year_particles",
            "edition_markers",
            "consolidation_markers",
        ):
            if any(not v.strip() for v in getattr(self, name)):
                raise ValueError(f"{name} must not carry a blank entry")
        return self

    edition_markers: list[str] = Field(
        default_factory=list,
        description=(
            "Words inside a parenthetical naming this instrument's edition, spelling "
            "variants included; the first is canonical and is what a slug carries."
        ),
    )
    consolidation_markers: list[str] = Field(
        default_factory=list,
        description=(
            "Parenthetical words marking a re-publication; an edition number after one is "
            "the edition folded in, not this document's."
        ),
    )


class FrbrConfig(BaseModel):
    model_config = _LOOSE

    country_code: str = Field(
        description=(
            "The country segment of minted FRBR URIs: usually the jurisdiction code, different "
            "for a subdivision homed under its parent."
        )
    )
    title_identity: TitleIdentity | None = Field(
        default=None,
        description="Declared where instruments carry no number and the title is the identity.",
    )
    uri_patterns: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "FRBR work URI template per doctype, using `{year}` and `{number}`. A doctype "
            "without one gets the canonical default."
        ),
    )
    date_calendar: Calendar = Field(
        default="gregorian",
        description=(
            "The calendar the jurisdiction's FRBR dates are given in. Recorded for reference; "
            "the pipeline does not read it."
        ),
    )
    date_calendar_note: str | None = Field(
        default=None,
        description=(
            "Free-text note on `date_calendar`. Recorded for reference; the pipeline does not "
            "read it."
        ),
    )
    calendar_conversion: CalendarConversion | None = Field(
        default=None,
        description=(
            "Conversion rule for a jurisdiction whose instruments are dated in a non-Gregorian "
            "calendar. FRBR URIs always use Gregorian dates (ADR 001), and this rule drives the"
            " conversion."
        ),
    )
    note: str | None = Field(
        default=None,
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    notes: list[str] | str | None = Field(
        default=None,
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    number_format: str | dict[str, str] | None = Field(
        default=None,
        description=(
            "How instrument numbers are written. Recorded for reference; the pipeline does not "
            "read it."
        ),
    )
    number_note: str | None = Field(
        default=None,
        description=(
            "Free-text note on instrument numbering. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    number_extraction: str | dict[str, Any] | None = Field(
        default=None,
        description=(
            "How an instrument number is found in the source. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    sample_uris: list[Any] | dict[str, str] | None = Field(
        default=None,
        description=(
            "Example FRBR URIs for the jurisdiction. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )

    @model_validator(mode="after")
    def _grammars_suit_the_conversion(self) -> "FrbrConfig":
        """A title grammar captures a bare number; an era table reads one as a
        year of its latest era, so the two cannot be declared together."""
        rule = self.calendar_conversion
        if self.title_identity is not None and rule is not None and rule.kind == "era_table":
            raise ValueError("an era_table conversion cannot carry a title_identity grammar")
        return self


class SeriesCitation(BaseModel):
    """A citation naming an instrument by series number alone ("Act No. 386")."""

    model_config = _STRICT

    name: str = Field(
        description=(
            "Regex for the instrument's name or abbreviation, matched case-insensitively."
        ),
    )
    doctype: str = Field(
        description=("The doctype whose FRBR work URI a matched citation links to.")
    )

    @field_validator("name")
    @classmethod
    def _name_compiles(cls, value: str) -> str:
        # A pattern that can match nothing would cite any bare number.
        if not value.strip() or re.compile(value).match("") is not None:
            raise ValueError("series citation name must not be empty or match empty text")
        return value


class NumberingConfig(BaseModel):
    model_config = _LOOSE

    act_citation: str | None = Field(
        default=None,
        description=(
            "Citation pattern for acts. Reference resolution reads every `<class>_citation` "
            "field, and needs the default class's, to tell which instrument type a citation "
            "names."
        ),
    )
    act_citation_alt: str | None = Field(
        default=None,
        description=(
            "An alternative citation form for acts. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    insertion_style: str | None = Field(
        default=None,
        description=(
            "How inserted units are numbered. `slash_alpha` lets a marker number carry a slash "
            'and capital letter, as in "5/A".'
        ),
    )
    note: str | None = Field(
        default=None,
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    insertion_example: str | None = Field(
        default=None,
        description=(
            "An example of an inserted unit's number. Recorded for reference; the pipeline "
            "does not read it."
        ),
    )
    insertion_note: str | None = Field(
        default=None,
        description=(
            "Free-text note on how inserted units are numbered. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    article_numbering: str | None = Field(
        default=None,
        description=(
            "How articles are numbered. Recorded for reference; the pipeline does not read it."
        ),
    )
    citations: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Citation pattern per instrument type, keyed by type. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    numbers_unique_across_years: bool = Field(
        default=False,
        description=(
            "Whether an instrument number identifies a work without its year. False for most "
            "jurisdictions, which restart numbering each year; true lets a year-less citation "
            "resolve on the number alone, and is a claim about the whole series, not just the "
            "documents held."
        ),
    )
    series_citations: list[SeriesCitation] = Field(
        default_factory=list,
        description=(
            "Instruments cited by a never-reused series number and no year. Order matters: "
            "full names before abbreviations, so the full form wraps first."
        ),
    )
    series_citation_connectors: list[str] = Field(
        default_factory=lambda: [r"No\.?", r"Nos\.?", "Numbered"],
        description="Regex fragments that may sit between a series name and its number.",
    )

    @field_validator("series_citation_connectors")
    @classmethod
    def _connectors_compile(cls, value: list[str]) -> list[str]:
        for fragment in value:
            re.compile(fragment)
        return value

    @model_validator(mode="after")
    def _series_patterns_compose(self) -> "NumberingConfig":
        # Compile each name with the connectors as the inline-markup pass does,
        # so a clashing group name fails at load rather than on every run.
        connectors = "|".join(self.series_citation_connectors)
        for series in self.series_citations:
            try:
                re.compile(rf"(?:{series.name})\s+(?:{connectors})?\s*(?P<num>\d+)")
            except re.error as exc:
                raise ValueError(f"series citation {series.name!r}: {exc}") from exc
        return self


class EnactingFormula(BaseModel):
    model_config = _LOOSE

    text: str | None = Field(
        default=None,
        description=(
            "Wording injected into the preamble as the enacting formula when the source has "
            "none. Text with an unfilled template slot is never injected."
        ),
    )
    position: str = Field(
        default="preamble",
        description=(
            "Where the formula sits in the document. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    era: str = Field(
        default="",
        description=("Label of the period the formula belongs to, recorded in the ingest log."),
    )
    from_date: date | None = Field(
        default=None,
        alias="from",
        description=(
            "First date the formula applies to, inclusive. A bounded formula is never applied "
            "to a document without a date."
        ),
    )
    to_date: date | None = Field(
        default=None,
        alias="to",
        description=("First date the formula no longer applies to, exclusive."),
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    document_class: str | list[str] | None = Field(
        default=None,
        description=(
            "The doctype, or list of doctypes, the formula applies to. Unset applies it to "
            "every doctype."
        ),
    )
    language: str | None = Field(
        default=None,
        description=(
            "Language of the formula's text. Recorded for reference; the pipeline does not read it."
        ),
    )
    transliteration: str | None = Field(
        default=None,
        description=(
            "A transliteration of the formula's text. Recorded for reference; the pipeline "
            "does not read it."
        ),
    )
    instrument_type: str | None = Field(
        default=None,
        description=(
            "The instrument type the formula belongs to. Recorded for reference; the pipeline "
            "does not read it."
        ),
    )
    applies_to: list[Any] | str | None = Field(
        default=None,
        description=(
            "What the formula applies to. Recorded for reference; the pipeline does not read it."
        ),
    )
    verified: bool = Field(
        default=False,
        description=(
            "Whether the wording has been checked against source documents. A verified formula "
            "is preferred over an unverified one that also matches."
        ),
    )
    enacting_body_tlc: str | None = Field(
        default=None,
        description=(
            "eId of the TLC entry naming the enacting body. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )

    @field_validator("from_date", "to_date", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: Any) -> Any:
        # Legacy configs store "" instead of null; coerce so date parsing
        # doesn't fail on empty strings.
        if v == "":
            return None
        return v


class AmendmentConfig(BaseModel):
    model_config = _LOOSE

    style: str | None = Field(
        default=None,
        description=(
            "How the jurisdiction drafts amendments. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    phrasing: str | None = Field(
        default=None,
        description=(
            "A phrase that marks a paragraph as an amendment instruction, in addition to the "
            "built-in English cues, so its references are classed as amendments."
        ),
    )
    cross_reference_format: str | None = Field(
        default=None,
        description=(
            "How amendments cite the provisions they change. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    trigger_phrases: list[str] = Field(
        default_factory=list,
        description=(
            "Lead-in phrases marking an embedded amendment article: a number following one "
            "of these plus a colon is quoted content, not a new top-level article."
        ),
    )
    amending_body_structure: str | None = Field(
        default=None,
        description=(
            "Body shape of an amending instrument. `two_pasal_roman`: the host instrument "
            "numbers its own articles in Roman numerals, so an Arabic-numbered article after "
            "them is quoted amending text."
        ),
    )
    amendment_item_kind: str | None = Field(
        default=None,
        description=(
            "Hierarchy element that numbers each amendment an amending article makes. What "
            "follows it belongs to the amended statute, so it does not compete in the host's "
            "numbering; empty leaves the pass inert."
        ),
    )


class TLCEntry(BaseModel):
    model_config = _STRICT

    eId: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9-]*$",
        min_length=1,
        description=(
            "An NCName-compatible identifier; camelCase and hyphenated forms are both accepted,"
            " and an empty one is refused."
        ),
    )
    tlc_class: TlcClass = Field(
        alias="class",
        description=("The AKN TLC element the entry becomes, such as `TLCOrganization`."),
    )
    href: str = Field(description=("The URI the entry refers to, written to its `href` attribute."))
    showAs: str = Field(description=("The display name written to the entry's `showAs` attribute."))
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )


class ClassificationRule(BaseModel):
    """Deterministic rule for assigning a document class from the incoming header or
    metadata. Tried in descending priority; the first match wins. No match falls back to
    `default_document_class` and to whatever heuristics `StructuringConfig.prompt_additions`
    still carries.

    signal          what `pattern` is tested against
      date_range      nothing; the match is `date_from`/`date_until` alone, for laws
                      post-dating a regime change
      preamble_match  the preamble or enacting-formula text
      issuer_role     the enacting body's role (for example a head of state or a cabinet)
      gazette_series  the official gazette series the document was published in
      title_regex     the document title (a fixed prefix such as "Regulation:")
    """

    model_config = _STRICT

    signal: Literal[
        "date_range", "preamble_match", "issuer_role", "gazette_series", "title_regex"
    ] = Field(description=("Which part of the document `pattern` is tested against."))
    pattern: str = Field(
        default="", description=("Regex tested against the signal's text. Unused for `date_range`.")
    )
    pattern_flags: str = Field(
        default="",
        description='Regex flags for `pattern`, for example "i" for case-insensitive matching.',
    )
    target_document_class: str = Field(
        description=(
            "The document class assigned when the rule matches. It must be a key of "
            "`document_classes`."
        )
    )
    priority: int = Field(
        default=0,
        description=("Rules are tried from highest priority to lowest; the first match wins."),
    )
    date_from: date | None = Field(
        default=None,
        description=(
            "First document date the rule applies to, inclusive. A rule with a date bound is "
            "skipped for a document without a date."
        ),
    )
    date_until: date | None = Field(
        default=None, description=("Last document date the rule applies to, inclusive.")
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )


class PlaceholderStatus(StrEnum):
    """The AKN `@status` a placeholder unit should carry; named so the generated
    contract keeps a stable alias rather than a numbered `Status`."""

    INCOMPLETE = "incomplete"
    REMOVED = "removed"
    EDITORIAL = "editorial"


class PlaceholderMarker(BaseModel):
    """A whole-string pattern a publisher writes where content is missing or
    struck, and the AKN status the unit should carry for it."""

    model_config = _STRICT

    pattern: str = Field(description=("Regex matched against a unit's whole text."))
    status: PlaceholderStatus = Field(
        default=PlaceholderStatus.INCOMPLETE,
        description=("The AKN `status` a unit matching `pattern` carries."),
    )


class AttachmentCaption(BaseModel):
    """A caption line opening an attachment. AKN models a schedule and a
    mandatory companion alike, so legal force is declared per caption."""

    model_config = _LOOSE

    caption: str = Field(
        description=(
            "Caption that opens the attachment. Captions are centred in the source, so the "
            "match tolerates a wide indent."
        ),
    )
    normative: bool = Field(
        default=True,
        description=(
            "False for an attachment with no normative force, such as an explanatory "
            "memorandum, which cannot serve as a legal basis."
        ),
    )
    hierarchy: list[HierarchyEntry] = Field(
        default_factory=list,
        description=(
            "Levels the attachment's own content uses, since an annex can be a lettered outline"
            " rather than the article hierarchy of its body. Empty uses the body's levels, or "
            "none."
        ),
    )
    prefix: bool = Field(
        default=False,
        description=(
            'True where the caption opens a longer title on the same line ("SCHEDULE of '
            'fees…"), so the caption need only begin the line. The line is the heading.'
        ),
    )
    always_opens: bool = Field(
        default=False,
        description=(
            "True where no table in the body carries this caption, so a line that opens "
            "with it always begins the attachment."
        ),
    )


def heading_line_pattern(pattern: str) -> re.Pattern[str]:
    """A heading pattern as it runs: from a line start, past indentation."""
    return re.compile(rf"(?m)^[ \t]*(?:{pattern})")


def _min_width(pattern: str) -> int:
    """Fewest characters a match can take; zero means it can match nothing anywhere."""
    return int(re._parser.parse(pattern).getwidth()[0])  # type: ignore[attr-defined]


def _refuse_empty_match(pattern: str, what: str) -> re.Pattern[str]:
    """The compiled pattern; one that can match nothing would fire on every line."""
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{what} does not compile: {pattern!r}: {exc}") from exc
    if not pattern.strip() or _min_width(pattern) == 0:
        raise ValueError(f"{what} must not be empty or match empty text: {pattern!r}")
    return compiled


class SegmentationConfig(BaseModel):
    """Lines that open an issue or an act, for telling apart the instruments one
    source holds. Absent means the jurisdiction declares none, and nothing splits."""

    model_config = _STRICT

    act_heading_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regexes matched at a line start; a `number` group, where present, is the label."
        ),
    )
    issue_heading_patterns: list[str] = Field(
        default_factory=list,
        description="The level above acts: a gazette issue within a bound volume.",
    )
    contents_keywords: list[str] = Field(
        default_factory=list,
        description="Headings of a contents listing, matched case-insensitively at a line start.",
    )
    printed_page_pattern: str | None = Field(
        default=None,
        description=(
            "A printed page number in a page's running header or footer; its first "
            "participating group is the number."
        ),
    )
    skip_heading_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Headings of instruments that are not acts (notices, appointments): kept apart, "
            "not ingested."
        ),
    )
    trailing_section_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Whole lines that open a trailing section which is never ingested: nothing is read "
            "from there on."
        ),
    )

    @field_validator(
        "act_heading_patterns",
        "issue_heading_patterns",
        "skip_heading_patterns",
        "trailing_section_patterns",
    )
    @classmethod
    def _headings_compile(cls, value: list[str]) -> list[str]:
        for pattern in value:
            _refuse_empty_match(pattern, "a heading pattern")
            # Global flags mid-pattern fail only once wrapped, so check the wrapped form.
            try:
                heading_line_pattern(pattern)
            except re.error as exc:
                raise ValueError(
                    f"a heading pattern does not compile at a line start: {pattern!r}: {exc}"
                ) from exc
        return value

    @field_validator("contents_keywords")
    @classmethod
    def _keywords_present(cls, value: list[str]) -> list[str]:
        if any(not keyword.strip() for keyword in value):
            raise ValueError("a contents keyword must not be blank")
        return value

    @field_validator("printed_page_pattern")
    @classmethod
    def _page_pattern_compiles(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if _refuse_empty_match(value, "the printed page pattern").groups < 1:
            raise ValueError("the printed page pattern needs a group for the number")
        return value


class StructuringConfig(BaseModel):
    model_config = _LOOSE

    prompt_variant: str | None = Field(
        default=None,
        description=(
            "Name of a structuring prompt variant. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    prompt_additions: str | None = Field(
        default=None,
        description=(
            "Jurisdiction-specific guidance appended to the structurer's context, for style "
            "hints the deterministic rules cannot express."
        ),
    )
    classification_rules: list[ClassificationRule] = Field(
        default_factory=list,
        description=(
            "Deterministic classification rules, evaluated in descending priority before the "
            "model is asked. `prompt_additions` carries only style hints the rules cannot "
            "express."
        ),
    )
    marker_boundary: Literal["relaxed", "line_anchored"] = Field(
        default="relaxed",
        description=(
            "`line_anchored` where the drafting standard puts the marker on its own line "
            "and the sources keep it, so a mid-sentence citation cannot anchor."
        ),
    )
    marker_order: Literal["keyword_first", "number_first"] = Field(
        default="keyword_first",
        description=('`number_first` where the number precedes the keyword (as in "15. §").'),
    )
    prose_precursors: list[str] = Field(
        default_factory=list,
        description=(
            "Words ending the previous line when a citation wraps onto the next, which a "
            "boundary rule cannot see. Empty falls back to the script pack's list."
        ),
    )
    sameline_precursors: list[str] = Field(
        default_factory=list,
        description="Precursors that mark a citation only when adjacent on the same line.",
    )
    ordinal_words: dict[str, int] = Field(
        default_factory=dict,
        description=(
            'Ordinal words used as container numbers (as in "Part One"). Declared in the '
            "source's own casing; matching is case-sensitive."
        ),
    )
    insertion_suffixes: dict[str, str] = Field(
        default_factory=dict,
        description=(
            'Words after a marker\'s number that mark an inserted unit ("5 bis"), mapped to '
            "the eId form. One shared table, like `ordinal_words`: never blank, a number, "
            "or a bare Latin letter."
        ),
    )
    citation_successors: list[str] = Field(
        default_factory=list,
        description=(
            "Words that, following a marker's number on its own line, make the line a "
            'citation list rather than a provision ("Article 5 to Article 9 apply").'
        ),
    )
    recurring_furniture: bool = Field(
        default=False,
        description=(
            "True where the layout engine files footnotes and page-top captions as header or "
            "footer, so only text repeated across pages counts as running header or footer, and"
            " a block opening on a marker is a note."
        ),
    )

    @field_validator("insertion_suffixes")
    @classmethod
    def _suffixes_are_distinctive_words(cls, value: dict[str, str]) -> dict[str, str]:
        for word, form in value.items():
            bare_latin = len(word) == 1 and word.isascii()
            # Letters and marks only: a decoration such as `**` or `-` is never a word.
            lettered = all(unicodedata.category(ch)[0] in "LM" for ch in word)
            if not word or not lettered or bare_latin:
                raise ValueError(f"insertion suffix {word!r} is not a distinctive word")
            if not re.fullmatch(r"[a-z]+", form):
                raise ValueError(f"insertion suffix {word!r} maps to {form!r}, not an eId form")
        return value

    @model_validator(mode="after")
    def _number_first_drops_keyword_tail_options(self) -> "StructuringConfig":
        # Each reads what follows the number, where a number-first marker has its keyword.
        dropped = set(self.marker_tolerances) & {"missing_separator", "split_number"}
        if self.marker_order == "number_first" and (dropped or self.insertion_suffixes):
            raise ValueError(
                "number_first markers take no insertion_suffixes, missing_separator or split_number"
            )
        return self

    @field_validator("citation_successors", "prose_precursors", "sameline_precursors")
    @classmethod
    def _cue_words_are_not_blank(cls, value: list[str]) -> list[str]:
        if any(not word.strip() or word.strip() != word for word in value):
            raise ValueError("a cue word cannot be blank or carry surrounding whitespace")
        return value

    reference_nouns: list[str] = Field(
        default_factory=list,
        description=(
            'Words that introduce a citation\'s number ("Article 41 paragraph (3)"). A line '
            "ending in one makes the bracketed number on the next line part of the citation, "
            "not a marker opening a new provision."
        ),
    )
    body_opens_with_container: bool = Field(
        default=False,
        description=(
            "True where the drafting standard puts every article inside a container, so an "
            "article before the first one is a citation in the opening material. Some laws "
            "cite the constitution that way in the legal-basis recital."
        ),
    )
    example_markers: list[str] = Field(
        default_factory=list,
        description=(
            'Lead-ins that introduce specimen legislation in a drafting manual ("Example"), '
            "whose markers are displayed rather than enacted."
        ),
    )
    marker_tolerances: list[
        Literal["missing_separator", "keyword_glyph", "digit_glyph", "split_number"]
    ] = Field(
        default_factory=list,
        description=(
            "OCR damage the sources carry, tolerated for this jurisdiction only so parsing "
            "elsewhere is unchanged. `missing_separator` accepts a keyword run into its number,"
            ' as in "Article24".'
        ),
    )
    long_title_lead_ins: list[str] = Field(
        default_factory=list,
        description=(
            "Openings of the document's own long title (`An Act`), marked up as "
            "`<longTitle>` so the official title comes from the text rather than "
            "from a filename."
        ),
    )
    citation_lead_ins: list[str] = Field(
        default_factory=list,
        description=(
            "Phrases introducing a law's own short name (`may be cited as`), the "
            "common-law short-title device."
        ),
    )
    citation_articles: list[str] = Field(
        default_factory=list,
        description=(
            "Articles that may sit between the clause and the name (`the`). "
            "Language data: most languages have none, and no article is assumed."
        ),
    )
    citation_subjects: list[str] = Field(
        default_factory=list,
        description=(
            "How an instrument refers to itself when declaring its short name (`This Act`). "
            "Required with `citation_lead_ins`, because the same phrasing names bodies a law "
            "creates, and without it a law could take a school's or office's name as its own."
        ),
    )
    designation_rule: Literal["eu_instrument"] | None = Field(
        default=None,
        description=(
            "Derives a short name from the title's own grammar where the legal tradition "
            "declares no short title. `eu_instrument` cites an act by its form and number."
        ),
    )


class DisplayConfig(BaseModel):
    model_config = _LOOSE

    heading_type: str = Field(
        default="marginal_note",
        description=(
            "How provision headings are printed. Declaring `marginal_note` explicitly also "
            "scans for basic units whose heading is printed as a marginal note."
        ),
    )
    heading_case: str = Field(
        default="title_case",
        description=(
            "How headings are cased. Recorded for reference; the pipeline does not read it."
        ),
    )
    script: str = Field(
        default="latin",
        description=(
            "The writing system of the jurisdiction's texts, which selects the script pack "
            "used for normalisation and marker matching."
        ),
    )
    rtl: bool = Field(
        default=False,
        description=(
            "Whether the script runs right to left. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    font_preference: str = Field(
        default="serif",
        description=(
            "Preferred font style for display. Recorded for reference; the pipeline does not "
            "read it."
        ),
    )
    amendment_markers: list[str] = Field(
        default_factory=lambda: ["[", "]"],
        min_length=2,
        max_length=2,
        description=(
            "The pair of strings a publisher prints around amended words; square brackets are "
            "the commonest."
        ),
    )


class ValidationConfig(BaseModel):
    model_config = _LOOSE

    status: ValidationStatus = Field(
        default="draft",
        description=("How far the config has been validated: `production`, `draft` or `stub`."),
    )
    documents_examined: int = Field(
        default=0, description=("How many source documents the config was checked against.")
    )
    bluebell_tested: bool = Field(
        default=False,
        description=("Whether the config's structure has been tested through Bluebell."),
    )
    unresolved_ambiguities: int = Field(
        default=0, description=("How many open questions about the config remain.")
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    documents_examined_note: str | None = Field(
        default=None, description=("Free-text note on the documents examined.")
    )
    ambiguities: list[str] = Field(
        default_factory=list, description=("The open questions about the config, as free text.")
    )
    laws_africa_reference: str | bool | None = Field(
        default=None,
        description=(
            "Whether Laws.Africa covers this jurisdiction: a URL to its collection, or a bare flag."
        ),
    )


class IdentificationConfig(BaseModel):
    model_config = _LOOSE

    system: str = Field(
        default="",
        description=(
            "Name of the jurisdiction's own identifier scheme. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    pattern: str = Field(
        default="",
        description=(
            "Pattern of identifiers in that scheme. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    eli_base: str = Field(
        default="",
        description=(
            "Base URI of the jurisdiction's European Legislation Identifiers. Recorded for "
            "reference; the pipeline does not read it."
        ),
    )


# Each kind selects a concrete Acquirer; new values only when one ships.
SourceAdapterKind = Literal[
    "akn_native",  # Native AKN3 export. legislation.gov.uk, laws.africa.
    "eurlex_cellar",  # EUR-Lex Cellar (Formex).
    "html_portal",  # HTML portal, pages + linked PDFs.
    "pdf_gazette",  # PDF gazette (qbz.gov.al, consultoria.gov.do, JORF).
    "paragraf_propisi",  # Paragraf Lex slug-keyed PDFs (rs).
    "sparql",  # SPARQL endpoint (non-Cellar).
    "bulk_xml_archive",  # Bulk XML archives (USLM, Indigo dumps).
    "gazette_xml",  # Official-gazette XML per item (BOE).
    "json_portal",  # JSON document API with per-unit sections (OCS, Diário da República).
    "oecd_compendium",  # Compendium of OECD Legal Instruments (JSON API + HTML bodies).
]


# SourceAdapter kind → tier rank. Tier 1 native AKN; Tier 2 structured /
# bespoke; Tier 3 unstructured PDFs. Used by JurisdictionConfig.effective_tier.
_ADAPTER_TIER: dict[SourceAdapterKind, Literal[1, 2, 3]] = {
    "akn_native": 1,
    "eurlex_cellar": 2,
    "html_portal": 2,
    "sparql": 2,
    "bulk_xml_archive": 2,
    "gazette_xml": 2,
    "json_portal": 2,
    "oecd_compendium": 2,
    "paragraf_propisi": 3,
    "pdf_gazette": 3,
}


AknSourceFormat = Literal[
    "akn_xml",
    "akn_xml_v2",
    "bespoke_xml",
    "structured_html",
    "html",
    "pdf",
    "pdf_scanned",
    "docx",
    "rtf",
    "json",
    "csv",
    "rdf",
]

AknSourceProtocol = Literal[
    "http",
    "rest",
    "odata",
    "sru",
    "oai_pmh",
    "soap",
    "sparql",
    "bulk_dump",
]

AknSourceAuthScheme = Literal[
    "none",
    "token",
    "bearer",
    "api_key",
    "operator_code",
    "basic",
    "soap_session",
    "email_registration",
]


# Format to fetchability tier, AKN-native down to binary. Drives
# `effective_tier` so the atlas reflects what is fetchable here, not what is
# nominally configured.
_FORMAT_TIER: dict[str, Literal[1, 2, 3]] = {
    "akn_xml": 1,
    "akn_xml_v2": 1,
    "bespoke_xml": 2,
    "structured_html": 2,
    "json": 2,
    "csv": 2,
    "rdf": 2,
    "html": 3,
    "pdf": 3,
    "pdf_scanned": 3,
    "docx": 3,
    "rtf": 3,
}


class AknSource(BaseModel):
    """One upstream that yields documents convertible to AKN.

    Distinct from `SourceAdapter`, the acquisition substrate keyed by adapter kind:
    this is a format-and-protocol pairing declared in `config.json#akn_sources`.
    Both coexist during the schema migration and `effective_tier` walks both; legacy
    configs without `format`/`protocol` default to PDF over HTTP.
    """

    model_config = _STRICT

    name: str = Field(description=("Name of the source."))
    url_template: str = Field(
        description=("URL of the source, or a template for one document's URL.")
    )
    format: AknSourceFormat = Field(
        default="pdf",
        description=(
            "The format the source serves, which sets its tier in `effective_tier`. Defaults "
            "to PDF."
        ),
    )
    protocol: AknSourceProtocol = Field(
        default="http",
        description=(
            "How documents are fetched from the source. Defaults to HTTP. Recorded for reference; "
            "the pipeline does not read it."
        ),
    )
    coverage: Literal["complete", "partial", "stub", "unknown"] = Field(
        default="unknown",
        description=(
            "How much of the statute base the source holds. Recorded for reference; the pipeline "
            "does not read it."
        ),
    )
    coverage_note: str | None = Field(
        default=None,
        description=(
            "Free-text note on `coverage`. Recorded for reference; the pipeline does not read it."
        ),
    )
    schema_id: str | None = Field(
        default=None,
        description=(
            "Identifier of the schema the source's documents follow. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    schema_url: str | None = Field(
        default=None,
        description=(
            "URL of the schema the source's documents follow. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    license: str | None = Field(
        default=None,
        description=(
            "Licence the source publishes under. Recorded for reference; the pipeline does not "
            "read it."
        ),
    )
    auth_scheme: AknSourceAuthScheme = Field(
        default="none", description=("How requests to the source are authenticated.")
    )
    auth_env_var: str = Field(
        default="",
        description=(
            "Name of the environment variable holding the credential. The credential itself is "
            "never stored in the config."
        ),
    )
    requires_token: bool = Field(
        default=False,
        description=(
            "True for a source that needs an access token: `effective_tier` skips it when the "
            "token's environment variable is unset, unless `fallback_format` declares a usable "
            "downgrade."
        ),
    )
    fallback_format: AknSourceFormat | None = Field(
        default=None,
        description=(
            "The format still available when the source's credential is unset, so "
            "`effective_tier` can count it at that format."
        ),
    )
    description: str = Field(
        default="",
        description=("Free-text description of the source, often including its provenance."),
    )


class DiscoveryConfig(BaseModel):
    """Config-driven discovery for adapters that support it. The *basket
    definition* (which acts to find) lives here as per-doctype query strings;
    the portal mechanics (request shape, facets) stay in the adapter."""

    model_config = _STRICT

    queries: dict[str, str] = Field(
        default_factory=dict,
        description=("Discovery query per doctype, in the portal's own query syntax."),
    )
    max_total: int = Field(
        default=5000,
        gt=0,
        description=("Most documents one discovery run returns, unless the caller sets a limit."),
    )
    output: str | None = Field(
        default=None,
        description="Path of the basket file discovery writes, relative to the repository root.",
    )


class SourceAdapter(BaseModel):
    """One upstream portal for this jurisdiction; multiple permitted.

    url_template placeholders: {country} {doctype} {year} {number} {frbr_uri}. Auth
    tokens resolve from the env var named by auth_env_var, never stored here.
    """

    model_config = _STRICT

    kind: SourceAdapterKind = Field(description=("Which acquirer handles this source."))
    name: str = Field(description=("Name of the source, used in log lines and errors."))
    base_url: str | None = Field(
        default=None,
        description=("Root URL requests are made against. Unset uses the acquirer's default."),
    )
    url_template: str | None = Field(
        default=None,
        description=(
            "Template for one document's URL, for acquirers that build it from the FRBR URI."
        ),
    )
    auth_scheme: Literal["none", "token", "bearer", "oauth"] = Field(
        default="none",
        description=("How requests are authenticated. Anything but `none` needs `auth_env_var`."),
    )
    auth_env_var: str = Field(
        default="",
        description=(
            "Name of the environment variable holding the credential. The credential itself is "
            "never stored in the config."
        ),
    )
    parser: str | None = Field(
        default=None,
        description=(
            "Registered structured parser (in `codify.pipeline.parsers`) that converts this "
            "adapter's main body deterministically. None sends it through the general "
            "structuring lane."
        ),
    )
    rate_limit_per_minute: int | None = Field(
        default=None,
        description=(
            "Most requests per minute sent to the source's host. Unset leaves the acquirer's "
            "own default."
        ),
    )
    robots_respect: bool = Field(
        default=True, description=("Whether requests honour the host's robots.txt.")
    )
    licence: str | None = Field(
        default=None,
        description=(
            "Licence the source publishes under, recorded on fetched documents by the acquirers "
            "that carry it."
        ),
    )
    notes: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    discovery: DiscoveryConfig | None = Field(
        default=None,
        description=("How the source lists documents, for acquirers that support discovery."),
    )

    @model_validator(mode="after")
    def _auth_env_var_required_when_secured(self) -> "SourceAdapter":
        if self.auth_scheme != "none" and not self.auth_env_var:
            raise ValueError(
                f"SourceAdapter {self.name!r}: auth_scheme={self.auth_scheme!r} "
                f"requires auth_env_var (the env var name holding the secret)"
            )
        return self


class LegalEra(BaseModel):
    """A period whose documents share a drafting convention and authority.

    Structural quality tracks era more than jurisdiction, so it is reported per era.
    ``from_``/``to`` are inclusive ISO dates, either may be open, and the field is
    spelled ``from`` in the config files.
    """

    model_config = _LOOSE

    id: str = Field(
        description=("Identifier of the era, used to report structural quality per era.")
    )
    label: str = Field(
        default="",
        description=("Display name of the era, given to the metadata extractor with its calendar."),
    )
    from_: str | None = Field(
        default=None,
        alias="from",
        description=("First date of the era, as an ISO date, inclusive. Unset leaves it open."),
    )
    to: str | None = Field(
        default=None,
        description=("Last date of the era, as an ISO date, inclusive. Unset leaves it open."),
    )
    calendar: Calendar | None = Field(
        default=None,
        description=(
            "The calendar this era's instruments are dated in, where it differs from the "
            "jurisdiction's current one. None means it does not differ. Same closed set as "
            "the jurisdiction's own field, so the two cannot drift apart."
        ),
    )
    enacting_authority: str = Field(
        default="",
        description=(
            "The body that enacted instruments in this era. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    document_class: str = Field(
        default="",
        description=(
            "The document class typical of the era. Recorded for reference; the pipeline does "
            "not read it."
        ),
    )
    closing_phrases: list[str] = Field(
        default_factory=list,
        description=(
            "Phrases opening the attestation that closes a document of this era, so the "
            "promulgation and signature stop being read as the last article's text. "
            "Era-scoped because the wording follows the enacting authority."
        ),
    )
    footnote_markers: list[str] = Field(
        default_factory=list,
        description=(
            "Regex for how a footnote marker opens in this era. The modern set uses "
            "superscripts and the older ones a parenthesised numeral; declaring only one "
            "form silently drops the other era."
        ),
    )
    authority_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "How this era's offices are named in provision text, to flag provisions for review."
            " A match means the office is named, not that it is granted a power; opt-in per "
            "era, since an era ending does not abolish its authorities."
        ),
    )

    def year_range(self) -> tuple[int | None, int | None]:
        """The era's bounds as years, for matching a work whose date is a bare year."""

        def year(value: str | None) -> int | None:
            return int(value[:4]) if value and value[:4].isdigit() else None

        return year(self.from_), year(self.to)


class JurisdictionConfig(BaseModel):
    model_config = _LOOSE

    code: str = Field(description=("The jurisdiction code, such as `xa`."))
    name: str = Field(description=("The jurisdiction's name."))
    name_local: str | None = Field(
        default=None, description=("The jurisdiction's name in its own language.")
    )
    name_en: str | None = Field(default=None, description=("The jurisdiction's name in English."))
    name_translations: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Localised jurisdiction names keyed by ISO 639-3 language code, in the same shape "
            "as a law's title translations. The pipeline does not read it."
        ),
    )
    type: JurisdictionType = Field(
        default="national",
        description=(
            "Whether the jurisdiction is national, supranational, subnational or a crown "
            "dependency."
        ),
    )
    tradition: list[Tradition] = Field(
        description=("The legal traditions the jurisdiction draws on. At least one is required.")
    )

    @field_validator("tradition")
    @classmethod
    def tradition_not_empty(cls, v: list[Tradition]) -> list[Tradition]:
        if not v:
            raise ValueError("tradition list must not be empty")
        return v

    calendar: Calendar = Field(
        default="gregorian",
        description=("The calendar the jurisdiction's instruments are dated in."),
    )
    languages: list[str] = Field(
        description=(
            "Languages of the jurisdiction's texts, as ISO 639-3 codes. Where there is only "
            "one, it is used for a document whose language is not detected."
        )
    )
    authoritative_language: str | None = Field(
        default=None,
        description=(
            "The language whose text is legally authoritative, used when a document's own "
            "language cannot be detected."
        ),
    )

    document_classes: dict[str, DocumentClass] = Field(
        default_factory=dict,
        description=("The instrument types the jurisdiction issues, keyed by doctype."),
    )
    default_document_class: str = Field(
        default="act",
        description=(
            "The doctype used when no classification rule matches. It must be a key of "
            "`document_classes`."
        ),
    )

    frbr: FrbrConfig | None = Field(
        default=None, description=("How the jurisdiction's FRBR URIs are minted.")
    )
    numbering: NumberingConfig | None = Field(
        default=None, description=("How instruments are numbered and cited.")
    )
    enacting_formulae: list[EnactingFormula] = Field(
        default_factory=list,
        description=(
            "Enacting formulae to inject when the source carries none, chosen by doctype and date."
        ),
    )
    enacting_formula_markers: list[str] = Field(
        default_factory=list,
        description=(
            "Phrases showing the source carries its own enacting formula, so none is injected. "
            "Never a cut point: a phrase here may open the instrument, not close it."
        ),
    )
    adoption_markers: list[str] = Field(
        default_factory=list,
        description=(
            "Phrases declaring the instrument adopts another body's numbered text, which "
            "every count over the document must read as an attachment, not its own "
            "structure."
        ),
    )
    opening_material_terminators: list[str] = Field(
        default_factory=list,
        description=(
            "Phrases the recitals precede, after which any further pre-body text is stray page "
            "text."
        ),
    )
    attachments: list[AttachmentCaption] = Field(
        default_factory=list,
        description="Captions opening an attachment, whose numbering restarts the body's.",
    )
    placeholders: list[PlaceholderMarker] = Field(
        default_factory=list,
        description=(
            "Whole-string markers for missing or struck content, checked in addition to the "
            "built-in placeholder pattern."
        ),
    )
    pool_excluded_refers_to: list[str] = Field(
        default_factory=list,
        description=(
            "`refersTo` values marking a duty on someone other than the instrument's subjects. "
            "Units at or under a marked element stay readable but are not embedded or assessed."
        ),
    )
    segmentation: SegmentationConfig | None = Field(
        default=None,
        description="Where one source holds several instruments, how their openings are read.",
    )
    amendments: AmendmentConfig | None = Field(
        default=None, description=("How amending instruments are drafted.")
    )
    core_tlcs: list[TLCEntry] = Field(
        default_factory=list,
        description=(
            "Recurring persons, bodies and concepts, written to a document's `references` "
            "block when the document refers to them by eId."
        ),
    )
    structuring: StructuringConfig | None = Field(
        default=None,
        description=("Settings for marker detection, classification and the structurer."),
    )
    supranational_memberships: list[SupranationalMembership] = Field(
        default_factory=list,
        description=(
            "Supranational bodies the jurisdiction belongs to. Recorded for reference; the "
            "pipeline does not read it."
        ),
    )
    binding_regimes: list[BindingRegime] = Field(
        default_factory=list,
        description=(
            "Non-membership binding regimes (treaty frameworks, bilateral regimes, "
            "constitutional annexes), which are not memberships."
        ),
    )
    residual_legal_systems: list[ResidualLaw] = Field(
        default_factory=list,
        description=(
            "Foreign legal corpora still residually applicable here, such as pre-independence "
            "or occupying-power law left in force, so amendment and consolidation tooling can "
            "trace layered systems."
        ),
    )
    legal_eras: list[LegalEra] = Field(
        default_factory=list,
        description=(
            "Drafting periods, so quality is reported per era rather than per corpus; empty "
            "means one undivided population. Distinct from `CalendarConversion.eras`, which is "
            "a regnal year table."
        ),
    )
    display: DisplayConfig | None = Field(
        default=None, description=("How the jurisdiction's texts are written and displayed.")
    )
    validation: ValidationConfig | None = Field(
        default=None,
        description=(
            "How far the config has been checked against real documents. Recorded for "
            "reference; the pipeline does not read it."
        ),
    )
    ocr_header_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Running header and footer text that leaks into article bodies during OCR, stripped"
            " from paragraphs before translation so the model never sees it. Regex strings, so "
            "a profile author can add patterns without touching Python."
        ),
    )

    furniture_inline_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Page furniture that shares a line with real content, such as a portal credit or a "
            "stamped URL, so it is removed from the line rather than discarding the line. "
            "Narrower than `ocr_header_patterns`, which also matches the real title on page "
            "one."
        ),
    )

    lexicon_words: list[str] = Field(
        default_factory=list,
        description=(
            "Legal vocabulary particular to this jurisdiction, folded into the language's "
            "word list when a page is scored. A term naming one jurisdiction is that "
            "jurisdiction's data, not the language's."
        ),
    )

    furniture_line_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Page furniture whose whole line can be discarded: mastheads, page numbers, stamps."
            " Narrower than `ocr_header_patterns`, which also carries the running title and so "
            "matches the real title on page one; only this set is safe for whole-line removal."
        ),
    )

    closing_phrases: list[str] = Field(
        default_factory=list,
        description=(
            "Phrases opening the closing attestation in any era. Attestation detection reads "
            "them together with every era's `closing_phrases`; the per-era region vocabulary "
            "uses an era's own list instead where it declares one."
        ),
    )
    footnote_markers: list[str] = Field(
        default_factory=list,
        description=(
            "`LegalEra.footnote_markers` for a jurisdiction that declares no eras. "
            "An era's own list wins where it declares one."
        ),
    )

    min_anchor_coverage: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum ratio of captured structural anchors to marker literals in the source; "
            "ingest fails below it, so a partial AKN cannot ship. The default 0.80 tolerates "
            "in-prose cross-references, and 0.0 disables the check."
        ),
    )

    source_truncation_floor: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Share of a document's contents listing that may lack a body before the source is "
            "treated as truncated. Off by default (0.0), since a threshold has to be set for "
            "each series from that series' own documents."
        ),
    )

    source_adapters: list[SourceAdapter] = Field(
        default_factory=list,
        description=(
            "Structured acquisition adapters for this jurisdiction, read by `codify.acquisition`."
        ),
    )
    akn_sources: list[AknSource] = Field(
        default_factory=list,
        description=(
            "Hand-curated list of upstream sources. Read alongside `source_adapters`; "
            "`effective_tier` considers both."
        ),
    )

    # Supranational-only fields
    members: list[str] = Field(
        default_factory=list,
        description=(
            "For a supranational jurisdiction, the codes of its members. Recorded for "
            "reference; the pipeline does not read it."
        ),
    )
    akn_profile: str | None = Field(
        default=None,
        description=(
            "Name of the AKN profile the jurisdiction's publisher uses. Recorded for "
            "reference; the pipeline does not read it."
        ),
    )
    identification: IdentificationConfig | None = Field(
        default=None,
        description=(
            "The jurisdiction's own identifier scheme. Recorded for reference; the pipeline "
            "does not read it."
        ),
    )

    note: str = Field(
        default="",
        description="Free-text note at the jurisdiction level for cross-cutting guidance.",
    )

    tier: Literal[1, 2, 3, 4, 5] | None = Field(
        default=None,
        description=(
            "Availability tier of the jurisdiction's statute base: 1 AKN native, 2 "
            "XML-structured, 3 HTML anchors, 4 PDF-only, 5 no online statute base."
        ),
    )
    status: ValidationStatus | None = Field(
        default=None,
        description=("How far the config has been validated, as listed in the registry."),
    )
    coverage_status: Literal["in_scope", "subsumed", "no_corpus", "deferred"] = Field(
        default="in_scope",
        description=(
            "Whether the jurisdiction has its own corpus: `in_scope`, `subsumed` into another "
            "jurisdiction's, `no_corpus` or `deferred`."
        ),
    )
    subsumed_by: str | None = Field(
        default=None,
        description=(
            "The code of the jurisdiction whose corpus covers this one. Required when "
            "`coverage_status` is `subsumed` and refused otherwise."
        ),
    )

    synthetic: bool = Field(
        default=False,
        description=(
            "True for a fictional jurisdiction authored as synthetic test material. It is "
            "discovered and validated like any other config but kept out of the derived "
            "registry and the world atlas, which list real jurisdictions only."
        ),
    )

    public_reference: bool = Field(
        default=False,
        description=(
            "True where the config ships in the open core. Defaults to false, so a jurisdiction"
            " joins the open set only by a deliberate edit."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_tier(self) -> Literal[1, 2, 3, 4, 5] | None:
        """Tier actually achievable in the current environment.

        Walks both `source_adapters` and the legacy `akn_sources`, taking the best
        (lowest) tier. Token-gated entries with an unset env var are skipped unless the
        source declares a `fallback_format`, whose tier contributes instead.
        """
        best: int | None = None

        for sa in self.source_adapters:
            if sa.auth_scheme != "none" and not os.environ.get(sa.auth_env_var, ""):
                continue
            rank = _ADAPTER_TIER.get(sa.kind)
            if rank is None:
                continue
            if best is None or rank < best:
                best = rank

        for src in self.akn_sources:
            # Token-gated source: env var must be set, else fall back if declared.
            gated = src.requires_token or (
                src.auth_scheme not in ("none",) and src.auth_env_var != ""
            )
            usable_format: str | None = src.format
            if gated and not os.environ.get(src.auth_env_var, ""):
                usable_format = src.fallback_format
            if usable_format is None:
                continue
            rank = _FORMAT_TIER.get(usable_format)
            if rank is None:
                continue
            if best is None or rank < best:
                best = rank

        if best is None:
            return self.tier
        return cast("Literal[1, 2, 3]", best)

    @model_validator(mode="after")
    def _subsumed_by_consistency(self) -> "JurisdictionConfig":
        if self.coverage_status == "subsumed" and not self.subsumed_by:
            raise ValueError(
                f"jurisdiction {self.code!r} has coverage_status='subsumed' but no subsumed_by code"
            )
        if self.coverage_status != "subsumed" and self.subsumed_by:
            raise ValueError(
                f"jurisdiction {self.code!r} has subsumed_by={self.subsumed_by!r} "
                f"but coverage_status={self.coverage_status!r}; subsumed_by is "
                "only meaningful when coverage_status == 'subsumed'"
            )
        return self

    @model_validator(mode="after")
    def _resolve_document_class_extends(self) -> "JurisdictionConfig":
        # A class that extends another is the parent with the child's fields laid over.
        parents = {name: dc.extends for name, dc in self.document_classes.items()}
        for name, dc in self.document_classes.items():
            if dc.extends is None:
                continue
            parent = self.document_classes.get(dc.extends)
            if parent is None or parents[dc.extends] is not None:
                raise ValueError(
                    f"document class '{name}' extends '{dc.extends}', which is "
                    "missing or itself extends another class"
                )
            overlay = {k: getattr(dc, k) for k in dc.model_fields_set if k != "extends"}
            self.document_classes[name] = parent.model_copy(update=overlay)
        return self

    @model_validator(mode="after")
    def _title_numbered_classes_have_a_grammar(self) -> "JurisdictionConfig":
        # Without the grammar the forced number would be the content address.
        if self.frbr is not None and self.frbr.title_identity is not None:
            return self
        for name, dc in self.document_classes.items():
            if dc.number_source == "title_identity":
                raise ValueError(
                    f"document class '{name}' sets number_source='title_identity' "
                    "but frbr.title_identity is not declared"
                )
        return self

    @model_validator(mode="after")
    def _default_document_class_exists(self) -> "JurisdictionConfig":
        if self.document_classes and self.default_document_class not in self.document_classes:
            raise ValueError(
                f"default_document_class '{self.default_document_class}' "
                f"not in document_classes keys {sorted(self.document_classes.keys())}"
            )
        return self

    @model_validator(mode="after")
    def _national_cannot_claim_direct_effect(self) -> "JurisdictionConfig":
        if self.type == "national":
            for dc_name, dc in self.document_classes.items():
                if dc.direct_effect is True:
                    raise ValueError(
                        f"document class '{dc_name}': national jurisdictions cannot "
                        f"set direct_effect=true (reserved for supranational bodies)"
                    )
        return self

    @model_validator(mode="after")
    def _classification_rule_targets_exist(self) -> "JurisdictionConfig":
        if not self.structuring or not self.structuring.classification_rules:
            return self
        for rule in self.structuring.classification_rules:
            if rule.target_document_class not in self.document_classes:
                raise ValueError(
                    f"classification_rule target '{rule.target_document_class}' "
                    f"not in document_classes {sorted(self.document_classes.keys())}"
                )
        return self

    @model_validator(mode="after")
    def _discovery_queries_are_doctypes(self) -> "JurisdictionConfig":
        # A discovery query keyed on a doctype the jurisdiction doesn't declare
        # is a typo/rename drift; catch it at config load, not at discover() time.
        if not self.document_classes:
            return self
        for sa in self.source_adapters:
            if sa.discovery is None:
                continue
            unknown = set(sa.discovery.queries) - set(self.document_classes)
            if unknown:
                raise ValueError(
                    f"source_adapter {sa.name!r} discovery queries {sorted(unknown)} "
                    f"not in document_classes {sorted(self.document_classes.keys())}"
                )
        return self

    @model_validator(mode="after")
    def _residual_source_jurisdictions_exist(self) -> "JurisdictionConfig":
        # Existence check, not a load: loading would recurse into config
        # resolution. An unresolved JURISDICTIONS_DIR (test harnesses, alternate
        # working directories) skips the check rather than failing every entry.
        if not self.residual_legal_systems or not JURISDICTIONS_DIR.exists():
            return self
        for r in self.residual_legal_systems:
            path = JURISDICTIONS_DIR / r.source_jurisdiction / "config.json"
            if not path.exists():
                raise ValueError(
                    f"residual_legal_systems: source_jurisdiction "
                    f"'{r.source_jurisdiction}' has no config at {path}"
                )
        return self

    @model_validator(mode="after")
    def _hierarchy_levels_coherent(self) -> "JurisdictionConfig":
        for dc_name, dc in self.document_classes.items():
            _check_hierarchy_levels(f"document class '{dc_name}'", dc.hierarchy)
        # An attachment declares its own levels and needs the same contract:
        # its ranks are merged into the containment order like any other.
        for att in self.attachments:
            _check_hierarchy_levels(f"attachment '{att.caption}'", att.hierarchy)
        return self

    def classify_document_class(
        self,
        *,
        title: str = "",
        preamble: str = "",
        issuer_role: str = "",
        gazette_series: str = "",
        doc_date: date | None = None,
    ) -> str | None:
        """First matching classification rule's target class, or ``None``.

        ``None`` means no rule matched and the caller falls back to
        ``default_document_class``: an unmatched document is unclassified, not
        misclassified.

        A signal the caller has no text for cannot match, so a rule keyed on it is
        skipped rather than treated as satisfied. Date bounds apply to every signal, and
        a rule with bounds but no date to test against is skipped for the same reason.
        """
        # OCR'd Arabic titles often store hamza decomposed (bare alef + U+0654)
        # where the rule patterns use the precomposed letter; canonically equal,
        # byte-different, so match on NFC.
        title = unicodedata.normalize("NFC", title)
        preamble = unicodedata.normalize("NFC", preamble)
        issuer_role = unicodedata.normalize("NFC", issuer_role)
        gazette_series = unicodedata.normalize("NFC", gazette_series)
        rules = self.structuring.classification_rules if self.structuring else []
        for rule in sorted(rules, key=lambda r: r.priority, reverse=True):
            if rule.date_from or rule.date_until:
                if doc_date is None:
                    continue
                if rule.date_from and doc_date < rule.date_from:
                    continue
                if rule.date_until and doc_date > rule.date_until:
                    continue
            if rule.signal == "date_range":
                if rule.date_from or rule.date_until:
                    return rule.target_document_class
                continue
            haystack = {
                "title_regex": title,
                "preamble_match": preamble,
                "issuer_role": issuer_role,
                "gazette_series": gazette_series,
            }.get(rule.signal, "")
            if not haystack or not rule.pattern:
                continue
            flags = re.IGNORECASE if "i" in rule.pattern_flags.lower() else 0
            try:
                matched = re.search(rule.pattern, haystack, flags) is not None
            except re.error:
                # A rule nobody can compile must not silently classify every
                # document that reaches it.
                logger.warning(
                    "classification_rule_pattern_invalid",
                    country=self.code,
                    signal=rule.signal,
                    pattern=rule.pattern[:80],
                )
                continue
            if matched:
                return rule.target_document_class
        return None

    def get_document_class(self, doctype: str) -> DocumentClass | None:
        explicit = self.document_classes.get(doctype)
        if explicit is not None:
            return explicit
        return self.document_classes.get(self.default_document_class)


# --- Loader ---


class ResolvedConfig(BaseModel):
    """Which jurisdiction config a run used, and where it came from.

    Recorded so a bundle can be read back and its provenance established. The
    digest is what distinguishes two runs of the same code against a config that
    changed between them, which the code alone cannot.
    """

    code: str
    path: str | None = None
    sha256: str | None = None

    @property
    def found(self) -> bool:
        return self.path is not None


def resolve_config(country_code: str) -> ResolvedConfig:
    """Identify the config for `country_code` without loading it.

    Absence is a `found` of False, not a raise: the callers report which
    jurisdiction was missing and where they looked, which a traceback buries.
    """
    code = country_code.strip().lower()
    path = JURISDICTIONS_DIR / code / "config.json"
    # A code names one directory under the root: one that escapes it is absent, and
    # a NUL, which realpath refuses with ValueError, is absent too.
    if "\x00" in code:
        return ResolvedConfig(code=code)
    root = os.path.realpath(JURISDICTIONS_DIR)
    real = os.path.realpath(os.path.join(root, code, "config.json"))
    if "/" in code or "\\" in code or not real.startswith(root + os.sep):
        return ResolvedConfig(code=code)
    if not os.path.exists(real):
        return ResolvedConfig(code=code)
    with open(real, "rb") as source:
        digest = hashlib.sha256(source.read()).hexdigest()
    # Recorded relative to the tree root, not absolutely: a bundle travels, and
    # an absolute path names the machine that produced it rather than the config.
    try:
        where = str(path.relative_to(JURISDICTIONS_DIR.parents[1]))
    except ValueError:
        where = str(path)
    return ResolvedConfig(code=code, path=where, sha256=digest)


# Named in every refusal: the fix is always to write this file.
CONFIG_HELP = (
    "write {path}: `code`, `name`, `tradition` and `languages` are required, "
    "everything else defaults; see docs/jurisdictions/adding-a-jurisdiction.md"
)


def log_config_absent(country_code: str, degraded: str) -> None:
    """For a caller that probes and carries on. `degraded` says what the reader
    loses, in their words, not the field's."""
    logger.warning(
        "jurisdiction_config_absent",
        jurisdiction_code=country_code,
        degraded=degraded,
        fix=CONFIG_HELP.format(path=JURISDICTIONS_DIR / country_code / "config.json"),
    )


class JurisdictionConfigError(LookupError):
    """A caller named a jurisdiction and no config answered. A `LookupError`
    because 78 broad `except ValueError` sites would default past it."""


class JurisdictionDataMissing(JurisdictionConfigError):
    """The data is not in this tree at all, as opposed to being present and
    wrong. Only absence is a legitimate skip; a config that ships and
    contradicts itself is a defect wherever it is read."""


# A config that is absent or does not validate. A caller tolerating its own
# failures re-raises these first: a thinner result is not a fallback.
CONFIG_FAULTS: tuple[type[Exception], ...] = (JurisdictionConfigError, ValidationError)


def load_config(country_code: str) -> JurisdictionConfig:
    """The jurisdiction's config, or a fault.

    Absence raises: a caller receiving nothing produced a thinner result.
    """
    config = try_load_config(country_code)
    if config is not None:
        return config
    # A code no tree could ship is malformed input, not absent data.
    if "/" in country_code or ".." in country_code:
        raise JurisdictionConfigError(f"not a jurisdiction code: {country_code!r}")
    if not JURISDICTIONS_DIR.exists():
        raise JurisdictionDataMissing(
            f"no jurisdiction data at {JURISDICTIONS_DIR}: the package was installed "
            "without it, so every config lookup would answer with defaults; "
            + CONFIG_HELP.format(path=JURISDICTIONS_DIR / country_code / "config.json")
        )
    raise JurisdictionDataMissing(
        f"no jurisdiction config for {country_code!r} in {JURISDICTIONS_DIR}; "
        + CONFIG_HELP.format(path=JURISDICTIONS_DIR / country_code / "config.json")
    )


@lru_cache(maxsize=64)
def try_load_config(country_code: str) -> JurisdictionConfig | None:
    """The config, or None if the code is malformed or names no config file.

    For a caller asking whether one exists; anything needing a config wants
    `load_config`. Clear cache with ``try_load_config.cache_clear()``.
    """
    if "/" in country_code or ".." in country_code:
        return None
    # Normalised and held under the data root before any read.
    base = os.path.normpath(str(JURISDICTIONS_DIR))
    path = os.path.normpath(os.path.join(base, country_code, "config.json"))
    if not path.startswith(base + os.sep) or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except (OSError, UnicodeError) as exc:
        # A config there and unreadable is a fault of the config, not of the caller.
        raise JurisdictionConfigError(
            f"jurisdiction config {path} could not be read: {exc}"
        ) from exc
    return JurisdictionConfig.model_validate_json(raw)


class FrbrCountry(NamedTuple):
    """The country segment a jurisdiction's work URIs carry, and its provenance.

    `resolved` is False when no config supplied the segment and it was guessed
    from the jurisdiction code. A caller minting an identity reads it to record
    the segment as guessed rather than authoritative.
    """

    segment: str
    resolved: bool


def placeholder_statuses_for(work_uri: str | None) -> tuple[tuple[str, str], ...]:
    """`(pattern, status)` for the platform placeholder marker and the jurisdiction's
    own, keyed by the work URI's country like the attachment rule, so ingest and
    every later pass agree. The platform marker stands for untranscribed content."""
    return _placeholder_entries(config_for_work_uri(work_uri))


def config_for_work_uri(work_uri: str | None) -> JurisdictionConfig | None:
    """The config a work URI's place names; a locality (`/akn/xa-c01/`) reads its
    country's."""
    from codify.akn.frbr import country_of

    if not work_uri:
        return None
    place = country_of(work_uri)
    config = try_load_config(place)
    if config is not None:
        return config
    country = _locality_country(place)
    return try_load_config(country) if country else None


#: The `{locality}` of a sub-national issuer's `/akn/{country}-{locality}/` place.
LOCALITY = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=4)
def _jurisdiction_places(root: Path) -> frozenset[str]:
    """Every jurisdiction code under `root` and every FRBR place its configs declare,
    read straight from JSON like `ordinal_word_folds`."""
    places: set[str] = set()
    for entry in root.glob("*/config.json"):
        try:
            frbr = json.loads(entry.read_text(encoding="utf-8")).get("frbr") or {}
            place = str(frbr.get("country_code") or "")
        except (OSError, ValueError, AttributeError) as exc:
            raise JurisdictionConfigError(f"jurisdiction config {entry} could not be read") from exc
        places.update((entry.parent.name, place.strip().lower()))
    places.discard("")
    return frozenset(places)


def _locality_country(place: str) -> str | None:
    """The country of a `{country}-{locality}` place, or None when the place names a
    jurisdiction. A tree with no configs cannot tell, so it reads no locality."""
    country, _, locality = place.rpartition("-")
    if not country or LOCALITY.fullmatch(locality) is None:
        return None
    places = _jurisdiction_places(JURISDICTIONS_DIR)
    return country if places and place not in places else None


def placeholder_statuses_for_code(jurisdiction_code: str) -> tuple[tuple[str, str], ...]:
    """As `placeholder_statuses_for`, from a jurisdiction code at ingest, before the
    document carries a work URI the row could be keyed by."""
    return _placeholder_entries(try_load_config(jurisdiction_code.strip().lower()))


def _placeholder_entries(config: JurisdictionConfig | None) -> tuple[tuple[str, str], ...]:
    from codify.quality.sentinels import PLACEHOLDER_PATTERN

    own = tuple((m.pattern, str(m.status)) for m in config.placeholders) if config else ()
    return ((PLACEHOLDER_PATTERN, str(PlaceholderStatus.INCOMPLETE)), *own)


def placeholder_statuses_by_code() -> dict[str, tuple[tuple[str, str], ...]]:
    """Each jurisdiction that declares its own markers, with its full entry list,
    for a sweep not scoped to one jurisdiction. Reads each config once."""
    out: dict[str, tuple[tuple[str, str], ...]] = {}
    for entry in sorted(JURISDICTIONS_DIR.glob("*/config.json")):
        config = try_load_config(entry.parent.name)
        if config is not None and config.placeholders:
            out[entry.parent.name] = _placeholder_entries(config)
    return out


def placeholder_markers_for(work_uri: str | None) -> tuple[str, ...]:
    """The patterns alone, for the row-level exclusion rule."""
    return tuple(pattern for pattern, _ in placeholder_statuses_for(work_uri))


def pool_excluded_refers_to_for(work_uri: str | None) -> frozenset[str]:
    """The jurisdiction's `pool_excluded_refers_to`, keyed by the work URI's country
    like the placeholder markers so every pass reads the same list."""
    config = config_for_work_uri(work_uri)
    if config is None:
        return frozenset()
    return frozenset(v.strip() for v in config.pool_excluded_refers_to if v.strip())


def resolve_frbr_country(jurisdiction_code: str) -> FrbrCountry:
    """The country segment for this jurisdiction, with whether a config set it.

    Not always the jurisdiction code: `gb-eng` publishes under `/akn/gb/`, `eac`
    under `/akn/aa-eac/`. A code with no config, or a config with no FRBR block,
    falls back to the code itself and reports `resolved=False`.
    """
    code = jurisdiction_code.strip().lower()
    cfg = try_load_config(code)
    if cfg is None or cfg.frbr is None:
        return FrbrCountry(code, resolved=False)
    return FrbrCountry(cfg.frbr.country_code.strip().lower(), resolved=True)


def frbr_country(jurisdiction_code: str) -> str:
    """The country segment this jurisdiction's work URIs carry.

    A missing config guesses the code as its own country and warns, since a
    guessed segment becomes a law's citation identity. Read `resolve_frbr_country`
    where the caller must branch on whether the segment was guessed.
    """
    country = resolve_frbr_country(jurisdiction_code)
    if not country.resolved:
        logger.warning("frbr_country_guessed_from_code", jurisdiction=jurisdiction_code)
    return country.segment


def work_uri_is_homed(work_uri: str, jurisdiction_code: str) -> bool:
    """Whether a work URI's country names this jurisdiction.

    An empty default, not `country_of`'s `"xx"`: `xx` is a real code here, so
    the default would let a country-less URI match it.
    """
    from codify.akn.frbr import country_of

    place = country_of(work_uri, default="").strip().lower()
    country = frbr_country(jurisdiction_code)
    return place == country or _locality_country(place) == country


#: Document elements for instruments other than legislation, which Bluebell
#: parses from the same text as an act.
OPEN_STRUCTURE_ROOTS = frozenset({"doc", "statement"})


def open_structure_root(jurisdiction_code: str, doctype: str) -> str | None:
    """`doc` or `statement` when `doctype` is one, or names a class declaring one
    as its `akn_element`; None for legislation."""
    if doctype in OPEN_STRUCTURE_ROOTS:
        return doctype
    cfg = try_load_config(jurisdiction_code.strip().lower())
    doc_class = cfg.document_classes.get(doctype) if cfg is not None else None
    element = doc_class.akn_element if doc_class is not None else None
    return element if element in OPEN_STRUCTURE_ROOTS else None


@lru_cache(maxsize=32)
def load_short_titles(country_code: str) -> dict[str, str]:
    """{normalised short title: work URI} from short_titles.json, {} if absent."""
    if "/" in country_code or ".." in country_code:
        return {}
    path = JURISDICTIONS_DIR / country_code / "short_titles.json"
    if not path.exists():
        return {}
    loaded: dict[str, str] = json.loads(path.read_text())
    return loaded


def authoritative_language(jurisdiction: str) -> str:
    """The jurisdiction's authoritative ISO 639-3 language from config, the
    single source for adapter fallbacks. Raises if the config is missing or
    declares no language (fail loud rather than ingest under a wrong language)."""
    cfg = load_config(jurisdiction)
    lang = cfg.authoritative_language or (cfg.languages[0] if cfg.languages else None)
    if lang is None:
        raise ValueError(f"jurisdiction {jurisdiction!r} declares no languages")
    return lang


def act_short_label(jurisdiction: str, doctype: str) -> str:
    """Compact doctype label from config (e.g. al/vendim → "VKM").
    Falls back to the title-cased doctype when unconfigured."""
    cfg = try_load_config(jurisdiction)
    if cfg is not None:
        dc = cfg.document_classes.get(doctype)
        if dc is not None and dc.short_label:
            return dc.short_label
        if dc is None:
            # Doctype unknown to the config, a typo/drift, not a missing label.
            logger.warning("doctype_not_in_config", jurisdiction=jurisdiction, doctype=doctype)
    return doctype.title() if doctype else "Act"


def act_short_code(frbr_work_uri: str, doctype: str) -> str:
    """`/akn/al/act/vendim/2021/285` + "vendim" → "VKM 285/2021". The
    jurisdiction comes from the FRBR; number/year are its last two segments."""
    from codify.akn.frbr import country_of

    segs = [s for s in (frbr_work_uri or "").split("/") if s]
    label = act_short_label(country_of(frbr_work_uri), doctype)
    # A real act work URI ends in …/{year}/{number} with a numeric year; anything
    # else is malformed, return the bare label rather than a garbage "X al/akn".
    if len(segs) < 2 or not segs[-2].isdigit():
        logger.warning("act_short_code_malformed_frbr", frbr_work_uri=frbr_work_uri)
        return label
    year, number = segs[-2], segs[-1]
    return f"{label} {number}/{year}".strip()


@lru_cache(maxsize=1)
def ordinal_word_folds() -> dict[str, str]:
    """Every declared ordinal word mapped to its integer, across all jurisdictions. The
    words are language-distinctive, so one table serves callers deriving an eId
    without knowing the country.

    Read straight from JSON: a sweep of every config would evict `load_config`'s
    64-entry cache.
    """
    out: dict[str, str] = {}
    for entry in sorted(JURISDICTIONS_DIR.glob("*/config.json")):
        try:
            raw = json.loads(entry.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("ordinal_words_unreadable", config=str(entry))
            continue
        words = (raw.get("structuring") or {}).get("ordinal_words") or {}
        for word, value in words.items():
            out[word] = str(value)
            out[word.upper()] = str(value)
    return out


@lru_cache(maxsize=1)
def insertion_suffix_folds() -> dict[str, str]:
    """Every declared insertion suffix mapped to its ASCII form, across all
    jurisdictions; one table, like `ordinal_word_folds`, for eId derivation."""
    out: dict[str, str] = {}
    for entry in sorted(JURISDICTIONS_DIR.glob("*/config.json")):
        try:
            raw = json.loads(entry.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("insertion_suffixes_unreadable", config=str(entry))
            continue
        words = (raw.get("structuring") or {}).get("insertion_suffixes") or {}
        out.update({str(word): str(value) for word, value in words.items()})
    return out


def fold_inserted_suffix(num: str) -> str | None:
    """`"5 bis"` as `"5bis"` when the number ends in a declared suffix, else None."""
    value = num.strip()
    folds = insertion_suffix_folds()
    # Longest word first: the suffix is matched at the end, never inferred.
    for word in sorted(folds, key=lambda w: (-len(w), w)):
        base = value[: -len(word)].rstrip() if value.endswith(word) else ""
        if base:
            # A slashed base keys the way the parser writes it.
            return re.sub(r"(?<=\d)/(?=\d)", "-", base) + folds[word]
    return None


@lru_cache(maxsize=32)
def _all_declared_ranks() -> dict[tuple[str, str], int]:
    """Every declared rank across all jurisdictions, keyed `(code, doctype)`.

    Read straight from JSON like `ordinal_word_folds`, so a 257-config sweep does not
    evict `load_config`'s cache.
    """
    out: dict[tuple[str, str], int] = {}
    for entry in sorted(JURISDICTIONS_DIR.glob("*/config.json")):
        try:
            raw = json.loads(entry.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("search_ranks_unreadable", config=str(entry))
            continue
        for doctype, cls in (raw.get("document_classes") or {}).items():
            rank = (cls or {}).get("search_rank")
            if rank is not None:
                out[(entry.parent.name, doctype)] = int(rank)
    return out


def doctype_search_ranks(country_codes: list[str] | None) -> dict[tuple[str, str], int]:
    """Hierarchy rank per `(jurisdiction_code, doctype)` for the scope given.

    Empty when no class in scope declares one, which is most of the corpus, leaving
    pure relevance order rather than an invented one.

    Keyed by jurisdiction as well as doctype because doctype names are
    jurisdiction-local vocabularies that collide: 96 names are defined by more than
    one config, `qanun` among them, meaning a statute in one jurisdiction and a
    provincial instrument in another. `None` scope means the whole corpus, which is
    what an unscoped search passes, and is not the same as an empty list.
    """
    every = _all_declared_ranks()
    if country_codes is None:
        return every
    wanted = set(country_codes)
    return {key: rank for key, rank in every.items() if key[0] in wanted}


def load_registry() -> list[dict[str, Any]]:
    """Every jurisdiction this tree carries. Absence raises: an empty registry
    reads as a deployment with no jurisdictions rather than a missing file."""
    path = JURISDICTIONS_DIR / "registry.json"
    if not path.exists():
        raise JurisdictionDataMissing(f"no jurisdiction registry at {path}")
    entries: list[dict[str, Any]] = json.loads(path.read_text()).get("jurisdictions", [])
    return entries


@lru_cache(maxsize=64)
def load_profile(country_code: str) -> str | None:
    """Prose from `data/jurisdictions/<code>/profile.md`, or None."""
    if "/" in country_code or ".." in country_code:
        return None
    path = JURISDICTIONS_DIR / country_code / "profile.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")
