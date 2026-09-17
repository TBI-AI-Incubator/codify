"""Every cell of the year/date resolution grid, asserted through each caller.
Axes: calendar label, month grid, stated date, echo, reform, title grammar."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from codify.calendar import labelled_year_as_gregorian, month_day_stating_this_year
from codify.jurisdictions import try_load_config
from codify.pipeline import stages

MONTHS = [
    "มกราคม",
    "กุมภาพันธ์",
    "มีนาคม",
    "เมษายน",
    "พฤษภาคม",
    "มิถุนายน",
    "กรกฎาคม",
    "สิงหาคม",
    "กันยายน",
    "ตุลาคม",
    "พฤศจิกายน",
    "ธันวาคม",
]
IDENTITY = {
    "strip_prefixes": ["พระราชบัญญัติ"],
    "year_particles": ["พ.ศ."],
    "edition_markers": ["ฉบับที่"],
    "consolidation_markers": ["Update"],
}
#: Pre-reform, so a first-quarter month moves the year; and post-reform, so it
#: does not. Both titles are invented.
PRE = "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2478"
POST = "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511"
#: A first-quarter date, so the reform shift is visible where it applies.
SOURCE_PRE = "ให้ไว้ ณ วันที่ ๓๑ มกราคม พ.ศ. ๒๔๗๘\n"
SOURCE_POST = "ให้ไว้ ณ วันที่ ๓๑ มกราคม พ.ศ. ๒๕๑๑\n"
#: A cue date naming the year after the one PRE's title states.
SOURCE_OTHER = "ให้ไว้ ณ วันที่ ๓๑ มกราคม พ.ศ. ๒๔๗๙\n"
#: A title naming the Act it amends before itself, so two year runs stand in it
#: and only the grammar can say which is the instrument's own.
TWO_YEARS = "พระราชบัญญัติแก้ไขเพิ่มเติมพระราชบัญญัติมโหรีหลวง พ.ศ. 2478 พ.ศ. 2486"
#: A title stating no year, so a field's sentinel has nothing to hide behind.
UNDATED = "พระราชบัญญัติเครื่องร่อนสุริยะ"


def _conversion(
    *, gregorian_grid: bool = True, reform: bool = True, epoch_year: int = -543
) -> dict[str, object]:
    rule: dict[str, object] = {
        "kind": "buddhist",
        "epoch_year": epoch_year,
        "month_names": MONTHS,
        "month_day_is_gregorian": gregorian_grid,
        "date_cues": ["ให้ไว้ ณ วันที่"],
        "year_particles": ["พ.ศ."],
    }
    if reform:
        rule |= {"new_year_month": 4, "new_year_reform_year": 2484}
    return rule


#: code -> (title identity declared, Gregorian month grid, reform declared)
JURISDICTIONS = {
    "xg": (True, True, True),
    "xn": (True, True, False),
    "xo": (True, False, True),
    "xs": (False, True, True),
    # A declared epoch the calendar's name does not imply, and no title grammar.
    "xe": (False, True, True),
    # The same declared epoch, with a title grammar, so every year source can be
    # exercised against a rule the calendar's name does not imply.
    "xei": (True, True, True),
    # The same three, with no title grammar declared.
    "xg2": (False, True, True),
    "xn2": (False, True, False),
    "xo2": (False, False, True),
}


@pytest.fixture(autouse=True)
def configs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tests.config_fixtures import isolated_configs

    built = {}
    for code, (identity, grid, reform) in JURISDICTIONS.items():
        frbr: dict[str, object] = {
            "country_code": code,
            "uri_patterns": {"act": f"/akn/{code}/act/{{year}}/{{number}}"},
            "calendar_conversion": _conversion(
                gregorian_grid=grid,
                reform=reform,
                epoch_year=-200 if code in ("xe", "xei") else -543,
            ),
        }
        if identity:
            frbr["title_identity"] = IDENTITY
        built[code] = {"calendar": "buddhist_era", "languages": ["tha"], "frbr": frbr}
    with isolated_configs(monkeypatch, tmp_path / "jurisdictions", built):
        yield


def _resolve(code: str, metadata: dict[str, object], source: str) -> stages.Descriptors:
    try_load_config.cache_clear()
    return stages.resolve_descriptors(
        {"number": "", **metadata},
        jurisdiction_code=code,
        source_bytes=source.encode(),
        fallback_stem="source",
        classification_text=source,
    )


# (case, code, metadata, source text, expected year, expected date)
GRID: list[tuple[str, str, dict[str, object], str, str, str]] = [
    # --- no date anywhere: the title's year converts, month-blind ------------
    ("title only, pre-reform", "xg", {"title": PRE}, "", "1935", ""),
    ("title only, post-reform", "xg", {"title": POST}, "", "1968", ""),
    ("title only, no reform declared", "xn", {"title": PRE}, "", "1935", ""),
    # No grammar declares the title, so its year is read as written.
    ("no identity, no date", "xs", {"title": PRE}, "", "2478", ""),
    # An ordinary title in a jurisdiction dating in another calendar: with no
    # grammar to say which year is local, none is.
    ("a plain title, no grammar", "xs", {"title": "Act of 2023"}, "", "2023", ""),
    # --- source states a date ------------------------------------------------
    ("source date, pre-reform", "xg", {"title": PRE}, SOURCE_PRE, "1936", "1936-01-31"),
    ("source date, post-reform", "xg", {"title": POST}, SOURCE_POST, "1968", "1968-01-31"),
    ("source date, no reform declared", "xn", {"title": PRE}, SOURCE_PRE, "1935", "1935-01-31"),
    ("source date, no identity", "xs", {"title": PRE}, SOURCE_PRE, "1936", "1936-01-31"),
    (
        "source date, model year echoes the title",
        "xg",
        {"title": PRE, "year": "2478", "calendar": "buddhist"},
        SOURCE_PRE,
        "1936",
        "1936-01-31",
    ),
    (
        "source date wins over a metadata date",
        "xg",
        {"title": PRE, "date": "2478-05-20", "calendar": "buddhist"},
        SOURCE_PRE,
        "1936",
        "1936-01-31",
    ),
    # --- the model states a local date, labelled -----------------------------
    (
        "labelled local date, Gregorian grid",
        "xg",
        {"title": PRE, "date": "2478-02-29", "calendar": "buddhist"},
        "",
        "1936",
        "1936-02-29",
    ),
    (
        "labelled local date, post-reform",
        "xg",
        {"title": POST, "date": "2511-09-09", "calendar": "buddhist"},
        "",
        "1968",
        "1968-09-09",
    ),
    (
        "labelled local date, month grid not Gregorian",
        "xo",
        {"title": PRE, "date": "2478-02-20", "calendar": "buddhist"},
        "",
        # A month of another grid says nothing about a threshold set on the
        # Gregorian one, so the year converts month-blind.
        "1935",
        "",
    ),
    (
        "an unlabelled echo, month grid not Gregorian",
        "xo",
        {"title": PRE, "date": "2478-02-20", "calendar": ""},
        "",
        "1935",
        "",
    ),
    # --- the model states a local date and calls it Gregorian ----------------
    (
        "unlabelled local date echoing the title",
        "xg",
        {"title": POST, "date": "2511-09-09", "calendar": ""},
        "",
        "1968",
        "1968-09-09",
    ),
    (
        "unlabelled local date, month grid not Gregorian",
        "xo",
        {"title": POST, "date": "2511-09-09", "calendar": ""},
        "",
        "1968",
        "",
    ),
    (
        "unlabelled date that does not echo the title",
        "xg",
        {"title": POST, "date": "1968-09-09", "calendar": ""},
        "",
        "1968",
        "1968-09-09",
    ),
    # --- the configured rule, not a generic offset --------------------------
    ("custom epoch, title only", "xe", {"title": PRE}, "", "2478", ""),
    (
        "no identity, labelled local date",
        "xs",
        {"title": PRE, "date": "2478-02-29", "calendar": "buddhist"},
        "",
        "1936",
        "1936-02-29",
    ),
    (
        "unlabelled local date whose day does not exist",
        "xg",
        {"title": POST, "date": "2511-04-31", "calendar": ""},
        "",
        "1968",
        "",
    ),
    (
        "a non-date field does not hide a source date",
        "xg",
        {"title": PRE, "date": "unknown"},
        SOURCE_PRE,
        "1936",
        "1936-01-31",
    ),
    ("custom epoch, title only, with a grammar", "xei", {"title": PRE}, "", "2278", ""),
    (
        "custom epoch, labelled year",
        "xei",
        {"title": PRE, "year": "2478", "calendar": "buddhist"},
        "",
        "2278",
        "",
    ),
    (
        "custom epoch, labelled date",
        "xei",
        {"title": PRE, "date": "2478-02-20", "calendar": "buddhist"},
        "",
        "2279",
        "2279-02-20",
    ),
    (
        "custom epoch, unlabelled echo",
        "xei",
        {"title": PRE, "date": "2478-09-09", "calendar": ""},
        "",
        "2278",
        "2278-09-09",
    ),
    ("custom epoch, source date", "xei", {"title": PRE}, SOURCE_PRE, "2279", "2279-01-31"),
    (
        "a day run longer than two digits is not a date",
        "xg",
        {"title": POST, "date": "2511-09-090", "calendar": "buddhist"},
        "",
        "1968",
        "",
    ),
    (
        # The date names a year the title does not. Its own year converts; it is
        # never rebuilt on the title's, which would invent a date neither states.
        "a labelled date naming another year keeps its own",
        "xg",
        {"title": PRE, "date": "2481-05-20", "calendar": "buddhist"},
        "",
        "1938",
        "1938-05-20",
    ),
    (
        # The model echoes the title's year and dates the document in another.
        # The echoed year converts; the conflicting date is not rebuilt on it.
        "an echoed year with a date naming another year",
        "xg",
        {"title": PRE, "year": "2478", "date": "2481-05-20", "calendar": "buddhist"},
        "",
        "1935",
        "",
    ),
    # --- padded and decorated fields --------------------------------------
    (
        "a padded labelled local date",
        "xg",
        {"title": PRE, "date": " 2478-02-29 ", "calendar": "buddhist"},
        "",
        "1936",
        "1936-02-29",
    ),
    (
        "a labelled local date in native digits",
        "xg",
        {"title": PRE, "date": "๒๔๗๘-๐๒-๒๙", "calendar": "buddhist"},
        "",
        "1936",
        "1936-02-29",
    ),
    (
        "a decorated labelled year",
        "xg",
        {"title": PRE, "year": "B.E. 2478", "date": "2478-01-31", "calendar": "buddhist"},
        "",
        "1936",
        "1936-01-31",
    ),
    (
        # The cue date names a year the title does not, so it dates another
        # document: it stands as found, and its month settles nothing here.
        "a source date naming another local year",
        "xg",
        {"title": PRE},
        SOURCE_OTHER,
        "1935",
        "1937-01-31",
    ),
    # --- the title grammar is the only thing that can answer -----------------
    (
        # Unlabelled and bare: nothing but the title says the year is local.
        "a bare year echoing the title, unlabelled",
        "xg",
        {"title": POST, "year": "2511", "calendar": ""},
        "",
        "1968",
        "",
    ),
    ("a title naming two years", "xg", {"title": TWO_YEARS}, "", "1943", ""),
    (
        "a title naming two years, with the year field",
        "xg",
        {"title": TWO_YEARS, "year": "2486", "calendar": "buddhist"},
        "",
        "1943",
        "",
    ),
    # --- the label names a calendar other than the jurisdiction's -------------
    (
        # Read as written: a label the jurisdiction does not declare is not
        # a licence to convert by the jurisdiction's rule.
        "another label on the year field",
        "xg",
        {"title": UNDATED, "year": "2478", "calendar": "hindu"},
        "",
        "2478",
        "",
    ),
    (
        "another label on the date field",
        "xg",
        {"title": UNDATED, "date": "2478-05-20", "calendar": "hindu"},
        "",
        "2478",
        "",
    ),
    (
        # The generic rule for that calendar converts the year; the month and
        # day are of that calendar's grid and do not carry.
        "another label the generic rule knows",
        "xg",
        {"title": UNDATED, "date": "2016-05-20", "calendar": "ethiopian"},
        "",
        "2024",
        "",
    ),
    (
        # The title says which year this is, whatever the label says.
        "another label, echoing the title",
        "xg",
        {"title": PRE, "year": "2478", "calendar": "hindu"},
        "",
        "1935",
        "",
    ),
    # --- fields that state no year ------------------------------------------
    (
        "the unknown-year sentinel in the year field",
        "xg",
        {"title": UNDATED, "year": "0001"},
        "",
        "",
        "",
    ),
    ("the zero sentinel in the year field", "xg", {"title": UNDATED, "year": "0000"}, "", "", ""),
    (
        # A five-digit run is not a year, no four of its digits are either, and
        # a date-shaped value with one is no date a reader can parse.
        "a five-digit year in the date field",
        "xg",
        {"title": UNDATED, "date": "12024-01-01"},
        "",
        "",
        "",
    ),
    ("date field naming no year", "xg", {"title": POST, "date": "unknown"}, "", "1968", "unknown"),
    (
        # A segment is four digits, so a three-digit year is carried padded.
        "a three-digit year is carried padded",
        "xg",
        {"title": "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 999", "year": "999"},
        "",
        "0456",
        "",
    ),
]


@pytest.mark.parametrize(
    ("case", "code", "metadata", "source", "expected_year", "expected_date"),
    GRID,
    ids=[row[0] for row in GRID],
)
def test_year_and_date_resolution(
    case: str,
    code: str,
    metadata: dict[str, object],
    source: str,
    expected_year: str,
    expected_date: str,
) -> None:
    desc = _resolve(code, metadata, source)
    assert (desc.year, desc.raw_date) == (expected_year, expected_date), case


@pytest.mark.parametrize(
    ("case", "code", "metadata", "source", "expected_year", "expected_date"),
    GRID,
    ids=[row[0] for row in GRID],
)
def test_every_year_path_answers_the_same(
    case: str,
    code: str,
    metadata: dict[str, object],
    source: str,
    expected_year: str,
    expected_date: str,
) -> None:
    """Three callers read a year from one metadata: the URI path, `resolve_year`
    and the stored-year helper. They must not disagree about the same document.
    """
    from codify.pipeline.enrich.metadata import gregorian_year

    try_load_config.cache_clear()
    title = str(metadata.get("title") or "")
    resolved = stages.resolve_year(dict(metadata), code, title=title)
    stored = gregorian_year(dict(metadata), code, title=title)
    # These two read the same metadata and must never differ; three rounds of
    # review found them disagreeing about one document.
    assert resolved == (f"{stored:04d}" if stored is not None else ""), case
    if source:
        # The descriptor refines the year with a month only the source states,
        # which the two helpers are never handed.
        return
    # And the descriptor itself, which knows nothing the other two do not.
    try_load_config.cache_clear()
    assert _resolve(code, metadata, source).year == resolved, case
    # Against the twin declaring no title grammar, on the same three readings.
    twin = WITHOUT_IDENTITY[code]
    try_load_config.cache_clear()
    twin_resolved = stages.resolve_year(dict(metadata), twin, title=title)
    twin_stored = gregorian_year(dict(metadata), twin, title=title)
    assert twin_resolved == (f"{twin_stored:04d}" if twin_stored is not None else ""), case
    try_load_config.cache_clear()
    assert _resolve(twin, metadata, source).year == twin_resolved, case


#: Each jurisdiction of the grid paired with its twin declaring no title grammar,
#: so every cell runs on both sides of that axis.
WITHOUT_IDENTITY = {"xg": "xg2", "xn": "xn2", "xo": "xo2", "xs": "xs", "xe": "xe", "xei": "xe"}

#: Cells whose local-ness shows only by echoing the title. With no grammar
#: there is nothing to echo, so the model's word is the only reading left.
UNECHOED = {
    # A title-only year with no grammar is read as written, as it always was.
    "title only, pre-reform": ("2478", ""),
    "title only, post-reform": ("2511", ""),
    "title only, no reform declared": ("2478", ""),
    "custom epoch, title only, with a grammar": ("2478", ""),
    "date field naming no year": ("2511", "unknown"),
    "unlabelled local date echoing the title": ("2511", "2511-09-09"),
    "unlabelled local date, month grid not Gregorian": ("2511", "2511-09-09"),
    # Same: nothing marks it local, so it stays as the model wrote it. Whether a
    # model date should be validated at all is a question for every calendar.
    "unlabelled local date whose day does not exist": ("2511", "2511-04-31"),
    "custom epoch, unlabelled echo": ("2478", "2478-09-09"),
    # Read as written and carried padded; only the grammar side converts it.
    "a three-digit year is carried padded": ("0999", ""),
    # With no title to conflict with, the date is the one the model stated,
    # converted on its own year; both sides agree on the document's.
    "an echoed year with a date naming another year": ("1935", "1938-05-20"),
    # No grammar, so the bare local year is read as written; the same title with
    # two year runs names no single one, and nothing else states it.
    "a bare year echoing the title, unlabelled": ("2511", ""),
    "another label, echoing the title": ("2478", ""),
    "an unlabelled echo, month grid not Gregorian": ("2478", "2478-02-20"),
    "a title naming two years": ("", ""),
    "a title naming two years, with the year field": ("1943", ""),
    # No title year to disagree with, so the date off the document is the
    # document's own and states the year.
    "a source date naming another local year": ("1937", "1937-01-31"),
}


def test_a_supplied_title_is_evidence_and_a_filename_is_not() -> None:
    """The title a caller hands `resolve_year` goes through the declared grammar
    as a metadata title does; a filename stem only ever lends a bare year."""
    try_load_config.cache_clear()
    assert stages.resolve_year({}, "xg", title=POST) == "1968"
    assert stages.resolve_year({"title": POST}, "xg", title=POST) == "1968"
    try_load_config.cache_clear()
    desc = stages.resolve_descriptors(
        {"number": ""},
        jurisdiction_code="xg",
        source_bytes=b"",
        fallback_stem=POST,
        classification_text="",
    )
    assert (desc.year, desc.raw_date) == ("2511", "")


def test_a_padded_date_still_states_its_month() -> None:
    """A model pads the field. The month lookup matches on the canonical value,
    not on whatever whitespace came with it."""
    assert month_day_stating_this_year({"date": " 2478-02-29 "}, "2478") == (2, 29)


def test_a_decorated_year_converts_with_a_month() -> None:
    """The era written beside the year reaches the reform shift, which must read
    the same number the conversion did rather than the decorated string."""
    try_load_config.cache_clear()
    assert labelled_year_as_gregorian("B.E. 2478", "buddhist", "xg", (1, 31)) == 1936


@pytest.mark.parametrize(
    ("case", "code", "metadata", "source", "expected_year", "expected_date"),
    GRID,
    ids=[row[0] for row in GRID],
)
def test_the_same_grid_without_a_title_grammar(
    case: str,
    code: str,
    metadata: dict[str, object],
    source: str,
    expected_year: str,
    expected_date: str,
) -> None:
    """The date resolution is the title grammar's business only where the title
    states the year. Everything a date settles must settle the same either way.
    """
    twin = _resolve(WITHOUT_IDENTITY[code], metadata, source)
    assert (twin.year, twin.raw_date) == UNECHOED.get(case, (expected_year, expected_date)), case


#: What the shipped run stored, read off the pre-branch function: the replay arm
#: has to reproduce these, conversions the config gained since included.
REPLAY = [
    ("a title-only year, with a country", {"title": POST}, "xg", 2511),
    ("a title-only year, no country", {"title": POST}, "", 2511),
    ("a labelled year", {"year": "2478", "calendar": "buddhist"}, "xg", 1935),
    (
        "a labelled year beside a first-quarter date",
        {"year": "2478", "date": "2478-01-31", "calendar": "buddhist"},
        "xg",
        1935,
    ),
    ("a labelled year, custom epoch", {"year": "2478", "calendar": "buddhist"}, "xe", 1935),
    (
        "a labelled year, custom epoch and a grammar",
        {"year": "2478", "calendar": "buddhist"},
        "xei",
        1935,
    ),
    ("a year already Gregorian", {"year": "1968"}, "xg", 1968),
]


@pytest.mark.parametrize(("case", "metadata", "code", "stored"), REPLAY, ids=[r[0] for r in REPLAY])
def test_the_replay_arm_stores_what_the_shipped_run_stored(
    case: str, metadata: dict[str, object], code: str, stored: int
) -> None:
    """A replay reproduces a checkpointed answer. Every conversion this branch
    added is a conversion that run never made."""
    from codify.pipeline.enrich.metadata import gregorian_year

    try_load_config.cache_clear()
    assert gregorian_year(dict(metadata), code, legacy=True) == stored, case


#: A year below 1000 is carried padded to the four digits a URI segment has and
#: stored as its number; a five-digit one is no year, and no date. Every source, both twins.
SOURCE_999 = "ให้ไว้ ณ วันที่ ๓๑ มกราคม พ.ศ. ๙๙๙\n"
SOURCE_12024 = "ให้ไว้ ณ วันที่ ๓๑ มกราคม พ.ศ. ๑๒๐๒๔\n"
TITLE_999 = "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 999"
TITLE_12024 = "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 12024"
EDGES = [
    # (case, code, metadata, source, uri year, date)
    ("title, 999", "xg", {"title": TITLE_999}, "", "0456", ""),
    # A title year with no grammar is read by the generic token rule, which as
    # on main takes only a four-digit year.
    ("title, 999, no grammar", "xg2", {"title": TITLE_999}, "", "", ""),
    ("title, 12024", "xg", {"title": TITLE_12024}, "", "", ""),
    ("title, 12024, no grammar", "xg2", {"title": TITLE_12024}, "", "", ""),
    (
        "labelled year, 999",
        "xg",
        {"title": UNDATED, "year": "999", "calendar": "buddhist"},
        "",
        "0456",
        "",
    ),
    (
        "labelled year, 999, no grammar",
        "xg2",
        {"title": UNDATED, "year": "999", "calendar": "buddhist"},
        "",
        "0456",
        "",
    ),
    (
        "labelled year, 12024",
        "xg",
        {"title": UNDATED, "year": "12024", "calendar": "buddhist"},
        "",
        "",
        "",
    ),
    (
        "labelled year, 12024, no grammar",
        "xg2",
        {"title": UNDATED, "year": "12024", "calendar": "buddhist"},
        "",
        "",
        "",
    ),
    (
        "labelled date, 999",
        "xg",
        {"title": UNDATED, "date": "999-01-31", "calendar": "buddhist"},
        "",
        "0457",
        "0457-01-31",
    ),
    (
        "labelled date, 999, no grammar",
        "xg2",
        {"title": UNDATED, "date": "999-01-31", "calendar": "buddhist"},
        "",
        "0457",
        "0457-01-31",
    ),
    (
        "labelled date, 12024",
        "xg",
        {"title": UNDATED, "date": "12024-01-31", "calendar": "buddhist"},
        "",
        "",
        "",
    ),
    (
        "labelled date, 12024, no grammar",
        "xg2",
        {"title": UNDATED, "date": "12024-01-31", "calendar": "buddhist"},
        "",
        "",
        "",
    ),
    (
        "unlabelled echo, 999",
        "xg",
        {"title": TITLE_999, "date": "999-05-05", "calendar": ""},
        "",
        "0456",
        "0456-05-05",
    ),
    (
        "unlabelled echo, 999, no grammar",
        "xg2",
        {"title": TITLE_999, "date": "999-05-05", "calendar": ""},
        "",
        "0999",
        "0999-05-05",
    ),
    (
        "unlabelled echo, 12024",
        "xg",
        {"title": TITLE_12024, "date": "12024-05-05", "calendar": ""},
        "",
        "",
        "",
    ),
    (
        "unlabelled echo, 12024, no grammar",
        "xg2",
        {"title": TITLE_12024, "date": "12024-05-05", "calendar": ""},
        "",
        "",
        "",
    ),
    ("source date, 999", "xg", {"title": UNDATED}, SOURCE_999, "0457", "0457-01-31"),
    ("source date, 999, no grammar", "xg2", {"title": UNDATED}, SOURCE_999, "0457", "0457-01-31"),
    ("source date, 12024", "xg", {"title": UNDATED}, SOURCE_12024, "", ""),
    ("source date, 12024, no grammar", "xg2", {"title": UNDATED}, SOURCE_12024, "", ""),
]


@pytest.mark.parametrize(
    ("case", "code", "metadata", "source", "expected_year", "expected_date"),
    EDGES,
    ids=[r[0] for r in EDGES],
)
def test_a_year_at_the_edge_is_canonical_or_nothing(
    case: str,
    code: str,
    metadata: dict[str, object],
    source: str,
    expected_year: str,
    expected_date: str,
) -> None:
    from codify.pipeline.enrich.metadata import gregorian_year

    desc = _resolve(code, metadata, source)
    assert (desc.year, desc.raw_date) == (expected_year, expected_date), case
    if desc.raw_date:
        # Every date emitted is one a reader can parse.
        assert date.fromisoformat(desc.raw_date), case
    if source:
        # The helpers never see the source; the source-date rows test the
        # descriptor alone, as the grid does.
        return
    title = str(metadata.get("title") or "")
    try_load_config.cache_clear()
    assert stages.resolve_year(dict(metadata), code, title=title) == expected_year, case
    stored = gregorian_year(dict(metadata), code, title=title)
    assert stored == (int(expected_year) if expected_year else None), case
