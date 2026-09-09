from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codify.jurisdictions import JurisdictionConfig
from codify.pipeline import stages
from codify.pipeline.enrich.ocr import PageResult
from codify.pipeline.formats import pdf


def test_readable_classification_preserves_source_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = JurisdictionConfig.model_validate(
        {
            "code": "xx",
            "name": "Example",
            "tradition": ["common_law"],
            "languages": ["eng"],
            "document_classes": {
                "act": {"label": "Act"},
                "circular": {"label": "Circular"},
            },
            "structuring": {
                "classification_rules": [
                    {
                        "signal": "preamble_match",
                        "pattern": "^OFFICE OF THE REGULATOR",
                        "target_document_class": "circular",
                    }
                ]
            },
        }
    )
    monkeypatch.setattr(stages, "load_config", lambda _: cfg)
    original = b"%PDF-1.7 opaque compressed stream"
    desc = stages.resolve_descriptors(
        {"title": "Reporting requirements", "year": 2025},
        jurisdiction_code="xx",
        source_bytes=original,
        fallback_stem="source",
        classification_text="OFFICE OF THE REGULATOR\nCircular 12",
    )
    assert desc.doctype == "circular"
    assert desc.number == stages.draft_number(original)
    assert desc.number != stages.draft_number(b"OFFICE OF THE REGULATOR\nCircular 12")


@pytest.mark.asyncio
async def test_pdf_lane_passes_extracted_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class DescriptorsReached(Exception):
        pass

    observed: dict[str, Any] = {}

    def capture(metadata: dict[str, Any], **kwargs: Any) -> None:
        observed.update(kwargs)
        raise DescriptorsReached

    monkeypatch.setattr(pdf, "extract_metadata", AsyncMock(return_value={"title": "Notice"}))
    monkeypatch.setattr(pdf, "try_load_config", lambda _: None)
    monkeypatch.setattr(pdf, "resolve_descriptors", capture)
    original = b"%PDF-1.7 compressed source"
    pages = [PageResult(page_number=1, text="OFFICE OF THE REGULATOR\nCircular 12", method="text")]
    with pytest.raises(DescriptorsReached):
        async for _ in pdf._ingest_pages(
            pages,
            "xx",
            llm=MagicMock(),
            source_bytes=original,
            source_name="source.pdf",
            fallback_stem="source",
            model=None,
            on_scan=None,
            langfuse=MagicMock(),
            root=None,
        ):
            pass
    assert observed["source_bytes"] == original
    assert "OFFICE OF THE REGULATOR\nCircular 12" in observed["classification_text"]
