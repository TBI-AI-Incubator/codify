"""Event-shape unit tests."""

from __future__ import annotations

from codify.akn import Document
from codify.pipeline.events import (
    Complete,
    Enriched,
    Failed,
    MetadataExtracted,
    PageExtracted,
    Parsed,
    Structured,
    ValidationIssued,
)


def test_event_discriminator_round_trips() -> None:
    """Each event variant carries a unique `kind` literal for the discriminator."""
    kinds = {
        PageExtracted(page=1, method="text", text_len=42).kind,
        MetadataExtracted(metadata={"title": "x"}).kind,
        Structured(bluebell_len=10).kind,
        Parsed(akn_xml_len=20).kind,
        Enriched(pass_name="cobalt").kind,
        ValidationIssued(issue={"code": "X1", "msg": "y"}).kind,
        Failed(stage="extract", error="boom").kind,
    }
    assert kinds == {
        "page_extracted",
        "metadata_extracted",
        "structured",
        "parsed",
        "enriched",
        "validation_issued",
        "failed",
    }


def test_complete_event_carries_document_and_xml() -> None:
    """Complete is the terminal event; both Document and akn_xml are required."""
    from datetime import date as date_cls

    doc = Document(
        frbr_work_uri="/akn/test/act/2024/1",
        frbr_expression_uri="/akn/test/act/2024/1/eng@2024-01-01",
        language="eng",
        expression_date=date_cls(2024, 1, 1),
    )
    event = Complete(document=doc, akn_xml="<akomaNtoso/>")
    assert event.kind == "complete"
    assert event.document is doc
    assert event.akn_xml == "<akomaNtoso/>"
