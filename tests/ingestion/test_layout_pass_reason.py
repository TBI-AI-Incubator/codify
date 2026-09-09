"""A failed layout pass says why, and marks the pages it would have covered.
Without a rival read there is no divergence score, so the text layer is kept for
want of a second opinion; a run silent about that reads as a clean pass."""

from __future__ import annotations

import io
from typing import Any

import pytest
from pypdf import PdfWriter
from structlog.testing import capture_logs

from codify.core.llm import OcrNotConfigured
from codify.pipeline.enrich.ocr import (
    PageResult,
    _promote_rival_when_empty,
    _settle_rival_available,
    extract_text_from_pdf,
)

MESSAGE = "azure_foundry_ocr not configured; set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY"


class _NoOcrClient:
    """Configured for chat and not for the direct OCR endpoint."""

    model = "test-model"

    async def ocr(self, **_: Any) -> list[dict[str, Any]]:
        raise OcrNotConfigured(MESSAGE)

    async def vision(self, **_: Any) -> str:
        return "transcribed page text"

    async def complete(self, **_: Any) -> str:
        return ""


# Every case here keeps its text layer, which also keeps the file off poppler:
# the divert to vision rasterises, and the CI image has no renderer.
BODY = "Article 1. The provisions of this Law apply to every person. " * 6


@pytest.fixture(autouse=True)
def _text_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pypdf._page.PageObject.extract_text", lambda self, *a, **k: BODY)


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595.0, height=842.0)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_a_retained_text_layer_names_what_did_not_check_it() -> None:
    """The failure this exists for. A page long enough to be trusted never
    reaches OCR, so `divergence` is measured against an empty rival and reads as
    agreement unless the page says no rival existed."""
    with capture_logs() as captured:
        pages = await extract_text_from_pdf(_blank_pdf(), client=_NoOcrClient())  # type: ignore[arg-type]

    failures = [r for r in captured if r.get("event") == "layout_pass_failed"]
    assert failures, "the engine swap must stay loud"
    assert failures[0]["error"] == MESSAGE, "the type alone does not say which variable is unset"

    assert [p.method for p in pages] == ["text_extraction"], "the layer was kept"
    assert all(not p.rival_available for p in pages), (
        "a page kept without a rival must say so, or the output reads as a trusted layer"
    )


@pytest.mark.asyncio
async def test_no_client_reads_the_same_as_a_failed_pass() -> None:
    """Nothing read these pages twice either, and the flag is about the second
    read rather than about which way it went missing."""
    pages = await extract_text_from_pdf(_blank_pdf(), client=None)

    assert pages and all(not p.rival_available for p in pages)


def test_a_promoted_rival_is_one_engine_not_two() -> None:
    """Promotion moves the empty read into `rival_text`, leaving one engine's
    words in two fields. The page has been read once, and must say so."""
    page = PageResult(
        page_number=1,
        text="",
        method="vision_ocr",
        rival_text="the layout engine's transcription",
        rival_available=True,
        divert_reason="too_short",
    )

    promoted = _promote_rival_when_empty(page)

    assert promoted.text and not promoted.rival_text.strip(), "the empty read swapped in"
    assert not _settle_rival_available([promoted])[0].rival_available
