from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from codify.akn import (
    AmendmentReference,
    Article,
    Chapter,
    Citation,
    CrossReference,
    Document,
    Paragraph,
    Point,
    Section,
    Subparagraph,
    Title,
)


@pytest.fixture
def deep_doc() -> Document:
    return Document(
        frbr_work_uri="/akn/al/act/2020/162",
        frbr_expression_uri="/akn/al/act/2020/162/eng@2020-06-15",
        language="eng",
        expression_date=date(2020, 6, 15),
        body=[
            Title(
                akn_eid="ttl_1",
                akn_type="title",
                position=0,
                heading="Public Procurement",
                children=[
                    Chapter(
                        akn_eid=f"ttl_1__chp_{c}",
                        akn_type="chapter",
                        position=c,
                        number=str(c + 1),
                        children=[
                            Section(
                                akn_eid=f"ttl_1__chp_{c}__sec_{s}",
                                akn_type="section",
                                position=s,
                                children=[
                                    Article(
                                        akn_eid=f"ttl_1__chp_{c}__sec_{s}__art_{a}",
                                        akn_type="article",
                                        position=a,
                                        children=[
                                            Paragraph(
                                                akn_eid=f"ttl_1__chp_{c}__sec_{s}__art_{a}__para_{p}",
                                                akn_type="paragraph",
                                                position=p,
                                                text=f"Paragraph {p} of article {a}.",
                                                references=[
                                                    Citation(
                                                        start_offset=0,
                                                        end_offset=10,
                                                        text_snippet="Paragraph",
                                                        target_uri="/akn/al/act/2006/9643",
                                                    ),
                                                ]
                                                if p == 0
                                                else [],
                                            )
                                            for p in range(4)
                                        ]
                                        + [
                                            Subparagraph(
                                                akn_eid=f"ttl_1__chp_{c}__sec_{s}__art_{a}__subpara_a",
                                                akn_type="subparagraph",
                                                position=4,
                                                text="Subparagraph (a).",
                                                children=[
                                                    Point(
                                                        akn_eid=f"ttl_1__chp_{c}__sec_{s}__art_{a}__subpara_a__point_i",
                                                        akn_type="point",
                                                        position=0,
                                                        text="Point (i).",
                                                        references=[
                                                            CrossReference(
                                                                start_offset=0,
                                                                end_offset=5,
                                                                text_snippet="Point",
                                                                target_eid=f"ttl_1__chp_{c}__sec_0__art_0",
                                                            ),
                                                            AmendmentReference(
                                                                start_offset=6,
                                                                end_offset=10,
                                                                text_snippet="(i).",
                                                                amends_uri="/akn/al/act/2018/100/eng@2018-12-01",
                                                                operation="replace",
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    )
                                    for a in range(3)
                                ],
                            )
                            for s in range(2)
                        ],
                    )
                    for c in range(2)
                ],
            )
        ],
    )


def test_json_round_trip_preserves_equality(deep_doc: Document) -> None:
    payload = deep_doc.model_dump_json()
    reconstructed = Document.model_validate_json(payload)
    assert reconstructed == deep_doc


def test_dict_round_trip_preserves_equality(deep_doc: Document) -> None:
    payload = deep_doc.model_dump(mode="json")
    reconstructed = Document.model_validate(payload)
    assert reconstructed == deep_doc


def test_deep_copy_equals_original(deep_doc: Document) -> None:
    assert deep_doc.model_copy(deep=True) == deep_doc


def test_discriminator_preserves_concrete_types(deep_doc: Document) -> None:
    payload = deep_doc.model_dump_json()
    reconstructed = Document.model_validate_json(payload)
    title = reconstructed.body[0]
    assert isinstance(title, Title)
    chapter = title.children[0]
    assert isinstance(chapter, Chapter)
    section = chapter.children[0]
    assert isinstance(section, Section)
    article = section.children[0]
    assert isinstance(article, Article)
    paragraph = article.children[0]
    assert isinstance(paragraph, Paragraph)
    subpara = article.children[-1]
    assert isinstance(subpara, Subparagraph)
    point = subpara.children[0]
    assert isinstance(point, Point)
    citation = paragraph.references[0]
    assert isinstance(citation, Citation)
    xref = point.references[0]
    assert isinstance(xref, CrossReference)
    amend = point.references[1]
    assert isinstance(amend, AmendmentReference)


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        Paragraph.model_validate(
            {
                "akn_eid": "p_1",
                "akn_type": "paragraph",
                "position": 0,
                "kind": "paragraph",
                "rogue_field": "boom",
            }
        )


def test_inverted_offsets_rejected() -> None:
    with pytest.raises(ValidationError):
        Citation(start_offset=10, end_offset=5, text_snippet="bad")


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        Document.model_validate(
            {
                "frbr_work_uri": "/akn/al/act/2020/162",
                "frbr_expression_uri": "/akn/al/act/2020/162/eng@2020-06-15",
                "language": "eng",
                "expression_date": "2020-06-15",
                "body": [
                    {
                        "akn_eid": "x_1",
                        "akn_type": "x",
                        "position": 0,
                        "kind": "not-a-real-kind",
                    }
                ],
            }
        )
