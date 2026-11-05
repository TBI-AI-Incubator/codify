"""Tables no provision carries still reach the document tree, placed by eId."""

from __future__ import annotations

from codify.storage.documents import _parse_version_xml, _unrowed_tables

_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_XML = f"""<akomaNtoso xmlns="{_NS}"><act><body>
<article eId="art_1"><content><table eId="art_1__tbl"><tr><td><p>in a provision</p></td></tr>
</table></content></article></body>
<attachments><attachment eId="att_1"><doc name="ANNEX"><mainBody>
<hcontainer eId="att_1__table_1" name="TAB"><heading>List of widgets</heading>
<content><table eId="att_1__table_1__tbl"><tr><th><p>CN code</p></th></tr>
<tr><td><p>9999 10 00 – Widgets</p></td></tr></table></content></hcontainer>
</mainBody></doc></attachment></attachments></act></akomaNtoso>"""


def test_an_annex_table_is_placed_and_a_rowed_one_is_left_to_its_provision() -> None:
    root = _parse_version_xml(_XML)
    assert root is not None
    (placed,) = _unrowed_tables(root, frozenset({"art_1"}))
    assert placed.ancestor_eids == ["att_1", "att_1__table_1"]
    assert placed.heading == "List of widgets"
    assert placed.table.akn_eid == "att_1__table_1__tbl"
    assert [r.akn_eid for r in placed.table.rows] == [
        "att_1__table_1__tbl__tr_1",
        "att_1__table_1__tbl__tr_2",
    ]
