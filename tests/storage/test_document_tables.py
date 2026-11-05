"""Tables no provision carries still reach the document tree, placed by eId."""

from __future__ import annotations

import uuid

from codify.storage.documents import (
    DocumentProvision,
    DocumentProvisionBlock,
    DocumentSection,
    DocumentTable,
    _parse_version_xml,
    _rendered_tables,
    _unrowed_tables,
)

_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_XML = f"""<akomaNtoso xmlns="{_NS}"><act><body>
<article eId="art_1"><content><table eId="art_1__tbl"><tr><td><p>in a provision</p></td></tr>
</table></content></article></body>
<attachments><attachment eId="att_1"><doc name="ANNEX"><mainBody>
<hcontainer eId="att_1__table_1" name="TAB"><heading>List of widgets</heading>
<content><table eId="att_1__table_1__tbl"><tr><th><p>CN code</p></th></tr>
<tr><td><p>9999 10 00 – Widgets</p></td></tr></table></content></hcontainer>
</mainBody></doc></attachment></attachments></act></akomaNtoso>"""


def test_an_annex_table_is_placed_and_a_rendered_one_is_left_to_its_provision() -> None:
    root = _parse_version_xml(_XML)
    assert root is not None
    (placed,) = _unrowed_tables(root, frozenset({"art_1__tbl"}))
    assert placed.ancestor_eids == ["att_1", "att_1__table_1"]
    assert placed.heading == "List of widgets"
    assert placed.table.akn_eid == "att_1__table_1__tbl"
    assert [r.akn_eid for r in placed.table.rows] == [
        "att_1__table_1__tbl__tr_1",
        "att_1__table_1__tbl__tr_2",
    ]


def test_a_table_under_a_provision_that_did_not_render_it_is_still_placed() -> None:
    """A persisted group whose walker skipped the nested wrapper holding the table."""
    root = _parse_version_xml(_XML)
    assert root is not None
    placed = _unrowed_tables(root, frozenset())
    assert [p.table.akn_eid for p in placed] == ["art_1__tbl", "att_1__table_1__tbl"]


def test_rendered_tables_are_collected_from_nested_blocks() -> None:
    table = DocumentProvisionBlock(
        akn_eid="art_1__tbl", akn_type="table", table=DocumentTable(akn_eid="art_1__tbl")
    )
    provision = DocumentProvision(
        id=uuid.uuid4(),
        akn_eid="art_1__para_1",
        akn_type="paragraph",
        text="",
        position=1,
        blocks=[
            DocumentProvisionBlock(akn_eid="art_1__para_1__a", akn_type="point", blocks=[table])
        ],
    )
    section = DocumentSection(
        id=uuid.uuid4(),
        akn_eid="chp_1",
        akn_type="chapter",
        title=None,
        position=1,
        provisions=[provision],
    )
    assert _rendered_tables([section], []) == frozenset({"art_1__tbl"})


def test_a_table_without_an_eid_is_skipped_rather_than_failing_the_read() -> None:
    root = _parse_version_xml(_XML.replace(' eId="att_1__table_1__tbl"', ""))
    assert root is not None
    assert [p.table.akn_eid for p in _unrowed_tables(root, frozenset())] == ["art_1__tbl"]
