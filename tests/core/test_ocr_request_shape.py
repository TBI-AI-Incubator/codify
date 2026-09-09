"""What the OCR request asks the endpoint for."""

import asyncio
from typing import Any

import httpx
import pytest

from codify.core.llm import LiteLLMClient


class _Response:
    status_code = 200
    headers: dict[str, str] = {}
    text = ""

    def json(self) -> dict[str, object]:
        return {"pages": [{"index": 0, "markdown": "m", "header": "h", "footer": "f"}]}


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run one ocr() call against a stubbed transport; hand back the payload."""
    sent: dict[str, Any] = {}

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **kwargs: object) -> _Response:
            sent.update(kwargs.get("json") or {})  # type: ignore[arg-type]
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())
    client = LiteLLMClient(
        base_url="http://x/v1",
        api_key="k",
        model="m",
        azure_foundry_ocr_url="http://foundry/ocr",
        azure_foundry_ocr_key="key",
    )
    asyncio.run(client.ocr(pdf=b"%PDF-1.4", model="mistral-ocr-4-0"))
    return sent


def test_request_asks_for_header_and_footer_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _capture(monkeypatch)
    assert payload["extract_header"] is True
    assert payload["extract_footer"] is True


def test_request_pins_include_blocks_rather_than_inheriting_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _capture(monkeypatch)["include_blocks"] is True


def test_request_still_carries_model_and_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _capture(monkeypatch)
    assert payload["model"] == "mistral-ocr-4-0"
    assert payload["document"]["type"] == "document_url"
