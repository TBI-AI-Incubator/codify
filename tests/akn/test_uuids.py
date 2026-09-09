from __future__ import annotations

from datetime import date
from uuid import UUID

from codify.akn import Citation, Document, Paragraph, Section, Title


def _doc() -> Document:
    return Document(
        frbr_work_uri="/akn/al/act/2020/1",
        frbr_expression_uri="/akn/al/act/2020/1/eng@2020-01-01",
        language="eng",
        expression_date=date(2020, 1, 1),
        body=[
            Title(
                akn_eid="ttl_1",
                akn_type="title",
                position=0,
                children=[
                    Section(
                        akn_eid="ttl_1__sec_1",
                        akn_type="section",
                        position=0,
                        children=[
                            Paragraph(
                                akn_eid="ttl_1__sec_1__para_1",
                                akn_type="paragraph",
                                position=0,
                                text="Hello.",
                                references=[
                                    Citation(start_offset=0, end_offset=5, text_snippet="Hello"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def test_ids_are_uuid_instances() -> None:
    doc = _doc()
    assert isinstance(doc.id, UUID)
    title = doc.body[0]
    assert isinstance(title.id, UUID)
    citation = title.children[0].children[0].references[0]
    assert isinstance(citation.id, UUID)


def test_fresh_documents_have_distinct_ids() -> None:
    assert _doc().id != _doc().id


def test_round_trip_preserves_ids() -> None:
    doc = _doc()
    original_doc_id = doc.id
    original_title_id = doc.body[0].id
    original_paragraph_id = doc.body[0].children[0].children[0].id
    original_citation_id = doc.body[0].children[0].children[0].references[0].id

    reconstructed = Document.model_validate_json(doc.model_dump_json())

    assert reconstructed.id == original_doc_id
    assert reconstructed.body[0].id == original_title_id
    assert reconstructed.body[0].children[0].children[0].id == original_paragraph_id
    assert reconstructed.body[0].children[0].children[0].references[0].id == original_citation_id


def test_deep_copy_preserves_ids() -> None:
    doc = _doc()
    copy = doc.model_copy(deep=True)
    assert copy.id == doc.id
    assert copy.body[0].id == doc.body[0].id
    assert copy.body[0].children[0].children[0].id == doc.body[0].children[0].children[0].id
