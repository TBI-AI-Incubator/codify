"""Body-fill table loss restores source rows within their original anchor."""

from dataclasses import replace
from pathlib import Path

import pytest
from lxml import etree

from codify.pipeline.enrich.anchors import StructuralAnchor
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.scaffold import (
    BodyBlock,
    BodyFillResponse,
    assemble_filled_scaffold,
    scaffold_from_anchors,
)
from codify.pipeline.enrich.table_fidelity import preserve_source_tables

TABLE = (
    (Path(__file__).parents[1] / "pipeline/fixtures/ministerial_table.txt").read_text().splitlines()
)


def _case(lines: list[str]):
    source = (
        "SECTION 1\nBefore original.\n"
        + "\n".join(TABLE)
        + "\nAfter original.\nSECTION 2\nOther source.\n"
    )
    anchors = [
        StructuralAnchor(
            kind="section",
            keyword="SECTION",
            number=str(i),
            char_offset=offset,
            line=1,
            matched_text=f"SECTION {i}",
            akn_eid=f"sec_{i}",
        )
        for i, offset in [(1, 0), (2, source.index("SECTION 2"))]
    ]
    bodies = {
        "sec_1": BodyBlock(eid="sec_1", lines=lines),
        "sec_2": BodyBlock(eid="sec_2", lines=["Untouched model body."]),
    }
    return source, anchors, bodies


@pytest.mark.parametrize("change", ["omit", "number", "column", "order", "refusal"])
def test_source_table_fidelity_rejects_changed_cells(change: str) -> None:
    lines = list(TABLE)
    if change == "omit":
        lines = lines[:2]
    elif change == "number":
        lines = [x.replace("2016", "2017") for x in lines]
    elif change == "column":
        lines = ["|" + "|".join(x.split("|")[2:]) for x in lines]
    elif change == "order":
        lines[-2:] = reversed(lines[-2:])
    else:
        lines = ["Unable to transcribe this table."]
    source, anchors, bodies = _case(lines)
    literal_eids = preserve_source_tables(source, anchors, bodies)
    assert literal_eids == frozenset({"sec_1"})
    assert bodies["sec_2"].lines == ["Untouched model body."]
    scaffold, index = scaffold_from_anchors(anchors)
    filled = assemble_filled_scaffold(
        scaffold,
        index,
        BodyFillResponse(bodies=list(bodies.values())),
        literal_body_eids=literal_eids,
    )
    akn = parse_to_akn(filled, country="xa", doctype="act", date="2026-01-01", number="1")
    root = etree.fromstring(akn.encode())
    tables = root.xpath('//*[local-name()="table"]')
    assert len(tables) == 1
    assert tables[0].get("eId") == "sec_1__table_1"
    content = "".join(tables[0].itertext())
    for row in TABLE:
        if row.startswith("|---"):
            continue
        for cell in row.split("|")[1:-1]:
            assert cell.strip() in content
    assert "Other source." not in akn
    assert akn.count("Untouched model body.") == 1


def test_whitespace_only_table_changes_keep_model_prose() -> None:
    lines = ["Improved surrounding prose.", *[x.replace(" |", "   |") for x in TABLE]]
    source, anchors, bodies = _case(lines)
    original = bodies["sec_1"]
    literal_eids = preserve_source_tables(source, anchors, bodies)
    assert not literal_eids
    assert bodies["sec_1"] is original


def test_table_guard_does_not_replace_plain_body() -> None:
    source, anchors, bodies = _case(["Model prose."])
    source = source.replace("\n".join(TABLE), "No table.")
    anchors[1] = replace(anchors[1], char_offset=source.index("SECTION 2"))
    literal_eids = preserve_source_tables(source, anchors, bodies)
    assert not literal_eids
    assert bodies["sec_1"].lines == ["Model prose."]


def test_restored_table_body_keeps_citation_labels_in_actual_act_parser() -> None:
    source, anchors, bodies = _case(["A summary without the table."])
    before = "P.7/2021, beginning inventory.\na. First requirement.\nb. Second requirement."
    after = (
        "P.7/2021, namely:\nP. 8/2021 remains cited.\n"
        "SECTION 99 remains quoted source.\nPOINT is a literal word.\n"
        "Inline labels: a. alpha b. beta.\n"
        "Literal *stars*, _underlines_, {braces}, C:\\source and /slashes/."
    )
    source = source.replace("Before original.", before).replace("After original.", after)
    anchors[1] = replace(anchors[1], char_offset=source.index("SECTION 2"))
    literal_eids = preserve_source_tables(source, anchors, bodies)
    scaffold, index = scaffold_from_anchors(anchors)
    filled = assemble_filled_scaffold(
        scaffold,
        index,
        BodyFillResponse(bodies=list(bodies.values())),
        literal_body_eids=literal_eids,
    )
    akn = parse_to_akn(filled, country="xa", doctype="act", date="2026-01-01", number="1")
    root = etree.fromstring(akn.encode())
    section = root.xpath('//*[@eId="sec_1"]')[0]
    text = " ".join(" ".join(section.itertext()).split())
    for line in before.splitlines() + after.splitlines():
        assert line in text
    assert section.xpath('.//*[local-name()="point"]') == []
    assert len(section.xpath('.//*[local-name()="table"]')) == 1


def test_indented_source_heading_is_not_sliced_as_unindented() -> None:
    source, anchors, bodies = _case(["Lost table."])
    source = source.replace("SECTION 1\n", "  SECTION 1 Source title\n", 1)
    anchors[1] = replace(anchors[1], char_offset=source.index("SECTION 2"))
    literal_eids = preserve_source_tables(source, anchors, bodies)
    assert bodies["sec_1"].heading == "Source title"
    scaffold, index = scaffold_from_anchors(anchors)
    filled = assemble_filled_scaffold(
        scaffold,
        index,
        BodyFillResponse(bodies=list(bodies.values())),
        literal_body_eids=literal_eids,
    )
    root = etree.fromstring(
        parse_to_akn(filled, country="xa", doctype="act", date="2026-01-01", number="1").encode()
    )
    assert root.xpath('//*[@eId="sec_1"]/*[local-name()="heading"]/text()') == ["Source title"]


def test_omitted_separator_cannot_turn_a_single_row_table_into_prose() -> None:
    source, anchors, bodies = _case(TABLE[:1])
    source = source.replace("\n".join(TABLE), "\n".join(TABLE[:2]))
    anchors[1] = replace(anchors[1], char_offset=source.index("SECTION 2"))
    literal_eids = preserve_source_tables(source, anchors, bodies)
    assert literal_eids == frozenset({"sec_1"})
    scaffold, index = scaffold_from_anchors(anchors)
    filled = assemble_filled_scaffold(
        scaffold,
        index,
        BodyFillResponse(bodies=list(bodies.values())),
        literal_body_eids=literal_eids,
    )
    root = etree.fromstring(
        parse_to_akn(filled, country="xa", doctype="act", date="2026-01-01", number="1").encode()
    )
    assert len(root.xpath('//*[@eId="sec_1"]//*[local-name()="table"]')) == 1
