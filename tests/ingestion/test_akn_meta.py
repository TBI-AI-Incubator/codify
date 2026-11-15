# ruff: noqa: E501  # AKN XML test fixtures: line wraps would change tested whitespace
"""What the strict schema refuses in the `<meta>` we emit ourselves."""

from __future__ import annotations

from lxml import etree

from codify.akn import AKN_NS
from codify.akn._schema import validate_akn
from codify.pipeline.enrich.akn_meta import normalise_akn_meta, normalise_akn_meta_if_changed

_META = """<meta><identification source="#codify">
 <FRBRWork><FRBRthis value="/akn/xz/act/2007/237/!main"/><FRBRuri value="/akn/xz/act/2007/237"/>
  <FRBRdate date="2007" name="Generation"/><FRBRauthor href="#codify"/><FRBRcountry value="xz"/></FRBRWork>
 <FRBRExpression><FRBRthis value="/akn/xz/act/2007/237/eng@2026-08-02"/>
  <FRBRuri value="/akn/xz/act/2007/237/eng@2026-08-02"/>
  <FRBRdate date="2026-08-02" name="Generation"/><FRBRauthor href="#codify"/>
  <FRBRlanguage language="eng"/></FRBRExpression>
 <FRBRManifestation><FRBRthis value="/akn/xz/act/2007/237/eng@2026-08-02.xml"/>
  <FRBRuri value="/akn/xz/act/2007/237/eng@2026-08-02.xml"/>
  <FRBRdate date="2026-08-02" name="Generation"/><FRBRauthor href="#codify"/></FRBRManifestation>
 </identification></meta>"""
_BODY = '<body><article eId="art_1"><num>1</num><content><p>Body.</p></content></article></body>'


def _doc(preamble: str = "") -> str:
    return (
        f'<akomaNtoso xmlns="{AKN_NS}"><act name="act">{_META}{preamble}{_BODY}</act></akomaNtoso>'
    )


def _dates(xml: str) -> list[str | None]:
    root = etree.fromstring(xml.encode())
    return [el.get("date") for el in root.iter(f"{{{AKN_NS}}}FRBRdate")]


def _uris(xml: str) -> list[str | None]:
    """Every FRBR URI in document order.

    A helper that reads only `FRBRdate` is how the first version of this change
    shipped a document whose date said 2007 and whose URI still said the day we
    ran. The row is built from both: `expression_date` off the element,
    `expression_uri` off the URI."""
    root = etree.fromstring(xml.encode())
    return [
        el.get("value")
        for el in root.iter()
        if isinstance(el.tag, str) and etree.QName(el).localname in ("FRBRthis", "FRBRuri")
    ]


class TestTheDocumentStartsInvalid:
    def test_the_stored_shape_fails_the_schema(self) -> None:
        """The premise. The defects are present in what ingest stores today."""
        import pytest
        from lxml.etree import DocumentInvalid

        with pytest.raises(DocumentInvalid):
            validate_akn(_doc(), strict=True)


class TestFrbrDates:
    def test_a_bare_year_becomes_a_date(self) -> None:
        assert _dates(normalise_akn_meta(_doc()))[0] == "2007-01-01"

    def test_a_known_date_of_the_same_year_wins(self) -> None:
        assert _dates(normalise_akn_meta(_doc(), work_date="2007-03-17"))[0] == "2007-03-17"

    def test_a_known_date_of_another_year_is_not_imposed(self) -> None:
        """The work URI year is the authority on which work this is; a date
        disagreeing with it belongs to something else."""
        assert _dates(normalise_akn_meta(_doc(), work_date="2019-05-01"))[0] == "2007-01-01"

    def test_a_non_iso_date_falls_back_rather_than_raising(self) -> None:
        """`raw_date` is blanked for non-Gregorian calendars, but a stray
        unparseable value must not take the ingest down with it."""
        assert _dates(normalise_akn_meta(_doc(), work_date="١٤٢٥"))[0] == "2007-01-01"

    def test_full_dates_are_left_alone(self) -> None:
        assert _dates(normalise_akn_meta(_doc()))[1:] == ["2026-08-02", "2026-08-02"]


class TestAnUndatedExpression:
    """`2026-08-02` in the fixture is the day the pipeline ran, which is what
    Cobalt stamps on every expression it builds. It is not a date the law has."""

    def test_it_takes_the_work_date(self) -> None:
        got = normalise_akn_meta(_doc(), work_date="2007-03-17", expression_undated=True)
        # The manifestation records when the file was produced, which genuinely
        # is the day we ran, so only the expression moves.
        assert _dates(got) == ["2007-03-17", "2007-03-17", "2026-08-02"]

    def test_the_uri_moves_with_the_date(self) -> None:
        """The row takes `expression_date` from the element and `expression_uri`
        from the URI, so a document that moves one and not the other makes the
        row disagree with itself. This is the defect the first version shipped."""
        got = normalise_akn_meta(_doc(), work_date="2007-03-17", expression_undated=True)
        assert _uris(got) == [
            "/akn/xz/act/2007/237/!main",
            "/akn/xz/act/2007/237",
            "/akn/xz/act/2007/237/eng@2007-03-17",
            "/akn/xz/act/2007/237/eng@2007-03-17",
            # The manifestation format survives the substitution.
            "/akn/xz/act/2007/237/eng@2007-03-17.xml",
            "/akn/xz/act/2007/237/eng@2007-03-17.xml",
        ]

    def test_restatement_replaces_the_whole_timestamp(self) -> None:
        timestamped = _doc().replace("eng@2026-08-02", "eng@2026-08-02T14:00:00.000001Z")
        got = normalise_akn_meta(timestamped, work_date="2007-03-17", expression_undated=True)
        assert all("eng@2007-03-17T" not in uri for uri in _uris(got) if uri)
        assert _uris(got)[2:6] == [
            "/akn/xz/act/2007/237/eng@2007-03-17",
            "/akn/xz/act/2007/237/eng@2007-03-17",
            "/akn/xz/act/2007/237/eng@2007-03-17.xml",
            "/akn/xz/act/2007/237/eng@2007-03-17.xml",
        ]

    def test_a_year_only_work_still_supplies_one(self) -> None:
        """The lane that most needs this has no `raw_date` at all: the year came
        from the URI, so the run date would otherwise survive."""
        got = normalise_akn_meta(_doc(), expression_undated=True)
        assert _dates(got)[:2] == ["2007-01-01", "2007-01-01"]
        assert "/akn/xz/act/2007/237/eng@2007-01-01" in _uris(got)

    def test_the_name_stops_claiming_generation(self) -> None:
        got = normalise_akn_meta(_doc(), expression_undated=True)
        root = etree.fromstring(got.encode())
        assert [el.get("name") for el in root.iter(f"{{{AKN_NS}}}FRBRdate")] == [
            "Generation",
            "Original",
            "Generation",
        ]

    def test_it_stays_schema_valid(self) -> None:
        validate_akn(normalise_akn_meta(_doc(), expression_undated=True), strict=True)

    def test_an_unresolved_work_year_is_left_alone(self) -> None:
        """Year 1 is `UNKNOWN_YEAR` reaching the element. The run date is at
        least visibly wrong; `0001-01-01` reads like something the law said."""
        unknown = _doc().replace('<FRBRdate date="2007" ', '<FRBRdate date="0001-01-01" ')
        got = normalise_akn_meta(unknown, expression_undated=True)
        assert _dates(got)[1:] == ["2026-08-02", "2026-08-02"]
        assert "/akn/xz/act/2007/237/eng@2026-08-02" in _uris(got)

    def test_two_work_dates_are_refused_rather_than_ranked(self) -> None:
        """Document order is not a ranking. Taking the first would read the
        generation date here and stamp the day we ran onto the expression under
        `name="Original"`, which is worse than the defect this exists to fix."""
        ambiguous = _doc().replace(
            '<FRBRdate date="2007" name="Generation"/>',
            '<FRBRdate date="2026-08-09" name="Generation"/>'
            '<FRBRdate date="2007-03-17" name="enactment"/>',
        )
        got = normalise_akn_meta(ambiguous, expression_undated=True)
        # Work block keeps both of its own; the expression is left where it was
        # rather than taking the generation date under an "Original" label.
        assert _dates(got)[2:] == ["2026-08-02", "2026-08-02"]
        assert "/akn/xz/act/2007/237/eng@2026-08-02" in _uris(got)

    def test_the_lanes_that_read_a_real_date_are_not_opted_in(self) -> None:
        """FORMEX and native AKN source their own expression date, so the
        default must leave the element exactly as it found it."""
        got = normalise_akn_meta(_doc(), work_date="2007-03-17")
        assert _dates(got)[1:] == ["2026-08-02", "2026-08-02"]
        assert "/akn/xz/act/2007/237/eng@2026-08-02" in _uris(got)


class TestTheIngestLaneOptsIn:
    def test_run_enrich_passes_asks_for_the_undated_treatment(self) -> None:
        """Without this the whole feature is a parameter nobody passes. Asserted
        on the source because the pass list is built inside an async function
        that needs an LLM client to reach."""
        from pathlib import Path

        import codify.pipeline.stages as stages

        src = Path(stages.__file__).read_text(encoding="utf-8")
        assert "expression_undated=True" in src


class TestAnEmptyWorkDate:
    """The shape a document takes when no year resolved at all, so the work URI
    has no year segment either. Found by ingesting a real scan, not by the
    bare-year fixtures above."""

    _EMPTY = _doc().replace(
        '<FRBRdate date="2007" name="Generation"/>', '<FRBRdate date="" name="Generation"/>'
    )

    def test_a_supplied_work_date_fills_it(self) -> None:
        got = normalise_akn_meta(self._EMPTY, work_date="1999-04-01")
        assert _dates(got)[0] == "1999-04-01"
        validate_akn(got, strict=True)

    def test_without_one_it_is_left_refused_rather_than_invented(self) -> None:
        """The expression date can be the ingest date, so filling from it
        would stamp an old act as generated today. An unresolved date
        the gate refuses stays visible; a plausible wrong one does not."""
        assert _dates(normalise_akn_meta(self._EMPTY))[0] == ""

    def test_the_expression_date_is_never_borrowed(self) -> None:
        assert "2026-08-02" not in (_dates(normalise_akn_meta(self._EMPTY))[0] or "")


class TestReportingAChange:
    def test_reformatting_alone_is_not_a_change(self) -> None:
        """The backfill rewrites stored AKN on this answer. A document that only
        differs from lxml's layout must not earn a rewrite and a re-embed."""
        clean = normalise_akn_meta(_doc(), work_date="2007-03-17")
        compact = clean.replace("\n", "").replace("  ", "")
        assert normalise_akn_meta_if_changed(compact) is None

    def test_a_real_defect_is_a_change(self) -> None:
        assert normalise_akn_meta_if_changed(_doc()) is not None


class TestTheResult:
    def test_the_normalised_document_passes_the_schema(self) -> None:
        """One document carrying both date defects comes out valid, which is
        what lets the write path enforce rather than report."""
        validate_akn(normalise_akn_meta(_doc(), work_date="2007-03-17"), strict=True)

    def test_running_it_twice_changes_nothing(self) -> None:
        once = normalise_akn_meta(_doc())
        assert normalise_akn_meta(once) == once
