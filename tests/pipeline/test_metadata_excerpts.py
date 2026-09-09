from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from codify.pipeline.enrich.metadata import ExtractedMetadata, _metadata_text, extract_metadata


@pytest.mark.parametrize("length", [0, 7999, 8000])
def test_short_document_is_preserved_once(length: int) -> None:
    source = "x" * length
    assert _metadata_text(source) == source


@pytest.mark.parametrize("length", [8001, 24000])
def test_long_document_keeps_identity_and_signing_evidence_within_budget(length: int) -> None:
    opening = "CIRCULAR NO. 42\nSeries of 2025\n"
    closing = "Signed and issued on 30 May 2025.\nGovernor"
    source = opening + "x" * (length - len(opening) - len(closing)) + closing
    excerpt = _metadata_text(source)
    assert len(excerpt) == 8000
    assert excerpt.startswith(opening)
    assert excerpt.endswith(closing)
    assert excerpt.count(opening) == 1
    assert excerpt.count(closing) == 1
    assert "[Middle of document omitted]" in excerpt


@pytest.mark.asyncio
async def test_metadata_call_receives_closing_date_and_reference_distinction() -> None:
    source = (
        "CIRCULAR NO. 42\nThe Board in Resolution No. 7 dated 22 May 2025 approved these rules.\n"
        + "Operative text. " * 1000
        + "\nSigned and issued on 30 May 2025."
    )
    client = AsyncMock()
    client.chat_schema.return_value = ExtractedMetadata(number="42", date="2025-05-30")
    result = await extract_metadata(source, client, filename="source.pdf")
    call = client.chat_schema.call_args.kwargs
    assert "CIRCULAR NO. 42" in call["prompt"]
    assert "Signed and issued on 30 May 2025." in call["prompt"]
    assert "cited resolution" in call["system"]
    assert "relative effectivity clause" in call["system"]
    assert "technical digital-signature timestamp" in call["system"]
    assert result["number"] == "42"
