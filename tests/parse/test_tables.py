"""Tests for the deterministic pipe-table nester."""

from __future__ import annotations

import pytest

from codify.pipeline.enrich.tables import nest_pipe_tables


def test_header_and_body_become_table_rows():
    out = nest_pipe_tables(
        [
            "| Name | Age |",
            "| --- | --- |",
            "| Alice | 30 |",
            "| Bob | 25 |",
        ]
    )
    assert out == [
        "TABLE",
        "  TR",
        "    TH",
        "      Name",
        "    TH",
        "      Age",
        "  TR",
        "    TC",
        "      Alice",
        "    TC",
        "      30",
        "  TR",
        "    TC",
        "      Bob",
        "    TC",
        "      25",
    ]


def test_blank_lines_between_rows_do_not_break_the_table():
    out = nest_pipe_tables(
        [
            "| Name | Age |",
            "",
            "| --- | --- |",
            "",
            "| Alice | 30 |",
        ]
    )
    assert out.count("TABLE") == 1
    assert "      Alice" in out


def test_a_lone_pipe_row_stays_plain_text():
    """One row alone isn't a table: no second row to pair it with."""
    out = nest_pipe_tables(["Intro text.", "| just one row |"])
    assert out == ["Intro text.", "| just one row |"]


def test_non_table_lines_pass_through_unchanged():
    out = nest_pipe_tables(["Lead-in sentence.", "More prose."])
    assert out == ["Lead-in sentence.", "More prose."]


def test_leading_empty_cell_extends_the_origin_cells_rowspan():
    out = nest_pipe_tables(
        [
            "| Code | Name |",
            "| --- | --- |",
            "| 01 | Alpha |",
            "| | Beta |",
        ]
    )
    assert "    TC{rowspan 2}" in out
    assert out[out.index("    TC{rowspan 2}") + 1] == "      01"
    # the continuation row keeps only its own real cell
    assert out[-2:] == ["    TC", "      Beta"]


def test_a_double_rowspan_bumps_twice():
    out = nest_pipe_tables(
        [
            "| Code | Name |",
            "| --- | --- |",
            "| 01 | Alpha |",
            "| | Beta |",
            "| | Gamma |",
        ]
    )
    assert "    TC{rowspan 3}" in out


class TestATableOutsideABodyFillWindow:
    """Measured on a real corpus: of 22 versions whose OCR held a table that
    never reached the AKN, 9 had their rows in the document as plain text. The
    rows were fine; nothing had nested them."""

    def test_a_header_and_separator_alone_are_a_table(self) -> None:
        """A separator row is the author declaring a table. The body sitting on
        the next page does not make the header prose."""
        lines = [
            "| Serial | Name | Offence |",
            "|---|---|---|",
        ]
        out = nest_pipe_tables(lines)
        assert "TABLE" in out
        assert sum(1 for line in out if line.strip() == "TR") == 1
        assert sum(1 for line in out if line.strip() == "TH") == 3

    def test_a_lone_row_with_no_separator_is_still_prose(self) -> None:
        """Without a separator there is nothing saying this is a table, and a
        sentence containing pipes is not one."""
        lines = ["| this reads like a row but stands alone |"]
        assert "TABLE" not in nest_pipe_tables(lines)

    def test_rows_split_by_a_blank_line_stay_one_table(self) -> None:
        lines = [
            "| Serial | Item |",
            "|---|---|",
            "",
            "| 1 | first |",
            "| 2 | second |",
        ]
        out = nest_pipe_tables(lines)
        assert out.count("TABLE") == 1
        assert sum(1 for line in out if line.strip() == "TR") == 3


class TestTheRenderedMarkupParses:
    """Nesting rows is only worth anything if Bluebell then builds a table from
    them, in the container they were sitting in."""

    @staticmethod
    def _tables_in(bluebell: str) -> list[str]:
        from codify.akn._schema import parse_xml
        from codify.akn.bluebell import bluebell_to_akn

        xml, _, errors = bluebell_to_akn(bluebell, country="xa", language="ara")
        assert not errors, errors
        root = parse_xml(xml.encode())
        parents = {c: p for p in root.iter() for c in p}
        found = []
        for el in root.iter():
            if el.tag.endswith("}table"):
                chain, node = [], parents.get(el)
                while node is not None:
                    chain.append(node.tag.split("}")[-1])
                    node = parents.get(node)
                found.append(" < ".join(chain))
        return found

    def test_an_indented_table_stays_in_its_section(self) -> None:
        """Rendered at column 0 the markup escapes its container, and a table
        that reparses somewhere else has moved the law's content."""
        doc = "\n".join(
            nest_pipe_tables(
                [
                    "BODY",
                    "",
                    "  SECTION 1 - x",
                    "",
                    "    | Serial | Item |",
                    "    |---|---|",
                    "    | 1 | first |",
                ]
            )
        )
        assert self._tables_in(doc) == ["content < section < body < act < akomaNtoso"]

    def test_a_header_and_separator_alone_parse_as_a_table(self) -> None:
        doc = "\n".join(
            nest_pipe_tables(
                ["BODY", "", "  SECTION 1 - x", "", "    | Serial | Item |", "    |---|---|"]
            )
        )
        assert self._tables_in(doc) == ["content < section < body < act < akomaNtoso"]

    def test_a_run_opening_with_a_separator_has_no_header_row(self) -> None:
        """Its first data row is data; promoting it to TH would invent a header."""
        out = nest_pipe_tables(["|---|---|", "| 1 | first |", "| 2 | second |"])
        assert "TH" not in [line.strip() for line in out]
        assert sum(1 for line in out if line.strip() == "TC") == 4

    def test_separator_rows_alone_are_not_a_table(self) -> None:
        assert "TABLE" not in nest_pipe_tables(["|---|---|", "| :--- | :--- |"])


class TestFrontMatterHoldsATableToo:
    """The scaffold names no root; the parser takes it as an argument. A table
    indented under PREFACE or PREAMBLE therefore parses where it sits, and
    scoping the pass to the body would drop it for no reason."""

    @staticmethod
    def _where(scaffold: str) -> str:
        from codify.akn._schema import parse_xml
        from codify.akn.bluebell import bluebell_to_akn
        from codify.pipeline.enrich.scaffold import (
            BodyFillResponse,
            assemble_filled_scaffold,
        )

        filled = assemble_filled_scaffold(scaffold, {}, BodyFillResponse(bodies=[]))
        xml, _, errors = bluebell_to_akn(filled, country="xa", language="eng")
        assert not errors, errors
        root = parse_xml(xml.encode())
        parents = {c: p for p in root.iter() for c in p}
        for el in root.iter():
            if el.tag.endswith("}table"):
                chain, node = [], parents.get(el)
                while node is not None:
                    chain.append(node.tag.split("}")[-1])
                    node = parents.get(node)
                return " < ".join(chain)
        return "no table"

    def test_a_preface_table_parses_inside_the_preface(self) -> None:
        scaffold = (
            "PREFACE\n\n  An Act.\n\n  | A | B |\n  |---|---|\n  | 1 | 2 |\n\n"
            "BODY\n\nSECTION 1 - Title\n\n  Text.\n"
        )
        assert self._where(scaffold) == "preface < act < akomaNtoso"

    def test_a_preamble_table_parses_inside_the_preamble(self) -> None:
        scaffold = (
            "PREAMBLE\n\n  Whereas.\n\n  | A | B |\n  |---|---|\n  | 1 | 2 |\n\n"
            "BODY\n\nSECTION 1 - Title\n\n  Text.\n"
        )
        assert self._where(scaffold) == "preamble < act < akomaNtoso"

    def test_a_body_table_still_parses_in_its_section(self) -> None:
        scaffold = "BODY\n\nSECTION 1 - Title\n\n  | A | B |\n  |---|---|\n  | 1 | 2 |\n"
        assert self._where(scaffold) == "content < section < body < act < akomaNtoso"


class TestAFrontMatterTableSurvivesTheRoundTrip:
    """AKN to Bluebell and back is how a correction re-enters the pipeline, so a
    table the emitter cannot express is a table an edit would silently destroy."""

    def test_a_preface_table_returns_to_the_preface(self) -> None:
        from codify.akn._schema import parse_xml
        from codify.akn.bluebell import akn_xml_to_bluebell, bluebell_to_akn

        source = (
            "PREFACE\n\n  An Act.\n\n  TABLE\n    TR\n      TH\n        A\n      TH\n"
            "        B\n    TR\n      TC\n        1\n      TC\n        2\n\n"
            "BODY\n\nSECTION 1 - Title\n\n  Text.\n"
        )
        first, _, errors = bluebell_to_akn(source, country="xa", language="eng")
        assert not errors, errors
        again, _, errors = bluebell_to_akn(akn_xml_to_bluebell(first), country="xa", language="eng")
        assert not errors, errors

        for xml in (first, again):
            root = parse_xml(xml.encode())
            parents = {c: p for p in root.iter() for c in p}
            tables = [el for el in root.iter() if el.tag.endswith("}table")]
            assert len(tables) == 1, xml
            assert parents[tables[0]].tag.endswith("}preface"), xml


class TestTheAnchorlessPathReachesAParsedTable:
    """A document with no anchors never touches the scaffold, so the wiring in
    the fallback is the only thing carrying its table."""

    def test_a_pipe_table_survives_a_document_with_no_anchors(self) -> None:
        from codify.akn._schema import parse_xml
        from codify.akn.bluebell import bluebell_to_akn
        from codify.pipeline.enrich.structure import _verbatim_single_section

        bluebell = _verbatim_single_section(
            "Some preamble prose.\n\n| Serial | Item |\n|---|---|\n| 1 | first |\n"
        )
        xml, _, errors = bluebell_to_akn(bluebell, country="xa", language="eng")
        assert not errors, errors
        root = parse_xml(xml.encode())
        assert [el for el in root.iter() if el.tag.endswith("}table")], bluebell
        assert "|" not in xml


class TestAContainerOnlyDocumentNeverReachesAssembly:
    """Anchors that are all containers yield no body-fill windows, so the
    scaffold is returned before assembly runs. Nesting at build time is what
    carries a front-matter table through that path."""

    def test_a_preface_table_survives_with_only_container_anchors(self) -> None:
        from codify.akn._schema import parse_xml
        from codify.akn.bluebell import bluebell_to_akn
        from codify.pipeline.enrich.anchors import StructuralAnchor
        from codify.pipeline.enrich.scaffold import scaffold_from_anchors

        chapter = StructuralAnchor(
            kind="chapter",
            keyword="CHAPTER",
            number="1",
            char_offset=0,
            line=1,
            matched_text="CHAPTER 1",
        )
        scaffold, _ = scaffold_from_anchors(
            [chapter],
            preface="An Act.\n\n| A | B |\n|---|---|\n| 1 | 2 |",
            preamble=None,
            country="xa",
        )
        assert "TABLE" in scaffold, scaffold
        xml, _, errors = bluebell_to_akn(scaffold, country="xa", language="eng")
        assert not errors, errors
        root = parse_xml(xml.encode())
        parents = {c: p for p in root.iter() for c in p}
        tables = [el for el in root.iter() if el.tag.endswith("}table")]
        assert len(tables) == 1, xml
        assert parents[tables[0]].tag.endswith("}preface"), xml


class TestTheInstructionAndTheExtractorAgree:
    """The extractor consumes a shape nothing asked for, so whether a table arrived
    as one was the model's default. These pin that transcription is asked for the
    shape and that the extractor builds a table from it. Whether the model obeys is
    a run, not a test."""

    @staticmethod
    def _ocr_prompt() -> str:
        from pathlib import Path

        import codify.pipeline.enrich.tables as tables_mod

        prompts = Path(tables_mod.__file__).parent / "prompts" / "ocr_system.txt"
        return prompts.read_text(encoding="utf-8")

    def test_transcription_is_asked_for_pipe_rows(self) -> None:
        prompt = self._ocr_prompt()
        assert "| cell | cell |" in prompt, prompt
        assert "|---|---|" in prompt, prompt

    def test_the_extractor_builds_a_table_from_the_shape_asked_for(self) -> None:
        """The instruction's own example, through the extractor and the parser."""
        from codify.akn._schema import parse_xml
        from codify.akn.bluebell import bluebell_to_akn

        rows = ["| Rate | Item |", "|---|---|", "| 4 | tobacco |", "|  | tobacco, second |"]
        nested = nest_pipe_tables(rows)
        assert "TABLE" in nested
        body = "\n".join("    " + line if line.strip() else "" for line in nested)
        xml, _, errors = bluebell_to_akn(
            f"BODY\n\nSECTION 1 - x\n\n{body}\n", country="xa", language="eng"
        )
        assert not errors, errors
        root = parse_xml(xml.encode())
        assert [el for el in root.iter() if el.tag.endswith("}table")], xml

    def test_an_empty_cell_continues_the_one_above_it(self) -> None:
        """The extractor reads a blank cell as a rowspan continuation, so the
        instruction asks for a blank only where the source means one. Asking for
        a blank to mean an empty cell would have meant the two disagreed."""
        out = nest_pipe_tables(["| A | B |", "|---|---|", "| 1 |  |", "| 2 | y |"])
        assert "    TH{rowspan 2}" in out, out
        assert sum(1 for line in out if line.strip() == "TR") == 3

    def test_the_instruction_matches_that_convention(self) -> None:
        prompt = self._ocr_prompt()
        assert "continuing the cell above" in prompt, prompt


# A synthetic jurisdiction rather than a live one: this file asserts on prompts,
# not on any real legal text.
def _xa_sections(count: int, table_at: int) -> str:
    """A source long enough to window more than once, with a table in one section."""
    out = ["Part I", "General provisions", ""]
    for i in range(1, count + 1):
        out += [f"Section {i}", f"Heading {i}", f"Body text for section {i}."]
        if i == table_at:
            out += ["| A | B |", "|---|---|", "| 1 | 2 |"]
        out += [""]
    return "\n".join(out)


_XA_TEXT = """Part I
General provisions

Chapter I
Introduction

Section 1
Purpose
This Act governs the activity of organisations.

Section 2
Definitions
The following terms have these meanings.
"""


class TestTheBodyFillRuleIsConditional:
    """The rule reaches only the windows that carry a table, which is what these
    pin. A window with no table has no use for it. Whether an unscoped rule would
    also cost anything is not settled and is not what this asserts."""

    def test_the_prompt_file_itself_says_nothing_about_tables(self) -> None:
        from pathlib import Path

        import codify.pipeline.enrich.tables as tables_mod

        prompt = (
            Path(tables_mod.__file__).parent / "prompts" / "structure_body_fill.txt"
        ).read_text(encoding="utf-8")
        assert "| cell | cell |" not in prompt, prompt

    @staticmethod
    async def _calls_for(source: str) -> list[tuple[str, str]]:
        """The (user prompt, system prompt) pairs body-fill was called with."""
        from codify.pipeline.enrich.structure import text_to_bluebell_scaffolded

        from .test_text_to_bluebell_scaffolded import _RecordingLLMClient

        client = _RecordingLLMClient({})
        await text_to_bluebell_scaffolded(source, client=client, country="xa", doctype="act")
        return list(client.calls)

    @pytest.mark.asyncio
    async def test_only_the_window_holding_the_table_is_told_about_tables(self) -> None:
        """Per window, not per document, which needs a source of several windows:
        on one window the two are the same guard. Driven through the real call, so
        it fails if the guard is deleted or widened to the document."""
        from codify.pipeline.enrich.structure import TABLE_ROWS_RULE
        from codify.pipeline.enrich.tables import is_pipe_row

        calls = await self._calls_for(_xa_sections(24, table_at=2))
        assert len(calls) >= 3, f"need several windows to tell the two guards apart: {len(calls)}"
        for user, system in calls:
            carries_table = any(is_pipe_row(line) for line in user.splitlines())
            assert (TABLE_ROWS_RULE in system) == carries_table, (carries_table, system[-200:])
        assert sum(TABLE_ROWS_RULE in system for _, system in calls) == 1, [
            s[-80:] for _, s in calls
        ]

    @pytest.mark.asyncio
    async def test_the_rule_lands_before_the_prompt_closes_itself(self) -> None:
        """The prompt ends by saying the rules above are the only ones in force and
        nothing follows the source tag. A rule appended after that is either ignored
        or teaches the model that text past the line still counts."""
        from codify.pipeline.enrich.structure import TABLE_ROWS_RULE

        calls = await self._calls_for(_xa_sections(24, table_at=2))
        carrying = [s for _, s in calls if TABLE_ROWS_RULE in s]
        assert carrying, "no window got the rule"
        for system in carrying:
            assert system.index(TABLE_ROWS_RULE) < system.index("Untrusted input"), system
            assert system.rstrip().endswith("raw text below."), system[-120:]

    @pytest.mark.asyncio
    async def test_a_prose_only_source_is_never_told_about_tables(self) -> None:
        from codify.pipeline.enrich.structure import TABLE_ROWS_RULE

        calls = await self._calls_for(_XA_TEXT)
        assert calls, "body-fill was never called"
        assert not any(TABLE_ROWS_RULE in system for _, system in calls), calls


class TestTheGuardAsksTheNester:
    """`is_pipe_row` is true of a lone pipe-shaped line the nester leaves as prose,
    so a guard built on it fires for a window with no table in it; a source line
    already reading `TABLE` fools a guard that only looks for one in the output."""

    def test_a_lone_row_is_not_a_table(self) -> None:
        from codify.pipeline.enrich.tables import has_table, is_pipe_row

        lone = ["Some prose.", "| a | b |", "More prose."]
        assert any(is_pipe_row(line) for line in lone)
        assert not has_table(lone)

    def test_a_source_line_reading_table_is_not_a_table(self) -> None:
        """`TABLE` is Bluebell's own keyword, so a guard that just looks for one in
        the nester's output fires on a heading that already says it."""
        from codify.pipeline.enrich.tables import has_table, nest_pipe_tables

        assert nest_pipe_tables(["TABLE"]) == ["TABLE"]
        assert not has_table(["TABLE"])
        assert not has_table(["  TABLE  ", "Some prose."])
        assert has_table(["TABLE", "| a | b |", "|---|---|", "| 1 | 2 |"])

    def test_the_shapes_the_nester_accepts_are_tables(self) -> None:
        from codify.pipeline.enrich.tables import has_table

        assert has_table(["| A | B |", "|---|---|"])
        assert has_table(["|---|---|", "| 1 | 2 |"])
        assert has_table(["| a | b |", "| c | d |"])
        assert not has_table(["An ordinary sentence."])


class TestAHeaderlessTableKeepsItsFirstRow:
    """The nester reads the first row as a header unless a separator opens the
    run, so the instruction asks for the separator first where there is none."""

    def test_a_separator_first_run_has_no_header_cells(self) -> None:
        out = nest_pipe_tables(["|---|---|", "| 1 | 2 |", "| 3 | 4 |"])
        assert "TABLE" in out
        assert "TH" not in [line.strip() for line in out]
        assert sum(1 for line in out if line.strip() == "TC") == 4

    def test_the_instruction_asks_for_that_shape(self) -> None:
        from pathlib import Path

        import codify.pipeline.enrich.tables as tables_mod

        prompt = (Path(tables_mod.__file__).parent / "prompts" / "ocr_system.txt").read_text(
            encoding="utf-8"
        )
        assert "no header row" in prompt, prompt
