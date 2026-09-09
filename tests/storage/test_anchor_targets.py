"""A same-document anchor becomes a row only when the source carries its target.

A publisher's rendering can key anchors to its own transform rather than to the
legal structure. Rowed, they are indistinguishable from a citation we failed to
resolve, and no later acquisition can resolve them. Parsing stays lossless; the
judgement belongs where references become rows, against the ids the source
document actually carries.
"""

from __future__ import annotations

import uuid
from datetime import date

from codify.akn import AKN_NS, Article, Document, Paragraph
from codify.akn._parser import carried_ids
from codify.akn._schema import parse_xml
from codify.akn.references import CrossReference
from codify.storage.mappers import document_to_rows


def _ref(target: str, origin: str) -> CrossReference:
    return CrossReference(
        start_offset=0, end_offset=5, text_snippet="above", target_eid=target, origin=origin
    )


def _doc(*refs: CrossReference) -> Document:
    return Document(
        frbr_work_uri="/akn/xz/act/2024/1",
        frbr_expression_uri="/akn/xz/act/2024/1/eng@2024-01-01",
        language="eng",
        expression_date=date(2024, 1, 1),
        body=[
            Article(
                akn_eid="art_1",
                akn_type="article",
                position=1,
                children=[
                    Paragraph(
                        akn_eid="art_1__p_1",
                        akn_type="paragraph",
                        position=1,
                        text="A duty applies as provided elsewhere in this Act.",
                        references=list(refs),
                    )
                ],
            )
        ],
    )


def _targets(doc: Document, known: set[str] | None) -> list[str]:
    rows = document_to_rows(doc, law_id=uuid.uuid4(), known_ids=known)[3]
    return [r.target_uri for r in rows]


_CARRIED = {"art_1", "art_1__p_1"}


def test_an_anchor_the_source_does_not_carry_is_not_rowed() -> None:
    doc = _doc(_ref("art_1__p_1", "href"), _ref("d24e21476", "href"))
    assert _targets(doc, _CARRIED) == ["#art_1__p_1"]


def test_a_reading_of_prose_is_never_dropped() -> None:
    """Our own passes refuse a target they cannot find as they write it, so a
    derived reference is spared the check rather than judged twice."""
    assert _targets(_doc(_ref("d24e21476", "text")), _CARRIED) == ["#d24e21476"]


def test_nothing_is_dropped_when_the_source_is_not_available() -> None:
    """A document rowed without its source cannot be refuted, so the check does
    not run rather than calling every anchor foreign."""
    assert _targets(_doc(_ref("d24e21476", "href")), None) == ["#d24e21476"]


def test_the_origin_of_an_intra_document_reference_is_stored() -> None:
    """Read back from the database an unrecorded origin reads as the publisher's
    own link, and the rule sparing our readings would stop sparing them."""
    rows = document_to_rows(_doc(_ref("art_1__p_1", "text")), law_id=uuid.uuid4(), known_ids=None)
    assert [r.resolution_origin for r in rows[3]] == ["text"]


def test_the_ids_come_from_the_tree_and_not_from_the_rows() -> None:
    """An id can sit on an element no row is made from, and on `wId` rather than
    `eId`. A reference to either is one the document can answer for."""
    xml = (
        f'<akomaNtoso xmlns="{AKN_NS}"><act name="act"><meta/>'
        '<body><section eId="sec_1" wId="sec_1_w"><num>1</num><content>'
        '<p eId="sec_1__p_1">Text with <span eId="inline_1">a marked phrase</span>.</p>'
        "</content></section></body></act></akomaNtoso>"
    )
    known = carried_ids(parse_xml(xml))
    assert {"sec_1", "sec_1_w", "sec_1__p_1", "inline_1"} <= known
    assert _targets(_doc(_ref("inline_1", "href")), known) == ["#inline_1"]
