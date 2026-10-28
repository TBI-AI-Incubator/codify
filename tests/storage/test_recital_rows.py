"""Preamble recitals become citable provision rows that carry no norm."""

from __future__ import annotations

import uuid

from lxml import etree

from codify.akn._parser import parse_document
from codify.storage.documents import InlineAnchor, _walk_text_block
from codify.storage.mappers import document_to_rows, rows_to_document

_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="act">
    <meta>
      <identification source="#codify">
        <FRBRWork>
          <FRBRthis value="/akn/xa/act/2003/13/!main"/>
          <FRBRuri value="/akn/xa/act/2003/13"/>
          <FRBRdate date="2003-03-25" name="Generation"/>
          <FRBRauthor href=""/><FRBRcountry value="xa"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRthis value="/akn/xa/act/2003/13/eng@2003-03-25/!main"/>
          <FRBRuri value="/akn/xa/act/2003/13/eng@2003-03-25"/>
          <FRBRdate date="2003-03-25" name="Generation"/>
          <FRBRauthor href=""/><FRBRlanguage language="eng"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRthis value="/akn/xa/act/2003/13/eng@2003-03-25/!main.xml"/>
          <FRBRuri value="/akn/xa/act/2003/13/eng@2003-03-25.xml"/>
          <FRBRdate date="2003-03-25" name="Generation"/>
          <FRBRauthor href=""/>
        </FRBRManifestation>
      </identification>
    </meta>
"""

_PREAMBLE = """    <preamble>
      <formula name="enactingFormula"><p>The Assembly decides:</p></formula>
      <recitals eId="recitals">
        <recital eId="recitals__rec_1">
          <num>(1)</num>
          <p>Lenses should be kept clean<authorialNote placement="bottom" eId="fn_1">
            <p>A note.</p></authorialNote>.</p>
        </recital>
        <recital eId="recitals__rec_2">
          <num>(2)</num>
          <p>Boxes should be labelled.</p>
          <p>Labels should be legible.</p>
        </recital>
      </recitals>
    </preamble>
"""

_BODY = """    <body>
      <article eId="art_1"><num>1</num><content><p>Keep sample lenses.</p></content></article>
    </body>
  </act>
</akomaNtoso>
"""

AKN = _HEAD + _PREAMBLE + _BODY


def _rows(akn: str = AKN):
    return document_to_rows(parse_document(akn), law_id=uuid.uuid4())


def test_recitals_are_read_with_number_and_prose_only() -> None:
    doc = parse_document(AKN)
    assert [r.akn_eid for r in doc.recitals] == ["recitals__rec_1", "recitals__rec_2"]
    assert [r.number for r in doc.recitals] == ["(1)", "(2)"]
    assert doc.recitals[0].text == "Lenses should be kept clean."
    assert doc.recitals[1].text == "Boxes should be labelled.\nLabels should be legible."
    assert [b.akn_eid for b in doc.body] == ["art_1"]


def test_a_note_beside_a_recitals_prose_is_not_its_prose() -> None:
    sibling = AKN.replace(
        '</p>\n        </recital>\n        <recital eId="recitals__rec_2">',
        '</p><authorialNote placement="bottom" eId="fn_2"><p>Side note.</p></authorialNote>'
        '\n        </recital>\n        <recital eId="recitals__rec_2">',
        1,
    )
    assert "Side note." in sibling
    assert parse_document(sibling).recitals[0].text == "Lenses should be kept clean."


def test_recitals_become_non_normative_rows_beside_unchanged_operative_rows() -> None:
    _, _, provisions, cross_refs, *_ = _rows()
    by_eid = {p.akn_eid: p for p in provisions}
    recitals = [p for p in provisions if p.akn_type == "recital"]
    assert [p.akn_eid for p in recitals] == ["recitals__rec_1", "recitals__rec_2"]
    assert all(not p.normative and p.section_id is None for p in recitals)
    assert all(p.search_tokens for p in recitals)
    operative = [p for p in provisions if p.akn_type != "recital"]
    assert [p.akn_eid for p in operative] == ["art_1__content"]
    assert by_eid["art_1__content"].normative
    assert cross_refs == []


def test_a_document_without_a_preamble_gains_no_rows() -> None:
    plain = _HEAD + _BODY
    assert [p.akn_type for p in _rows(plain)[2]] == ["paragraph"]


def test_a_recital_whose_eid_is_taken_or_repeated_is_not_rowed_twice() -> None:
    clash = AKN.replace('eId="recitals__rec_2"', 'eId="recitals__rec_1"')
    assert [p.akn_eid for p in _rows(clash)[2] if p.akn_type == "recital"] == ["recitals__rec_1"]
    taken = AKN.replace('eId="recitals__rec_1"', 'eId="art_1__content"')
    assert [p.akn_eid for p in _rows(taken)[2] if p.akn_type == "recital"] == ["recitals__rec_2"]


def test_a_recital_without_an_eid_or_prose_is_not_rowed() -> None:
    bare = AKN.replace('<recital eId="recitals__rec_1">', "<recital>")
    bare = bare.replace("Boxes should be labelled.", "").replace("Labels should be legible.", "")
    assert [p for p in _rows(bare)[2] if p.akn_type == "recital"] == []


def test_rows_read_back_put_recitals_beside_the_body_not_in_it() -> None:
    version, sections, provisions, refs, *_ = _rows()
    doc = rows_to_document(version, sections, provisions, refs, frbr_work_uri="/akn/xa/act/2003/13")
    assert [e.akn_eid for e in doc.recitals] == ["recitals__rec_1", "recitals__rec_2"]
    assert all(e.akn_eid.startswith("art_1") for e in doc.body)


def test_the_preamble_reading_marks_where_each_recital_begins() -> None:
    el = etree.fromstring(_PREAMBLE.replace("<preamble>", _NS_PREAMBLE, 1))
    nodes = _walk_text_block(el)
    assert [n.eid for n in nodes if isinstance(n, InlineAnchor)] == [
        "recitals__rec_1",
        "recitals__rec_2",
    ]
    anchors = [i for i, n in enumerate(nodes) if isinstance(n, InlineAnchor)]
    assert "Lenses" in nodes[anchors[0] + 1].text  # type: ignore[union-attr]


_NS_PREAMBLE = '<preamble xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
