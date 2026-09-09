from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from lxml import etree
from pydantic import ValidationError

from codify.akn import (
    AKN_NS,
    AmendmentReference,
    Article,
    Chapter,
    Citation,
    CrossReference,
    Document,
    Paragraph,
    Section,
    Subparagraph,
    Title,
    parse_akn,
    to_akn,
    validate_akn,
)
from codify.akn._parser import _extract_text_and_refs

from ._helpers import structurally_equal


def _round_trip(doc: Document) -> Document:
    return parse_akn(to_akn(doc))


def _basic_doc(**body_kwargs: Any) -> Document:
    return Document(
        frbr_work_uri="/akn/gb/act/1981/64",
        frbr_expression_uri="/akn/gb/act/1981/64/eng@1981-10-30",
        language="eng",
        expression_date=date(1981, 10, 30),
        # Emitted AKN always carries a work date, so a document round-tripping
        # through it always parses one back. Stating it here keeps the round-trip
        # tests comparing like with like.
        work_date=date(1981, 10, 30),
        **body_kwargs,
    )


def test_round_trip_minimal_body() -> None:
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                number="1",
                heading="Citation",
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        text="This Act may be cited as the British Nationality Act 1981.",
                    ),
                ],
            ),
        ],
    )
    assert structurally_equal(doc, _round_trip(doc))


def test_round_trip_nested_hierarchy() -> None:
    doc = _basic_doc(
        body=[
            Title(
                akn_eid="ttl_1",
                akn_type="title",
                position=0,
                heading="Acquisition of Citizenship",
                children=[
                    Chapter(
                        akn_eid="ttl_1__chp_1",
                        akn_type="chapter",
                        position=0,
                        number="I",
                        children=[
                            Section(
                                akn_eid="ttl_1__chp_1__sec_1",
                                akn_type="section",
                                position=0,
                                number="1",
                                children=[
                                    Article(
                                        akn_eid="ttl_1__chp_1__sec_1__art_1",
                                        akn_type="article",
                                        position=0,
                                        children=[
                                            Paragraph(
                                                akn_eid="ttl_1__chp_1__sec_1__art_1__para_1",
                                                akn_type="paragraph",
                                                position=0,
                                                text="Citizenship by birth.",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    assert structurally_equal(doc, _round_trip(doc))


def test_round_trip_inline_citation() -> None:
    text = "Refers to the European Communities Act 1972 in clause."
    citation_start = text.index("European")
    citation_end = citation_start + len("European Communities Act 1972")
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        text=text,
                        references=[
                            Citation(
                                start_offset=citation_start,
                                end_offset=citation_end,
                                text_snippet=text[citation_start:citation_end],
                                target_uri="/akn/gb/act/1972/68",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    rebuilt = _round_trip(doc)
    assert structurally_equal(doc, rebuilt)
    ref = rebuilt.body[0].children[0].references[0]
    assert isinstance(ref, Citation)
    assert ref.target_uri == "/akn/gb/act/1972/68"


def test_round_trip_inline_cross_reference() -> None:
    text = "See section 5 for the qualifying conditions."
    snippet = "section 5"
    start = text.index(snippet)
    end = start + len(snippet)
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        text=text,
                        references=[
                            CrossReference(
                                start_offset=start,
                                end_offset=end,
                                text_snippet=snippet,
                                target_eid="sec_5",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    rebuilt = _round_trip(doc)
    assert structurally_equal(doc, rebuilt)
    ref = rebuilt.body[0].children[0].references[0]
    assert isinstance(ref, CrossReference)
    assert ref.target_eid == "sec_5"


def test_round_trip_inline_amendment_reference() -> None:
    text = "Substituted by paragraph 3 of Schedule 1."
    snippet = "paragraph 3 of Schedule 1"
    start = text.index(snippet)
    end = start + len(snippet)
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        text=text,
                        references=[
                            AmendmentReference(
                                start_offset=start,
                                end_offset=end,
                                text_snippet=snippet,
                                amends_uri="/akn/gb/act/2002/41",
                                operation="replace",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    rebuilt = _round_trip(doc)
    assert structurally_equal(doc, rebuilt)
    ref = rebuilt.body[0].children[0].references[0]
    assert isinstance(ref, AmendmentReference)
    assert ref.operation == "replace"
    assert ref.amends_uri == "/akn/gb/act/2002/41"


def test_round_trip_multiple_inline_refs_in_order() -> None:
    text = "See section 3 and section 7 of this Act."
    s3_start = text.index("section 3")
    s3_end = s3_start + len("section 3")
    s7_start = text.index("section 7")
    s7_end = s7_start + len("section 7")
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        text=text,
                        references=[
                            CrossReference(
                                start_offset=s3_start,
                                end_offset=s3_end,
                                text_snippet="section 3",
                                target_eid="sec_3",
                            ),
                            CrossReference(
                                start_offset=s7_start,
                                end_offset=s7_end,
                                text_snippet="section 7",
                                target_eid="sec_7",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    rebuilt = _round_trip(doc)
    assert structurally_equal(doc, rebuilt)
    refs = rebuilt.body[0].children[0].references
    assert [r.target_eid for r in refs if isinstance(r, CrossReference)] == [
        "sec_3",
        "sec_7",
    ]


def test_round_trip_preserves_non_canonical_akn_type() -> None:
    # <part> + <hcontainer> aren't in BodyElement's seven kinds; the parser
    # folds them but preserves the original tag in akn_type. The emitter
    # writes that back, so a round-trip keeps the source-shape invariant.
    src = b"""<?xml version='1.0'?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="act">
    <meta>
      <identification source="#x">
        <FRBRWork>
          <FRBRthis value="/akn/gb/act/1981/64"/>
          <FRBRuri value="/akn/gb/act/1981/64"/>
          <FRBRdate date="1981-10-30" name="Generation"/>
          <FRBRauthor href="#"/>
          <FRBRcountry value="gb"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRthis value="/akn/gb/act/1981/64/eng@1981-10-30"/>
          <FRBRuri value="/akn/gb/act/1981/64/eng@1981-10-30"/>
          <FRBRdate date="1981-10-30" name="Generation"/>
          <FRBRauthor href="#"/>
          <FRBRlanguage language="eng"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRthis value="/akn/gb/act/1981/64/eng@1981-10-30.akn"/>
          <FRBRuri value="/akn/gb/act/1981/64/eng@1981-10-30.akn"/>
          <FRBRdate date="1981-10-30" name="Generation"/>
          <FRBRauthor href="#"/>
        </FRBRManifestation>
      </identification>
    </meta>
    <body>
      <part eId="part-1">
        <num>1</num>
        <heading>Acquisition</heading>
        <hcontainer name="crossheading" eId="part-1-cross-1">
          <heading>By Birth</heading>
        </hcontainer>
      </part>
    </body>
  </act>
</akomaNtoso>"""
    doc = parse_akn(src)
    assert doc.body[0].akn_type == "part"
    assert doc.body[0].children[0].akn_type == "hcontainer"
    rebuilt = _round_trip(doc)
    assert rebuilt.body[0].akn_type == "part"
    assert rebuilt.body[0].children[0].akn_type == "hcontainer"


def test_round_trip_preserves_eids() -> None:
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_42",
                akn_type="section",
                position=0,
                children=[
                    Paragraph(
                        akn_eid="sec_42__para_1",
                        akn_type="paragraph",
                        position=0,
                        children=[
                            Subparagraph(
                                akn_eid="sec_42__para_1__subpara_a",
                                akn_type="subparagraph",
                                position=0,
                                text="leaf",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    rebuilt = _round_trip(doc)
    assert rebuilt.body[0].akn_eid == "sec_42"
    assert rebuilt.body[0].children[0].akn_eid == "sec_42__para_1"
    assert rebuilt.body[0].children[0].children[0].akn_eid == "sec_42__para_1__subpara_a"


def test_to_akn_emits_valid_akn() -> None:
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                number="1",
                heading="Title",
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        text="Body text.",
                    ),
                ],
            ),
        ],
    )
    xml = to_akn(doc)
    # validate_akn raises on failure; if this returns we've passed schema.
    validate_akn(xml)


def test_parse_akn_rejects_non_akomaNtoso_root() -> None:
    bad = b"<?xml version='1.0'?><foo/>"
    with pytest.raises(ValueError, match="akomaNtoso"):
        parse_akn(bad)


def test_validate_akn_raises_on_invalid_xml() -> None:
    bad = b"<?xml version='1.0'?><akomaNtoso xmlns='http://docs.oasis-open.org/legaldocml/ns/akn/3.0'/>"
    from lxml.etree import DocumentInvalid

    with pytest.raises(DocumentInvalid):
        validate_akn(bad)


def test_pydantic_offset_validator_rejects_inverted() -> None:
    with pytest.raises(ValidationError):
        Citation(start_offset=10, end_offset=5, text_snippet="bad")


def test_round_trip_preserves_intro_and_wrap_up() -> None:
    # `<intro>` and `<wrapUp>` are siblings of `<content>`; they wrap a list
    # of children with lead-in / trailing text. Real legislation uses them
    # heavily, losing them silently is a 60%+ text-loss bug.
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                intro="The following are prohibited:",
                wrap_up="except as provided in subsection (3).",
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_a",
                        akn_type="paragraph",
                        position=0,
                        text="trespass",
                    ),
                    Paragraph(
                        akn_eid="sec_1__para_b",
                        akn_type="paragraph",
                        position=1,
                        text="malicious damage",
                    ),
                ],
            ),
        ],
    )
    rebuilt = _round_trip(doc)
    assert structurally_equal(doc, rebuilt)
    assert rebuilt.body[0].intro == "The following are prohibited:"
    assert rebuilt.body[0].wrap_up == "except as provided in subsection (3)."


def test_emitter_skips_overlapping_refs() -> None:
    text = "abcdefgh"
    para = Paragraph(
        akn_eid="sec_1__para_1",
        akn_type="paragraph",
        position=0,
        text=text,
        references=[
            Citation(start_offset=0, end_offset=5, text_snippet="abcde"),
            Citation(start_offset=3, end_offset=8, text_snippet="defgh"),
        ],
    )
    doc = _basic_doc(
        body=[Section(akn_eid="sec_1", akn_type="section", position=0, children=[para])],
    )
    rebuilt = _round_trip(doc)
    rebuilt_para = rebuilt.body[0].children[0]
    assert rebuilt_para.text == text
    # Only the first ref survived; the overlapping one was dropped silently.
    assert len(rebuilt_para.references) == 1
    assert rebuilt_para.references[0].text_snippet == "abcde"


def test_emitter_clips_out_of_bounds_ref() -> None:
    text = "short"
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        text=text,
                        references=[
                            Citation(
                                start_offset=2,
                                end_offset=99,
                                text_snippet="ort but invented",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    rebuilt = _round_trip(doc)
    rebuilt_para = rebuilt.body[0].children[0]
    assert rebuilt_para.text == text
    assert rebuilt_para.references[0].text_snippet == "ort"


def test_parse_akn_raises_on_missing_expression_date() -> None:
    src = b"""<?xml version='1.0'?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="act">
    <meta>
      <identification source="#x">
        <FRBRWork>
          <FRBRuri value="/akn/gb/act/2020/1"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRuri value="/akn/gb/act/2020/1/eng"/>
          <FRBRlanguage language="eng"/>
        </FRBRExpression>
      </identification>
    </meta>
    <body/>
  </act>
</akomaNtoso>"""
    with pytest.raises(ValueError, match="FRBRExpression"):
        parse_akn(src)


class TestTheWorkDateIsTheWorks:
    """`FRBRWork/FRBRdate` used to be written from `expression_date`, so
    re-emitting an amended version wrote a derived date over the real one."""

    @staticmethod
    def _dates(doc: Document) -> tuple[str | None, str | None]:
        root = etree.fromstring(to_akn(doc).encode("utf-8"))
        work = root.find(f".//{{{AKN_NS}}}FRBRWork/{{{AKN_NS}}}FRBRdate")
        expr = root.find(f".//{{{AKN_NS}}}FRBRExpression/{{{AKN_NS}}}FRBRdate")
        assert work is not None and expr is not None
        return work.get("date"), expr.get("date")

    def test_it_is_emitted_from_the_work_not_the_expression(self) -> None:
        doc = _basic_doc().model_copy(update={"work_date": date(1981, 7, 27)})
        assert self._dates(doc) == ("1981-07-27", "1981-10-30")

    def test_without_one_the_expression_date_still_stands_in(self) -> None:
        """Optional with a fallback, so every existing construction site keeps
        emitting a schema-valid work block."""
        doc = _basic_doc().model_copy(update={"work_date": None})
        assert self._dates(doc) == ("1981-10-30", "1981-10-30")

    def test_it_survives_a_round_trip(self) -> None:
        doc = _basic_doc().model_copy(update={"work_date": date(1981, 7, 27)})
        assert _round_trip(doc).work_date == date(1981, 7, 27)


def test_each_paragraph_of_a_block_is_separately_addressable() -> None:
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                intro="The Secretary of State may:",
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        text="First limb.\nSecond limb.\nThird limb.",
                    ),
                ],
                wrap_up="and no other.",
            ),
        ],
    )
    validate_akn(to_akn(doc))
    root = etree.fromstring(to_akn(doc).encode())
    eids = [el.get("eId") for el in root.iter(f"{{{AKN_NS}}}p")]
    assert eids == [
        "sec_1__intro__p_1",
        "sec_1__para_1__p_1",
        "sec_1__para_1__p_2",
        "sec_1__para_1__p_3",
        "sec_1__wrapup__p_1",
    ]
    # The containers stay bare, as Bluebell leaves them; identity is on the <p>.
    assert root.find(f".//{{{AKN_NS}}}intro").get("eId") is None
    assert root.find(f".//{{{AKN_NS}}}wrapUp").get("eId") is None
    # The split is reversible: three <p> parse back to three lines.
    assert structurally_equal(doc, _round_trip(doc))


def test_a_reference_lands_in_the_paragraph_that_contains_it() -> None:
    # Offsets are into the whole block; the second line's ref must not be
    # rebased onto the first.
    doc = _basic_doc(
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                text="Nothing here.\nSee section 4 of that Act.",
                references=[
                    CrossReference(
                        start_offset=18,
                        end_offset=27,
                        text_snippet="section 4",
                        target_eid="sec_4",
                    ),
                ],
            ),
        ],
    )
    root = etree.fromstring(to_akn(doc).encode())
    paragraphs = list(root.iter(f"{{{AKN_NS}}}p"))
    assert paragraphs[0].find(f"{{{AKN_NS}}}ref") is None
    ref = paragraphs[1].find(f"{{{AKN_NS}}}ref")
    assert ref.get("href") == "#sec_4"
    assert ref.text == "section 4"


def test_the_space_between_two_references_survives() -> None:
    # Its tail is whitespace-only, the same shape as the indentation between
    # <p> siblings, but here it is the only thing separating two citations.
    content = etree.fromstring(
        f'<content xmlns="{AKN_NS}"><p>'
        f'See <ref href="#sec_4">section 4</ref> <ref href="#sec_5">section 5</ref>.'
        f"</p></content>".encode()
    )
    text, refs = _extract_text_and_refs(content)
    assert text == "See section 4 section 5."
    assert [(r.start_offset, r.end_offset) for r in refs] == [(4, 13), (14, 23)]


def test_a_list_beside_a_paragraph_is_not_dropped() -> None:
    # Bluebell emits prose/list/prose as siblings under <content>. Reading only
    # the <p> siblings loses every item in between.
    content = etree.fromstring(
        f'<content xmlns="{AKN_NS}"><p>Subject to this Act:</p>'
        f"<blockList><item><num>(a)</num><p>the first item</p></item>"
        f"<item><num>(b)</num><p>the second item</p></item></blockList>"
        f"<p>Nothing limits any other law.</p></content>".encode()
    )
    text, _ = _extract_text_and_refs(content)
    assert "the first item" in text
    assert "the second item" in text
