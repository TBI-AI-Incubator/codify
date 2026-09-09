"""Visible PDF overlays must participate in the source read."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from codify.core.llm import LLMClient
from codify.pipeline.enrich import ocr


@pytest.mark.parametrize(
    "annotation,expected",
    [
        ({"/Subtype": "/FreeText", "/Contents": "9001"}, True),
        ({"/Subtype": "/Widget", "/AP": {"/N": "signature appearance"}}, True),
        ({"/Subtype": "/Stamp", "/AP": {"/N": "stamp appearance"}}, True),
        ({"/Subtype": "/FreeText", "/Contents": "9001", "/F": 1}, True),
        ({"/Subtype": "/Widget", "/AP": {"/N": "signature"}, "/F": 1}, True),
        ({"/Subtype": "/Stamp", "/AP": {"/N": "stamp"}, "/F": 1}, True),
        ({"/Subtype": "/Link", "/Contents": "9001"}, False),
        ({"/Subtype": "/Text", "/Contents": "editor comment"}, False),
        ({"/Subtype": "/Widget"}, False),
        ({"/Subtype": "/FreeText", "/Contents": "9001", "/F": 2}, False),
        ({"/Subtype": "/FreeText", "/Contents": "9001", "/F": 32}, False),
    ],
)
def test_visible_overlay_selection(annotation: dict[str, Any], expected: bool) -> None:
    assert ocr._has_visible_text_overlay({"/Annots": [annotation]}) is expected


@pytest.mark.parametrize("model", [None, "mistral-ocr-4-0"])
async def test_overlay_diverts_an_otherwise_trusted_layer(
    monkeypatch: pytest.MonkeyPatch, model: str | None
) -> None:
    layer = "A sufficiently long and readable paragraph of synthetic source text. " * 5

    class Page(dict[str, Any]):
        def extract_text(self) -> str:
            return layer

    pages = [Page({"/Annots": [{"/Subtype": "/FreeText", "/Contents": "9001"}]}), Page()]
    monkeypatch.setattr(ocr, "PdfReader", lambda *_: SimpleNamespace(pages=pages))
    monkeypatch.setattr(ocr, "build_document_context", lambda *_: "")
    monkeypatch.setattr(ocr, "_layout_pass", AsyncMock(return_value=({}, {1: layer, 2: layer})))
    monkeypatch.setattr(ocr, "_render_window", AsyncMock(return_value={1: b"image"}))
    monkeypatch.setattr(ocr, "ink_ratio", lambda _: 0.2)
    vision = AsyncMock(return_value="9001\n" + layer)
    monkeypatch.setattr(ocr, "_ocr_page_with_context", vision)
    direct = AsyncMock(
        return_value=[
            {"index": 0, "markdown": "overlay rival"},
            {
                "index": 1,
                "markdown": "authoritative scanned page",
                "header": "scan header",
                "footer": "scan footer",
            },
        ]
    )
    monkeypatch.setattr(ocr, "_mistral_ocr_pdf_chunked", direct)
    result = await ocr.extract_text_from_pdf(
        b"fake", client=cast(LLMClient, AnyClient()), ocr_model=model
    )
    assert result[0].method == "vision_ocr"
    assert result[0].divert_reason == "visible_annotation"
    assert result[0].text.startswith("9001")
    if model:
        assert result[1].method == "mistral_ocr"
        assert result[1].model == model
        assert result[1].text == "authoritative scanned page"
        assert result[1].header == "scan header"
        assert result[1].footer == "scan footer"
        assert result[0].rival_text == "overlay rival"
        direct.assert_awaited_once()
    else:
        assert result[1].method == "text_extraction"
        direct.assert_not_awaited()
    vision.assert_awaited_once()


class AnyClient:
    model = "example-model"
