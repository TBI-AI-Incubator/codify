"""Table context resolution: header, group, prefix and indent, composed."""

from __future__ import annotations

import pytest

from codify.akn.vocabulary import table_row_eid
from codify.tables.resolve import ResolvedRow, TableIdentityError, resolve_tables

AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _doc(table: str, *, title: str = "Test Act") -> str:
    return (
        f'<akomaNtoso xmlns="{AKN}"><act><preface><longTitle><p>{title}</p></longTitle>'
        f'</preface><body><section eId="sec_1"><content>{table}</content></section>'
        f"</body></act></akomaNtoso>"
    )


def _table(rows: str, *, caption: str = "", eid: str = "t1") -> str:
    """Rows are authored with a `t1` prefix; stamp the table's own eId on them."""
    cap = f"<caption>{caption}</caption>" if caption else ""
    rows = rows.replace('eId="t1__tr_', f'eId="{eid}__tr_')
    return f'<table eId="{eid}">{cap}{rows}</table>'


def _tr(*cells: str, header: bool = False, index: int = 1, colspan: int = 0) -> str:
    tag = "th" if header else "td"
    span = f' colspan="{colspan}"' if colspan else ""
    body = "".join(f"<{tag}{span}><p>{c}</p></{tag}>" for c in cells)
    return f'<tr eId="t1__tr_{index}">{body}</tr>'


def _by_eid(rows: list[ResolvedRow]) -> dict[str, ResolvedRow]:
    return {r.akn_eid: r for r in rows}


# A nesting code column, an indent-marked description, and an "Other" leaf.
_NESTED = _table(
    _tr("Code", "Description", "Duty", header=True, index=1)
    + _tr("7408", "Copper wire:", "", index=2)
    + _tr("740811", "– – Of which the dimension exceeds 6 mm", "4,8", index=3)
    + _tr("740819", "– – Other:", "", index=4)
    + _tr("74081910", "– – – Of which the dimension exceeds 0,5 mm", "4,8", index=5)
    + _tr("74082900", "– – Other", "4,8", index=6),
    caption="Schedule of duties",
)


def test_a_row_that_says_only_other_gets_its_ancestors_back() -> None:
    """A quarter of tariff rows read "Other" and alone match nothing."""
    rows = _by_eid(resolve_tables(_doc(_NESTED)))
    row = rows["t1__tr_6"]
    assert row.lineage == ("Copper wire",)
    assert "Copper wire > Other" in row.resolved_text


def test_lineage_runs_outermost_first_through_several_levels() -> None:
    row = _by_eid(resolve_tables(_doc(_NESTED)))["t1__tr_5"]
    assert row.lineage == ("Copper wire", "Other")


def test_every_row_carries_the_document_frame() -> None:
    """A listing entry is unreadable without the act and annex it came from."""
    row = _by_eid(resolve_tables(_doc(_NESTED)))["t1__tr_6"]
    assert "Test Act" in row.frame
    assert "Schedule of duties" in row.frame
    assert row.resolved_text.startswith(row.frame)


def test_cells_are_paired_with_their_column_header() -> None:
    row = _by_eid(resolve_tables(_doc(_NESTED)))["t1__tr_6"]
    assert [(c.label, c.text) for c in row.cells][0] == ("Code", "74082900")
    assert "Duty: 4,8" in row.resolved_text


# A country band above a nested code column: both mechanisms, one table.
_COMPOSED = _table(
    _tr("Xanadu", index=1, colspan=2)
    + _tr("Product code", "Description", header=True, index=2)
    + _tr("2507", "Clay:", index=3)
    + _tr("25070080", "– Calcined clay", index=4)
    + _tr("2523", "Cement:", index=5)
    + _tr("25232100", "– White cement", index=6)
    + _tr("25232900", "– Other", index=7),
    eid="t2",
)


def test_a_group_row_and_a_code_tree_resolve_together() -> None:
    """Resolving only the code loses the band, and every row under it is then
    wrong about who it applies to."""
    row = _by_eid(resolve_tables(_doc(_COMPOSED)))["t2__tr_7"]
    assert row.lineage == ("Xanadu", "Cement")
    assert "group" in row.mechanisms


def test_a_group_row_is_not_itself_a_data_row() -> None:
    rows = resolve_tables(_doc(_COMPOSED))
    assert "t2__tr_1" not in _by_eid(rows)


def test_a_ragged_row_is_data_not_a_heading() -> None:
    """A row that lost cells looks narrow but heads nothing. Treated as a band
    it would vanish from the output and hang its text on every row below it."""
    table = _table(
        _tr("Code", "Description", "Duty", header=True, index=1)
        + _tr("7408", "Copper wire:", "", index=2)
        + _tr("(see footnote 3)", index=3)
        + _tr("740811", "Real child", "4,8", index=4)
        + _tr("740821", "Of brass", "4,8", index=5)
        + _tr("740829", "Other", "4,8", index=6),
        eid="t8",
    )
    rows = _by_eid(resolve_tables(_doc(table)))
    assert "t8__tr_3" in rows, "the ragged row is content and must survive"
    assert rows["t8__tr_4"].lineage == ("Copper wire",)
    assert "group" not in rows["t8__tr_4"].mechanisms


def test_a_band_is_recognised_by_its_span_not_its_cell_count() -> None:
    table = _table(
        _tr("Region A", index=1, colspan=3)
        + _tr("Code", "Description", "Duty", header=True, index=2)
        + _tr("7408", "Copper wire", "4,8", index=3),
        eid="t9",
    )
    rows = _by_eid(resolve_tables(_doc(table)))
    assert "t9__tr_1" not in rows
    assert rows["t9__tr_3"].lineage == ("Region A",)


# Right-to-left tables store the same logical order; only rendering flips.
_REVERSED = _table(
    _tr("Duty", "Description", "Code", header=True, index=1)
    + _tr("", "Copper wire:", "7408", index=2)
    + _tr("4,8", "– – Of which the dimension exceeds 6 mm", "740811", index=3)
    + _tr("4,8", "– – Of copper-zinc base alloys", "74082100", index=4)
    + _tr("4,8", "– – Other", "74082900", index=5),
    caption="Schedule of duties",
    eid="t3",
)


def test_the_code_column_is_found_by_content_not_position() -> None:
    """Keyed on position, a reversed import reports flat rows silently."""
    row = _by_eid(resolve_tables(_doc(_REVERSED)))["t3__tr_5"]
    assert row.lineage == ("Copper wire",)


def test_an_explicit_depth_column_is_read_as_depth() -> None:
    table = _table(
        _tr("H*", "Name", header=True, index=1)
        + _tr("0", "Consignment", index=2)
        + _tr("1", "Carrier acceptance date", index=3),
        eid="t4",
    )
    row = _by_eid(resolve_tables(_doc(table)))["t4__tr_3"]
    assert row.lineage == ("Consignment",)
    assert "indent" in row.mechanisms


def test_a_flat_table_resolves_to_the_frame_alone() -> None:
    """Most rows are self-contained and must not acquire an invented lineage."""
    table = _table(
        _tr("Entry", "Name", header=True, index=1)
        + _tr("1", "Alpha", index=2)
        + _tr("2", "Beta", index=3),
        eid="t5",
    )
    row = _by_eid(resolve_tables(_doc(table)))["t5__tr_3"]
    assert row.lineage == ()
    assert row.mechanisms == frozenset({"header"})
    assert "Name: Beta" in row.resolved_text


def test_an_ordinal_column_is_not_mistaken_for_a_code_tree() -> None:
    """Entry numbers 1, 2, 3 do not nest, and 1 prefixing 10 is a coincidence."""
    table = _table(
        _tr("No", "Name", header=True, index=1)
        + "".join(_tr(str(n), f"Item {n}", index=n + 1) for n in range(1, 12)),
        eid="t6",
    )
    rows = resolve_tables(_doc(table))
    assert all(r.lineage == () for r in rows)


def test_a_table_with_no_rows_yields_nothing() -> None:
    assert resolve_tables(_doc('<table eId="t7"/>')) == []


def test_rows_keep_the_eid_the_document_gave_them() -> None:
    rows = resolve_tables(_doc(_NESTED))
    assert [r.akn_eid for r in rows][0] == "t1__tr_2"
    assert all(r.table_eid == "t1" for r in rows)


def test_a_band_does_not_lend_its_code_tree_to_the_next_band() -> None:
    """An ancestor held over from the previous band is not this row's parent."""
    table = _table(
        _tr("Region A", index=1, colspan=2)
        + _tr("Code", "Description", header=True, index=2)
        + _tr("25", "Minerals:", index=3)
        + _tr("2507", "Clay", index=4)
        + _tr("250710", "White clay", index=5)
        + _tr("Region B", index=6, colspan=2)
        + _tr("2507", "Clay", index=7)
        + _tr("250790", "Other clay", index=8),
        eid="t10",
    )
    rows = _by_eid(resolve_tables(_doc(table)))
    assert rows["t10__tr_8"].lineage[0] == "Region B"
    assert "Minerals" not in rows["t10__tr_8"].lineage


def test_a_band_is_not_read_as_the_column_header() -> None:
    """Taken for the header row, a band is consumed and its rows lose it."""
    table = _table(
        '<tr eId="t11__tr_1"><th colspan="2"><p>Region A</p></th></tr>'
        + _tr("Code", "Description", header=True, index=2)
        + _tr("2507", "Clay", index=3),
        eid="t11",
    )
    rows = _by_eid(resolve_tables(_doc(table)))
    assert rows["t11__tr_3"].lineage == ("Region A",)
    assert [c.label for c in rows["t11__tr_3"].cells] == ["Code", "Description"]


def test_the_frame_names_the_annex_a_row_sits_in() -> None:
    """Rows reach a reader through an attachment, not a bare section."""
    inner = _table(
        _tr("Code", "Description", header=True, index=1) + _tr("2507", "Clay", index=2),
        caption="Table of goods",
        eid="t12",
    )
    doc = (
        f'<akomaNtoso xmlns="{AKN}"><act>'
        "<preface><longTitle><p>Test Act</p></longTitle></preface>"
        '<body><section eId="sec_1"><content><p>Body.</p></content></section></body>'
        '<attachments><attachment eId="att_1"><heading>Annex I</heading>'
        f'<doc name="annex"><mainBody>{inner}</mainBody></doc>'
        "</attachment></attachments></act></akomaNtoso>"
    )
    row = _by_eid(resolve_tables(doc))["t12__tr_2"]
    assert "Annex I" in row.frame
    assert "Table of goods" in row.frame


def test_a_heading_that_carries_no_code_still_places_its_children() -> None:
    """Placed by prefix alone, a coded child loses the code-less heading."""
    table = _table(
        _tr("Code", "Description", header=True, index=1)
        + _tr("0101", "Live horses, asses, mules and hinnies:", index=2)
        + _tr("", "– Horses:", index=3)
        + _tr("01012100", "– – Pure-bred breeding animals", index=4)
        + _tr("010129", "– – Other:", index=5)
        + _tr("01012910", "– – – For slaughter", index=6),
        eid="t13",
    )
    rows = _by_eid(resolve_tables(_doc(table)))
    assert rows["t13__tr_4"].lineage == ("Live horses, asses, mules and hinnies", "Horses")
    assert rows["t13__tr_6"].lineage[-1] == "Other"


def test_a_table_without_an_eid_is_refused() -> None:
    """Minting one gives two eId-less tables the same row addresses."""
    with pytest.raises(TableIdentityError):
        resolve_tables(_doc("<table><tr><td><p>a</p></td></tr></table>"))


def test_a_partial_colspan_does_not_shift_the_cells_after_it() -> None:
    """Paired off the tree, a spanned row's cells slide onto wrong headers."""
    table = _table(
        _tr("Code", "Description", "Duty", "Unit", header=True, index=1)
        + '<tr eId="t14__tr_2"><td colspan="2"><p>7408</p></td>'
        "<td><p>4,8</p></td><td><p>kg</p></td></tr>",
        eid="t14",
    )
    row = _by_eid(resolve_tables(_doc(table)))["t14__tr_2"]
    assert [(c.label, c.text) for c in row.cells][2:] == [("Duty", "4,8"), ("Unit", "kg")]


def test_a_rowspan_carries_its_value_down() -> None:
    table = _table(
        _tr("Chapter", "Code", "Description", header=True, index=1)
        + '<tr eId="t15__tr_2"><td rowspan="2"><p>Copper</p></td>'
        "<td><p>7408</p></td><td><p>Wire</p></td></tr>"
        + '<tr eId="t15__tr_3"><td><p>7409</p></td><td><p>Plate</p></td></tr>',
        eid="t15",
    )
    rows = _by_eid(resolve_tables(_doc(table)))
    assert rows["t15__tr_3"].cells[0].text == "Copper"
    assert [c.label for c in rows["t15__tr_3"].cells] == ["Chapter", "Code", "Description"]


def test_a_spanned_header_label_covers_every_column_it_spans() -> None:
    """Paired off the tree, the second column takes the third's label."""
    table = _table(
        '<tr eId="t16__tr_1"><th colspan="2"><p>Product</p></th>'
        "<th><p>Duty</p></th></tr>" + _tr("7408", "Copper wire", "4,8", index=2),
        eid="t16",
    )
    row = _by_eid(resolve_tables(_doc(table)))["t16__tr_2"]
    assert [c.label for c in row.cells] == ["Product", "Product", "Duty"]


def test_a_rowspan_in_the_last_column_does_not_leak_into_a_later_row() -> None:
    """Never advanced into, it would surface later as an extra stale column."""
    table = _table(
        _tr("Code", "Description", "Note", header=True, index=1)
        + '<tr eId="t17__tr_2"><td><p>7408</p></td><td><p>Wire</p></td>'
        '<td rowspan="2"><p>see 3</p></td></tr>'
        + '<tr eId="t17__tr_3"><td><p>7409</p></td><td><p>Plate</p></td></tr>'
        + _tr("7410", "Foil", "none", index=4),
        eid="t17",
    )
    rows = _by_eid(resolve_tables(_doc(table)))
    assert [c.text for c in rows["t17__tr_3"].cells] == ["7409", "Plate", "see 3"]
    assert [c.text for c in rows["t17__tr_4"].cells] == ["7410", "Foil", "none"]


def test_each_table_in_an_annex_carries_its_own_title() -> None:
    """Searched by descent, the first table's title answers for every row."""
    inner = (
        _table(_tr("Code", header=True, index=1) + _tr("7408", index=2), eid="ta")
        + _table(_tr("Code", header=True, index=1) + _tr("7409", index=2), eid="tb")
    ).replace(
        '<table eId="ta">', '<hcontainer name="TAB"><heading>Heating oil</heading><table eId="ta">'
    )
    inner = inner.replace(
        '<table eId="tb">', '<hcontainer name="TAB"><heading>Natural gas</heading><table eId="tb">'
    )
    inner = inner.replace("</table>", "</table></hcontainer>")
    doc = (
        f'<akomaNtoso xmlns="{AKN}"><act><body><section eId="s"><content><p>x</p></content>'
        '</section></body><attachments><attachment eId="att_1"><heading>Annex I</heading>'
        f'<doc name="ANNEX"><mainBody>{inner}</mainBody></doc></attachment></attachments>'
        "</act></akomaNtoso>"
    )
    rows = _by_eid(resolve_tables(doc))
    assert "Heating oil" in rows["ta__tr_2"].frame
    assert "Natural gas" in rows["tb__tr_2"].frame
    assert "Natural gas" not in rows["ta__tr_2"].frame


def test_the_row_eid_helper_refuses_an_id_that_cites_nothing() -> None:
    """A row id without its table names no row, and is silently valid-looking."""
    with pytest.raises(ValueError):
        table_row_eid("", 3)
    with pytest.raises(ValueError):
        table_row_eid("att_1__table_1__tbl", 0)
