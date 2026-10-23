"""What a direct-route observation records: usage, and cost where we have one."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, Iterator

import httpx
import pytest
from structlog.testing import capture_logs

from codify.core import llm as llm_module
from codify.core import tracing
from codify.core.llm import LiteLLMClient
from codify.core.tracing import record_usage


class _Observation:
    """Collects every `update()` the client makes, merged in call order."""

    def __init__(self) -> None:
        self.fields: dict[str, Any] = {}

    def update(self, **kwargs: Any) -> None:
        self.fields.update(kwargs)


def _observed(monkeypatch: pytest.MonkeyPatch, usage_info: dict[str, Any]) -> dict[str, Any]:
    """Run one ocr() call against a stubbed endpoint; hand back what was recorded."""
    observation = _Observation()

    @contextmanager
    def _stub(*_: object, **__: object) -> Iterator[_Observation]:
        yield observation

    class _Response:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self) -> dict[str, Any]:
            return {"pages": [{"index": 0, "markdown": "m"}], "usage_info": usage_info}

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> _Response:
            return _Response()

    monkeypatch.setattr(llm_module, "direct_observation", _stub)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())
    client = LiteLLMClient(
        base_url="http://x/v1",
        api_key="k",
        model="m",
        azure_foundry_ocr_url="http://foundry/ocr",
        azure_foundry_ocr_key="key",
    )
    asyncio.run(client.ocr(pdf=b"%PDF-1.4", model="mistral-ocr-4-0"))
    return observation.fields


def test_ocr_records_pages_and_the_cost_of_them(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = _observed(monkeypatch, {"pages_processed": 4, "doc_size_bytes": 34281})
    assert fields["usage_details"] == {"pages_processed": 4}
    assert fields["cost_details"] == pytest.approx({"total": 0.0176})


def test_ocr_keeps_doc_size_out_of_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    # Left in the usage blob it is summed into the token total beside the page
    # count, which is how a week of OCR read as 73.7M tokens.
    fields = _observed(monkeypatch, {"pages_processed": 4, "doc_size_bytes": 34281})
    assert "doc_size_bytes" not in fields["usage_details"]
    assert fields["metadata"] == {"doc_size_bytes": 34281}


def test_ocr_without_usage_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _observed(monkeypatch, {}) == {}


def test_a_new_provider_usage_key_is_not_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The units are named, not filtered. Filtering out the one key known to
    have broken the total leaves the next one Mistral adds free to break it."""
    fields = _observed(
        monkeypatch,
        {"pages_processed": 4, "doc_size_bytes": 34281, "images_processed": 900, "cached": True},
    )
    assert fields["usage_details"] == {"pages_processed": 4}
    assert fields["cost_details"] == pytest.approx({"total": 0.0176})


def test_a_boolean_never_lands_in_the_usage_total() -> None:
    """bool is an int subclass, so a provider flag would count as a 1."""
    observation = _Observation()
    record_usage(observation, "mistral-ocr-4-0", {"pages_processed": 2, "cached": True})
    assert observation.fields["usage_details"] == {"pages_processed": 2}


def test_an_unpriced_model_says_so_once() -> None:
    """A route the table does not know is invisible in cost terms, and reads as
    free. `mistral-ocr-4-1` would route direct and record pages at nothing."""
    tracing._UNPRICED.discard("mistral-ocr-4-1")
    with capture_logs() as captured:
        for _ in range(3):
            record_usage(_Observation(), "mistral-ocr-4-1", {"pages_processed": 2})
    warned = [c for c in captured if c["event"] == "llm_model_unpriced"]
    assert len(warned) == 1, captured
    assert warned[0]["model"] == "mistral-ocr-4-1"


def _agent_messages() -> list[Any]:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.usage import RequestUsage

    return [
        ModelRequest(parts=[UserPromptPart(content="q")]),
        ModelResponse(
            parts=[TextPart("a")], usage=RequestUsage(input_tokens=100, output_tokens=40)
        ),
        ModelRequest(parts=[UserPromptPart(content="q2")]),
        ModelResponse(
            parts=[TextPart("b")], usage=RequestUsage(input_tokens=300, output_tokens=60)
        ),
    ]


def _agent_observed(monkeypatch: pytest.MonkeyPatch, messages: list[Any]) -> list[dict[str, Any]]:
    opened: list[dict[str, Any]] = []

    @contextmanager
    def _stub(name: str, **kwargs: Any) -> Iterator[_Observation]:
        observation = _Observation()
        yield observation
        opened.append({"name": name, **kwargs, **observation.fields})

    monkeypatch.setattr(tracing, "direct_observation", _stub)
    tracing.record_agent_usage("agent.repair", "gemini/gemini-3.6-flash", messages)
    return opened


def test_an_agent_run_is_priced_from_every_response(monkeypatch: pytest.MonkeyPatch) -> None:
    (observed,) = _agent_observed(monkeypatch, _agent_messages())
    assert observed["usage_details"] == {"input": 400, "output": 100}
    assert observed["cost_details"] == pytest.approx(
        {"input": 0.0003, "output": 0.000375, "total": 0.000675}
    )
    assert observed["metadata"] == {"route": "agent", "requests": 2}


def test_an_agent_run_with_no_response_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _agent_observed(monkeypatch, _agent_messages()[:1]) == []


def test_an_agent_run_that_reported_no_usage_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic_ai.messages import ModelResponse, TextPart

    assert _agent_observed(monkeypatch, [ModelResponse(parts=[TextPart("a")])]) == []
