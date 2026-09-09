"""Guard: no OCR log record carries text lifted off the page.

Asserted on the emitted records, not on `error_fields` in isolation: a
regression to `error=str(exc)` is invisible to a unit test of the projection.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from structlog.testing import capture_logs

from codify.core.llm import OcrStatusError
from codify.pipeline.enrich.ocr import _layout_pass, _LayoutPassFailed

# Short and leading, so a truncated `str(exc)` still trips the check: pydantic
# spends ~100 characters on preamble before `input_value`.
SENTINEL = "مادة٧٩"
PAGE_TEXT = f"{SENTINEL} على جميع الجهات المختصة، كل فيما يخصه، تنفيذ أحكام هذه اللائحة"


def _records(captured: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [r for r in captured if r.get("event") == event]


def _assert_clean(record: dict[str, Any]) -> None:
    assert SENTINEL not in repr(record), f"page text reached the log: {record}"


@pytest.mark.parametrize(
    "exc",
    [
        OcrStatusError(400),
        RuntimeError(f"provider echoed: {PAGE_TEXT}"),
    ],
    ids=["status_error", "arbitrary_provider_failure"],
)
def test_a_failed_layout_pass_logs_no_page_text(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    async def _boom(**_: object) -> list[dict[str, Any]]:
        raise exc

    monkeypatch.setattr("codify.pipeline.enrich.ocr._mistral_ocr_pdf_chunked", _boom)

    with capture_logs() as captured, pytest.raises(_LayoutPassFailed):
        asyncio.run(
            _layout_pass(client=object(), pdf_bytes=b"%PDF-1.4", reader=object(), total_pages=3)
        )

    records = _records(captured, "layout_pass_failed")
    assert records, "the engine swap must stay loud"
    _assert_clean(records[0])
    assert records[0]["error_type"] == type(exc).__name__


def test_an_unparsable_layout_page_logs_field_paths_not_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`content` arriving as a list: pydantic quotes the page in `input_value`."""

    async def _pages(**_: object) -> list[dict[str, Any]]:
        block = {"type": "text", "content": [PAGE_TEXT]}
        return [{"index": 0, "markdown": PAGE_TEXT, "blocks": [block]}]

    monkeypatch.setattr("codify.pipeline.enrich.ocr._mistral_ocr_pdf_chunked", _pages)

    with capture_logs() as captured:
        layouts, rivals = asyncio.run(
            _layout_pass(client=object(), pdf_bytes=b"%PDF-1.4", reader=object(), total_pages=1)
        )

    assert layouts == {}  # the page is absent, not empty
    assert rivals[1]  # the transcription still survives the layout failure
    records = _records(captured, "layout_page_unparsed")
    assert records, "a page that would not parse must say so"
    _assert_clean(records[0])
    assert records[0]["fields"], "field paths are what replaced the message"
