"""Annexes reach the AKN, and their tables survive as tables."""

from __future__ import annotations

from codify.pipeline.formats.eu_directive import formex_to_akn4eu

_ACT_HEAD = (
    "<ACT><BIB.INSTANCE><DATE ISO='20200101'>20200101</DATE></BIB.INSTANCE>"
    "<ENACTING.TERMS><ARTICLE><TI.ART>Article 1</TI.ART>"
    "<PARAG><TXT>Text.</TXT></PARAG></ARTICLE></ENACTING.TERMS>"
)
_TABLE = (
    "<TBL COLS='2'><TITLE><TI><P>List of goods</P></TI></TITLE><CORPUS>"
    "<ROW TYPE='HEADER'><CELL COL='1' TYPE='HEADER'>CN code</CELL>"
    "<CELL COL='2' TYPE='HEADER'>Description</CELL></ROW>"
    "<ROW><CELL COL='1'>8471 30 00</CELL><CELL COL='2'>Portable computers</CELL></ROW>"
    "</CORPUS></TBL>"
)


def _with_annex(contents: str) -> str:
    return (
        f"{_ACT_HEAD}<ANNEX><BIB.INSTANCE><NO.SEQ>0001.0001</NO.SEQ></BIB.INSTANCE>"
        f"<TITLE><TI><P>ANNEX I</P></TI></TITLE>"
        f"<CONTENTS>{contents}</CONTENTS></ANNEX></ACT>"
    )


def test_an_annex_becomes_an_attachment() -> None:
    out = formex_to_akn4eu(_with_annex("<P>Some annex prose.</P>"))
    assert "<attachments>" in out
    assert 'name="ANNEX"' in out
    assert "Some annex prose." in out


def test_an_annex_table_stays_a_table() -> None:
    out = formex_to_akn4eu(_with_annex(_TABLE))
    assert "omitted" not in out
    assert out.count("<tr ") == 2
    assert "<th><p>CN code</p></th>" in out
    assert "<td>" in out
    assert "Portable computers" in out


def test_a_table_row_can_be_cited() -> None:
    """A row is what an amending act replaces, so it needs an address. Cells do
    not get one: they are never amended alone and AKN puts the id on the row."""
    out = formex_to_akn4eu(_with_annex(_TABLE))
    assert 'eId="att_1__table_1__tbl"' in out
    # The row hangs off the table, so a row eId names the table containing it.
    assert 'eId="att_1__table_1__tbl__tr_2"' in out
    assert "__tc_" not in out


def test_the_table_title_becomes_the_wrapper_heading() -> None:
    """AKN4EU gives a table an hcontainer whose num and heading carry its
    number and subject, so both are citable."""
    out = formex_to_akn4eu(_with_annex(_TABLE))
    assert '<hcontainer name="TAB"' in out
    assert "<heading>List of goods</heading>" in out


def test_a_numbered_table_keeps_its_number_apart_from_its_subject() -> None:
    """FORMEX puts the number in TI and the subject in STI; joined, they read
    as one run-on heading."""
    numbered = _TABLE.replace(
        "<TITLE><TI><P>List of goods</P></TI></TITLE>",
        "<TITLE><TI><P>Table 1</P></TI><STI><P>List of goods</P></STI></TITLE>",
    )
    out = formex_to_akn4eu(_with_annex(numbered))
    assert "<num>Table 1</num>" in out
    assert "<heading>List of goods</heading>" in out


def test_the_document_validates_against_the_akn_schema() -> None:
    """An annex that will not validate cannot be exported or imported."""
    import os.path

    import cobalt
    from lxml import etree

    xsd = os.path.join(os.path.dirname(cobalt.__file__), "xsd", "akomantoso30.xsd")
    schema = etree.XMLSchema(etree.parse(xsd))
    out = formex_to_akn4eu(_with_annex(_TABLE))
    assert schema.validate(etree.fromstring(out.encode())), schema.error_log


def test_a_table_inside_a_list_item_is_still_a_table() -> None:
    nested = f"<LIST><ITEM><NP><P>Introduced by:</P>{_TABLE}</NP></ITEM></LIST>"
    out = formex_to_akn4eu(_with_annex(nested))
    assert out.count("<tr ") == 2


def test_a_grouping_wrapper_does_not_hide_the_table() -> None:
    out = formex_to_akn4eu(_with_annex(f"<GR.SEQ><GR.SEQ>{_TABLE}</GR.SEQ></GR.SEQ>"))
    assert out.count("<tr ") == 2


def test_an_untranscribed_page_scan_is_reported_not_dropped() -> None:
    """A run that reports nothing here reads as complete when it is not."""
    provenance: dict[str, object] = {}
    out = formex_to_akn4eu(
        _with_annex("<P><INCL.ELEMENT TYPE='TIFF' FILEREF='page.tif'/></P>"),
        provenance=provenance,
    )
    assert provenance["annex_images"] == 1
    assert "page.tif" in out


def test_an_act_with_no_annex_gains_no_attachments() -> None:
    assert "<attachments>" not in formex_to_akn4eu(f"{_ACT_HEAD}</ACT>")


def test_a_numbered_table_is_not_a_basic_unit() -> None:
    """Counted as one, the preservation gate rejects a restructure that dropped
    nothing: the Bluebell path renders a table without the wrapper."""
    from lxml import etree

    from codify.akn.vocabulary import basic_unit_numbers

    numbered = _TABLE.replace(
        "<TITLE><TI><P>List of goods</P></TI></TITLE>",
        "<TITLE><TI><P>Table 1</P></TI><STI><P>List of goods</P></STI></TITLE>",
    )
    out = formex_to_akn4eu(_with_annex(numbered))
    units = basic_unit_numbers(etree.fromstring(out.encode()))
    assert not [u for u in units if u.startswith("TAB")]


def test_every_annex_block_carries_an_eid() -> None:
    """`akn_wid` falls back to `akn_eid`, and a non-empty wid is a database
    constraint, so an eId-less block fails the whole ingest at persist."""
    from lxml import etree

    formex = (
        _ACT_HEAD + "<ANNEX><TI>ANNEX I</TI>"
        "<P>Loose prose the walker does not otherwise recognise.</P>"
        "<FORMULA>x</FORMULA>"
        "<INCL.ELEMENT TYPE='TIFF' FILEREF='scan.tif'/>"
        "</ANNEX>"
        # A second annex whose only child is metadata, so the walker adds
        # nothing and the raw-text fallback is what mints its block. A bare
        # <TI> would not do: it is not metadata, so the walker keeps it.
        "<ANNEX><TITLE><TI>ANNEX II</TI></TITLE>"
        "Raw text no structural child carries.</ANNEX>"
        "</ACT>"
    )
    akn = formex_to_akn4eu(formex, frbr_work_uri="/akn/xx/act/reg/2024/1")
    root = etree.fromstring(akn.encode())
    attachments = root.findall(".//{*}attachments/{*}attachment")
    assert len(attachments) == 2, "both annexes should have produced an attachment"
    blocks = root.findall(".//{*}attachments//{*}hcontainer")
    assert blocks, "the annex produced no blocks to check"
    # The second annex has only the fallback block, so it proves that path.
    assert attachments[1].findall(".//{*}hcontainer"), "the fallback minted no block"
    missing = [b.get("name") for b in blocks if not (b.get("eId") or "").strip()]
    assert not missing, f"annex blocks without an eId: {missing}"
    eids = [b.get("eId") for b in blocks]
    assert len(eids) == len(set(eids)), f"annex block eIds repeat: {eids}"


def test_ingest_marks_an_untranscribed_scan_incomplete(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The status rides the ingest path this format owns, not only the PDF lane."""
    import asyncio

    from codify.pipeline.events import Complete
    from codify.pipeline.formats.eu_directive import ingest

    source = tmp_path / "act.xml"
    source.write_text(_with_annex("<P><INCL.ELEMENT TYPE='TIFF' FILEREF='page.tif'/></P>"))

    async def run() -> str:
        async for ev in ingest(source, "eu"):
            if isinstance(ev, Complete):
                return ev.akn_xml
        return ""

    out = asyncio.run(run())
    assert 'status="incomplete"' in out
    assert "page.tif" in out
