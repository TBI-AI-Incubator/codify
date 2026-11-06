"""A consolidated text maps to a dated, non-authoritative expression of its base act."""

from __future__ import annotations

import asyncio
import os.path
from pathlib import Path

import cobalt
import pytest
from lxml import etree

from codify.pipeline.events import Complete, ValidationIssued
from codify.pipeline.formats.eu_directive import (
    FormexDateMissingError,
    FormexNotAnActError,
    _detect_format,
    formex_to_akn4eu,
    ingest,
)

NS = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
WORK = "/akn/eu/act/reg/2042/1"

# The shape Cellar serves, with invented content: the base act and one amending act.
_CONSOLIDATED = """<CONS.ACT>
<INFO.CONSLEG CONSLEG.REF="2042R0001" CONSLEG.DATE="20450227" START.DATE="20450301"
  END.DATE="99999999" LEG.VAL="REG"/>
<INFO.PROD PRODUCER="ES"/>
<CONS.DOC>
<BIB.INSTANCE><DOCUMENT.REF><LG.OJ>EN</LG.OJ></DOCUMENT.REF>
  <DATE ISO="20450301">20450301</DATE></BIB.INSTANCE>
<FAM.COMP><BIB.DATA><BIB.INSTANCE.CONS><NO.DOC FORMAT="YN" TYPE="OJ"><NO.CURRENT>1</NO.CURRENT>
  <YEAR>2042</YEAR></NO.DOC></BIB.INSTANCE.CONS><NO.CELEX>32042R0001</NO.CELEX>
  <DATE ISO="20420115">20420115</DATE>
  <TITLE><TI><P>Regulation (EU) 2042/1 on synthetic widgets</P></TI></TITLE></BIB.DATA>
  <GR.MOD.ACT><MOD.ACT><BIB.DATA><NO.CELEX>32044R0007</NO.CELEX>
  <DATE ISO="20441201">20441201</DATE></BIB.DATA></MOD.ACT></GR.MOD.ACT></FAM.COMP>
<TITLE><TI><P>Regulation (EU) 2042/1 of 15 January 2042 on synthetic widgets</P></TI></TITLE>
<PREAMBLE><PREAMBLE.INIT/><PREAMBLE.FINAL/></PREAMBLE>
<ENACTING.TERMS><ARTICLE IDENTIFIER="001"><TI.ART>Article 1</TI.ART>
  <PARAG IDENTIFIER="001.001"><NO.PARAG>1.</NO.PARAG><ALINEA>Widgets are listed.</ALINEA></PARAG>
  <PARAG IDENTIFIER="001.001A"><NO.PARAG>1a.</NO.PARAG><ALINEA>Inserted text.</ALINEA></PARAG>
  <PARAG IDENTIFIER="001.002"><NO.PARAG>2.</NO.PARAG><ALINEA>Second.</ALINEA></PARAG>
</ARTICLE></ENACTING.TERMS>
<FINAL><P>This Regulation shall be binding in its entirety.</P></FINAL>
<CONS.ANNEX><TITLE><TI><P>ANNEX I</P></TI></TITLE>
  <CONTENTS><P>Listed widgets.</P></CONTENTS></CONS.ANNEX>
</CONS.DOC>
</CONS.ACT>"""


@pytest.fixture(autouse=True)
def protocol_data(monkeypatch, tmp_path):
    from tests.acquisition.config_support import install_protocol_data

    install_protocol_data(monkeypatch, tmp_path)


def _convert(xml: str = _CONSOLIDATED, provenance: dict | None = None) -> etree._Element:
    return etree.fromstring(formex_to_akn4eu(xml, frbr_work_uri=WORK, provenance=provenance))


def test_a_consolidated_root_is_formex() -> None:
    assert _detect_format(_CONSOLIDATED) == "formex"


def test_the_expression_is_dated_to_the_consolidation_and_the_work_keeps_its_date() -> None:
    root = _convert()
    assert root.find(".//a:FRBRExpression/a:FRBRuri", NS).get("value") == f"{WORK}/eng@2045-03-01"
    work_dates = {d.get("date") for d in root.iterfind(".//a:FRBRWork/a:FRBRdate", NS)}
    assert work_dates == {"2042-01-15"}


def test_the_consolidation_record_dates_the_expression() -> None:
    undated_bib = _CONSOLIDATED.replace('<DATE ISO="20450301">20450301</DATE>', "")
    root = _convert(undated_bib)
    assert root.find(".//a:FRBRExpression/a:FRBRdate", NS).get("date") == "2045-03-01"


def test_every_expression_says_it_is_not_the_authentic_text() -> None:
    root = _convert()
    assert root.find("a:act", NS).get("contains") == "singleVersion"
    expressions = root.findall(".//a:FRBRExpression", NS)
    assert len(expressions) == 2  # the act and its annex
    for expr in expressions:
        assert expr.find("a:FRBRauthoritative", NS).get("value") == "false"


def test_an_adopted_act_stays_the_original_authoritative_version() -> None:
    adopted = (
        "<ACT><BIB.INSTANCE><DATE ISO='20420115'>20420115</DATE></BIB.INSTANCE>"
        "<ENACTING.TERMS><ARTICLE><TI.ART>Article 1</TI.ART>"
        "<PARAG><TXT>Text.</TXT></PARAG></ARTICLE></ENACTING.TERMS></ACT>"
    )
    root = _convert(adopted)
    assert root.find("a:act", NS).get("contains") == "originalVersion"
    assert root.find(".//a:FRBRauthoritative", NS) is None


def test_the_publication_is_the_consolidation_not_a_missing_journal_issue() -> None:
    provenance: dict = {}
    root = _convert(provenance=provenance)
    publication = root.find(".//a:publication", NS)
    assert publication.get("number") == "2042R0001/20450301"
    assert publication.get("date") == "2045-03-01"
    assert "publication_missing" not in provenance
    events = [(e.get("date"), e.get("type")) for e in root.iterfind(".//a:eventRef", NS)]
    assert events == [("2042-01-15", "generation"), ("2045-03-01", "amendment")]


def test_the_closing_formula_is_kept_without_a_signature() -> None:
    conclusions = _convert().find(".//a:conclusions", NS)
    assert "".join(conclusions.itertext()) == "This Regulation shall be binding in its entirety."


def test_an_adopted_acts_closing_formula_comes_before_its_signature() -> None:
    adopted = (
        "<ACT><BIB.INSTANCE><DATE ISO='20420115'>20420115</DATE></BIB.INSTANCE>"
        "<ENACTING.TERMS><ARTICLE><TI.ART>Article 1</TI.ART>"
        "<PARAG><TXT>Text.</TXT></PARAG></ARTICLE></ENACTING.TERMS>"
        "<FINAL><P>This Regulation shall be binding.</P><SIGNATURE><PL.DATE><P>Done at Brussels."
        "</P></PL.DATE><SIGNATORY><P>For the Council</P></SIGNATORY></SIGNATURE></FINAL></ACT>"
    )
    conclusions = _convert(adopted).find(".//a:conclusions", NS)
    assert ["".join(c.itertext()) for c in conclusions] == [
        "This Regulation shall be binding.",
        "Done at Brussels.",
        "For the Council",
    ]


def test_the_consolidated_annex_is_an_attachment() -> None:
    root = _convert()
    attachments = root.findall(".//a:attachments/a:attachment", NS)
    assert len(attachments) == 1
    assert "Listed widgets." in "".join(attachments[0].itertext())


def test_content_beside_the_consolidated_document_is_kept() -> None:
    beside = _CONSOLIDATED.replace(
        "</CONS.DOC>",
        "</CONS.DOC><CONS.ANNEX><TITLE><TI><P>ANNEX II</P></TI></TITLE>"
        "<CONTENTS><P>Further widgets.</P></CONTENTS></CONS.ANNEX>",
    )
    attachments = _convert(beside).findall(".//a:attachments/a:attachment", NS)
    assert "Further widgets." in "".join(attachments[-1].itertext())


def test_without_a_caller_uri_the_work_is_the_base_act() -> None:
    from codify.pipeline.formats.eu_directive import _frbr_from_formex

    assert _frbr_from_formex(_CONSOLIDATED) == WORK


@pytest.mark.parametrize("printed", ["1a.", "1α.", "1A."])
def test_an_inserted_paragraph_keeps_its_letter_in_any_language(printed: str) -> None:
    root = _convert(
        _CONSOLIDATED.replace("<NO.PARAG>1a.</NO.PARAG>", f"<NO.PARAG>{printed}</NO.PARAG>")
    )
    eids = [p.get("eId") for p in root.iterfind(".//a:article/a:paragraph", NS)]
    assert eids == ["art_1__para_1", "art_1__para_1a", "art_1__para_2"]


_REPEATED = _CONSOLIDATED.replace(
    'IDENTIFIER="001.001A"><NO.PARAG>1a.</NO.PARAG>', 'IDENTIFIER="001.001"><NO.PARAG>1.</NO.PARAG>'
)


def test_a_repeated_paragraph_number_still_gets_a_unique_eid() -> None:
    provenance: dict = {}
    root = _convert(_REPEATED, provenance)
    eids = [p.get("eId") for p in root.iterfind(".//a:article/a:paragraph", NS)]
    assert eids == ["art_1__para_1", "art_1__para_1_dup2", "art_1__para_2"]
    assert provenance["deduped_eids"] == 1


def test_a_consolidation_without_its_date_is_refused() -> None:
    undated = _CONSOLIDATED.replace('START.DATE="20450301"', "")
    with pytest.raises(FormexDateMissingError, match="START.DATE"):
        _convert(undated)


def test_a_consolidation_without_its_document_is_not_an_act() -> None:
    with pytest.raises(FormexNotAnActError):
        _convert("<CONS.ACT><INFO.CONSLEG START.DATE='20450301'/></CONS.ACT>")


def test_without_the_base_act_date_the_work_takes_the_consolidation_date() -> None:
    root = _convert(_CONSOLIDATED.replace('<DATE ISO="20420115">20420115</DATE>', "", 1))
    work_dates = {d.get("date") for d in root.iterfind(".//a:FRBRWork/a:FRBRdate", NS)}
    assert work_dates == {"2045-03-01"}


def test_the_document_validates_against_the_akn_schema() -> None:
    xsd = os.path.join(os.path.dirname(cobalt.__file__), "xsd", "akomantoso30.xsd")
    schema = etree.XMLSchema(etree.parse(xsd))
    assert schema.validate(_convert()), schema.error_log


def test_ingest_completes_a_consolidated_text_as_a_dated_expression(tmp_path: Path) -> None:
    source = tmp_path / "regulation-2042-1.akn"
    source.write_text(_REPEATED, encoding="utf-8")

    async def run() -> list:
        return [e async for e in ingest(source, "eu", frbr_work_uri=WORK)]

    events = asyncio.run(run())
    final = events[-1]
    assert isinstance(final, Complete)
    renamed = [e for e in events if isinstance(e, ValidationIssued)]
    assert [e.issue["deduped_eids"] for e in renamed if "deduped_eids" in e.issue] == [1]
    assert final.document.frbr_expression_uri == f"{WORK}/eng@2045-03-01"
    assert str(final.document.work_date) == "2042-01-15"
