"""An EU-shaped annex (hcontainers under the annex's own doc) reaches the Bluebell export."""

from __future__ import annotations

from codify.akn.bluebell import akn_xml_to_bluebell

_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_ACT = f"""<akomaNtoso xmlns="{_NS}"><act name="act">
<meta/><body><article eId="art_1"><num>Article 1</num>
<content><p>Goods listed in Annex I are covered.</p></content></article></body>
<attachments><attachment eId="att_1"><doc name="ANNEX"><meta/>
<preface><container name="headerOfAnnex">
<block name="num">ANNEX I</block><block name="heading">List of widgets</block>
</container></preface>
<mainBody>
<hcontainer eId="att_1__paragraph_1" name="paragraph">
<content><p>1. The goods are these.</p></content></hcontainer>
<hcontainer eId="att_1__table_1" name="TAB"><content><table eId="att_1__table_1__tbl">
<tr><th><p>CN code</p></th><th><p>Gas</p></th></tr>
<tr><td><p>9999 10 00 – Example widgets</p></td><td><p>Carbon dioxide</p></td></tr>
</table></content></hcontainer>
</mainBody></doc></attachment></attachments>
</act></akomaNtoso>"""


def test_an_annex_of_hcontainers_keeps_its_title_prose_and_table() -> None:
    out = akn_xml_to_bluebell(_ACT)
    schedule = out[out.index("SCHEDULE 1") :]
    assert schedule.startswith("SCHEDULE 1 - ANNEX I: List of widgets")
    assert "1. The goods are these." in schedule
    assert "TABLE" in schedule
    assert "9999 10 00 – Example widgets" in schedule
