"""Synthetic operative and explanatory attachments retain distinct legal-force flags."""

from __future__ import annotations

import uuid

import pytest

from codify.akn import Article, Chapter
from codify.akn._parser import parse_document
from codify.akn.document import Document
from codify.storage.mappers import document_to_rows


@pytest.fixture(autouse=True)
def attachment_config(monkeypatch):
    from codify import jurisdictions
    from codify.jurisdictions import AttachmentCaption

    original = jurisdictions.try_load_config
    config = jurisdictions.load_config("xa").model_copy(deep=True)
    config.attachments = [
        AttachmentCaption(caption="EXPLANATORY NOTE", normative=False),
        AttachmentCaption(caption="SCHEDULE", normative=True),
    ]
    monkeypatch.setattr(
        jurisdictions, "try_load_config", lambda code: config if code == "xa" else original(code)
    )


ATTACHMENT_TEMPLATE = """      <attachment eId="att_{n}">
        <heading>{heading}</heading>
        <doc name="schedule">
          <meta><identification source="#codify">
            <FRBRWork><FRBRthis value="/akn/xa/act/2003/13/!schedule_{n}"/>
              <FRBRuri value="/akn/xa/act/2003/13"/>
              <FRBRdate date="2003-03-25" name="Generation"/>
              <FRBRauthor href=""/><FRBRcountry value="xa"/></FRBRWork>
            <FRBRExpression><FRBRthis value="/akn/xa/act/2003/13/ind@2003-03-25/!schedule_{n}"/>
              <FRBRuri value="/akn/xa/act/2003/13/ind@2003-03-25"/>
              <FRBRdate date="2003-03-25" name="Generation"/>
              <FRBRauthor href=""/><FRBRlanguage language="ind"/></FRBRExpression>
            <FRBRManifestation>
              <FRBRthis value="/akn/xa/act/2003/13/ind@2003-03-25/!schedule_{n}.xml"/>
              <FRBRuri value="/akn/xa/act/2003/13/ind@2003-03-25.xml"/>
              <FRBRdate date="2003-03-25" name="Generation"/>
              <FRBRauthor href=""/></FRBRManifestation>
          </identification></meta>
          <mainBody>
            <article eId="att_{n}__art_1"><num>1</num>
              <content><p>{text}</p></content>
            </article>
          </mainBody>
        </doc>
      </attachment>
"""

AKN_WITH_ATTACHMENT = """<?xml version="1.0" encoding="UTF-8"?>
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
          <FRBRthis value="/akn/xa/act/2003/13/ind@2003-03-25/!main"/>
          <FRBRuri value="/akn/xa/act/2003/13/ind@2003-03-25"/>
          <FRBRdate date="2003-03-25" name="Generation"/>
          <FRBRauthor href=""/><FRBRlanguage language="ind"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRthis value="/akn/xa/act/2003/13/ind@2003-03-25/!main.xml"/>
          <FRBRuri value="/akn/xa/act/2003/13/ind@2003-03-25.xml"/>
          <FRBRdate date="2003-03-25" name="Generation"/>
          <FRBRauthor href=""/>
        </FRBRManifestation>
      </identification>
    </meta>
    <body>
      <article eId="art_1"><num>1</num><content><p>Keep sample lenses.</p></content></article>
    </body>
    <attachments>
      <attachment eId="att_1">
        <heading>1 - EXPLANATORY NOTE</heading>
        <doc name="schedule">
          <meta><identification source="#codify">
            <FRBRWork><FRBRthis value="/akn/xa/act/2003/13/!schedule_1"/>
              <FRBRuri value="/akn/xa/act/2003/13"/>
              <FRBRdate date="2003-03-25" name="Generation"/>
              <FRBRauthor href=""/><FRBRcountry value="xa"/></FRBRWork>
            <FRBRExpression><FRBRthis value="/akn/xa/act/2003/13/ind@2003-03-25/!schedule_1"/>
              <FRBRuri value="/akn/xa/act/2003/13/ind@2003-03-25"/>
              <FRBRdate date="2003-03-25" name="Generation"/>
              <FRBRauthor href=""/><FRBRlanguage language="ind"/></FRBRExpression>
            <FRBRManifestation>
              <FRBRthis value="/akn/xa/act/2003/13/ind@2003-03-25/!schedule_1.xml"/>
              <FRBRuri value="/akn/xa/act/2003/13/ind@2003-03-25.xml"/>
              <FRBRdate date="2003-03-25" name="Generation"/>
              <FRBRauthor href=""/></FRBRManifestation>
          </identification></meta>
          <mainBody>
            <article eId="att_1__art_1"><num>1</num>
              <content><p>The sample is explanatory.</p></content>
            </article>
          </mainBody>
        </doc>
      </attachment>
    </attachments>
  </act>
</akomaNtoso>
"""


def test_attachment_content_is_parsed_out_of_the_inner_document() -> None:
    """A generic `<doc>` holds `<mainBody>`, not `<body>`; reading only the
    latter returned zero attachments and looked like a document without one."""
    doc = parse_document(AKN_WITH_ATTACHMENT)
    assert len(doc.body) == 1
    # One container per attachment, carrying its heading and its content.
    assert len(doc.attachments) == 1
    att = doc.attachments[0]
    assert att.akn_eid == "att_1"
    assert att.heading == "1 - EXPLANATORY NOTE"
    assert [c.akn_eid for c in att.children] == ["att_1__art_1"]


def test_attachment_provisions_are_indexed_and_marked() -> None:
    doc = parse_document(AKN_WITH_ATTACHMENT)
    _, _, provisions, *_ = document_to_rows(doc, law_id=uuid.uuid4())
    by_eid = {p.akn_eid: p for p in provisions}
    body = [p for p in provisions if not p.akn_eid.startswith("att_")]
    attachment = [p for p in provisions if p.akn_eid.startswith("att_")]
    assert attachment, "attachment content never reached provisions"
    assert all(not p.normative for p in attachment)  # a declared Penjelasan
    assert body and all(p.normative for p in body)
    assert "The sample is explanatory." in by_eid["att_1__art_1__content"].text


def test_a_document_without_attachments_is_all_normative() -> None:
    doc = Document(
        frbr_work_uri="/akn/xa/act/2003/13",
        frbr_expression_uri="/akn/xa/act/2003/13/ind@2003-03-25",
        language="ind",
        expression_date="2003-03-25",  # type: ignore[arg-type]
        body=[
            Chapter(
                akn_eid="chp_1",
                akn_type="chapter",
                position=0,
                children=[Article(akn_eid="art_1", akn_type="article", position=0, text="Isi.")],
            )
        ],
    )
    _, _, provisions, *_ = document_to_rows(doc, law_id=uuid.uuid4())
    assert provisions and all(p.normative for p in provisions)


def test_attachment_sorts_after_the_body_and_stays_one_unit() -> None:
    """The reader orders sections by `position` alone. Attachment numbering
    restarts at zero, so loose roots tie with the body's chapters and an
    elucidation article renders between two of them, which is the mixed copy
    this separation exists to end."""
    doc = parse_document(AKN_WITH_ATTACHMENT)
    _, sections, _, *_ = document_to_rows(doc, law_id=uuid.uuid4())
    tops = sorted((s for s in sections if s.parent_section_id is None), key=lambda s: s.position)
    assert len(set(s.position for s in tops)) == len(tops), "tied top-level positions"
    assert tops[-1].akn_eid == "att_1"
    assert tops[-1].title == "1 - EXPLANATORY NOTE"
    # Its articles hang off it rather than sitting at top level beside chapters.
    assert all(s.parent_section_id is not None for s in sections if s.akn_eid.startswith("att_1__"))


def test_a_lampiran_keeps_its_force_beside_a_penjelasan() -> None:
    """Marking every attachment non-normative was wrong for a Lampiran, which
    usually carries the operative content."""
    akn = AKN_WITH_ATTACHMENT.replace(
        "</attachments>",
        ATTACHMENT_TEMPLATE.format(
            n=2, heading="2 - SCHEDULE", text="Keep lenses in labelled boxes."
        )
        + "</attachments>",
    )
    doc = parse_document(akn)
    _, _, provisions, *_ = document_to_rows(doc, law_id=uuid.uuid4())
    by_eid = {p.akn_eid: p for p in provisions}
    assert not by_eid["att_1__art_1__content"].normative
    assert by_eid["att_2__art_1__content"].normative


def test_an_undeclared_attachment_is_operative() -> None:
    """No declaration must not strip a document's annexes of force."""
    doc = parse_document(AKN_WITH_ATTACHMENT.replace("1 - EXPLANATORY NOTE", "1 - INVENTORY"))
    _, _, provisions, *_ = document_to_rows(doc, law_id=uuid.uuid4())
    assert all(p.normative for p in provisions)


def test_a_lampiran_whose_heading_mentions_penjelasan_keeps_its_force() -> None:
    """The caption is matched at the head of the heading. A substring match
    read "SCHEDULE II EXPLANATORY NOTE TEKNIS" as an elucidation and stripped a
    schedule of its force."""
    akn = AKN_WITH_ATTACHMENT.replace("1 - EXPLANATORY NOTE", "SCHEDULE II EXPLANATORY NOTE TEKNIS")
    doc = parse_document(akn)
    _, _, provisions, *_ = document_to_rows(doc, law_id=uuid.uuid4())
    assert all(p.normative for p in provisions)


def test_a_presentation_wrapper_does_not_become_a_provision() -> None:
    """A table's rows are indexed as rows. Parsed as a provision too, the
    wrapper flattens every cell into one text, which duplicates them and, on a
    six-figure annex, exceeds what a single provision can carry."""
    rows = "".join(
        f'<tr eId="att_1__table_1__tbl__tr_{i}">'
        f"<td><p>740{i}</p></td><td><p>Widget {i}</p></td></tr>"
        for i in range(1, 6)
    )
    akn = AKN_WITH_ATTACHMENT.replace(
        '<article eId="att_1__art_1"><num>1</num>\n'
        "              <content><p>The sample is explanatory.</p></content>\n"
        "            </article>",
        '<hcontainer name="TAB" eId="att_1__table_1"><num>Table 1</num>'
        "<heading>Goods</heading>"
        f'<content><table eId="att_1__table_1__tbl">{rows}</table></content></hcontainer>',
    )
    doc = parse_document(akn)
    _, sections, provisions, *_ = document_to_rows(doc, law_id=uuid.uuid4())
    assert not [p for p in provisions if "Widget" in p.text]
    # The wrapper stays and carries its title, so the annex and its table
    # still reach the reader, which renders blocks from the XML.
    by_eid = {p.akn_eid: p for p in provisions}
    assert by_eid["att_1__table_1"].text == "Table 1 Goods"
    assert [s.akn_eid for s in sections] == ["art_1", "att_1"]
