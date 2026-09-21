"""Unit coverage for the LiteLLM-backed LLMClient."""

from __future__ import annotations

import base64
from contextlib import nullcontext
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from codify.core.llm import LiteLLMClient, _is_transient, _sniff_mime


def _resp(content: str) -> MagicMock:
    msg = MagicMock(content=content)
    choice = MagicMock(message=msg)
    return MagicMock(choices=[choice])


def _streaming_chunks(deltas: list[str]) -> Any:
    """Build an async iterator of streaming chunk objects."""

    class _AsyncIter:
        def __init__(self, items: list[Any]) -> None:
            self._items = iter(items)

        def __aiter__(self) -> "_AsyncIter":
            return self

        async def __anext__(self) -> Any:
            try:
                return next(self._items)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    chunks = []
    for d in deltas:
        delta = MagicMock(content=d)
        choice = MagicMock(delta=delta)
        chunks.append(MagicMock(choices=[choice]))
    return _AsyncIter(chunks)


@pytest.fixture
def client() -> LiteLLMClient:
    c = LiteLLMClient(base_url="http://x/v1", api_key="k", model="gemini-3.6-flash")
    c.client = MagicMock()
    c.client.chat = MagicMock()
    c.client.chat.completions = MagicMock()
    c.client.chat.completions.create = AsyncMock()
    return c


async def test_chat_returns_content(client: LiteLLMClient) -> None:
    client.client.chat.completions.create.return_value = _resp("hello")
    out = await client.chat("hi", system="be terse")
    assert out == "hello"
    args = client.client.chat.completions.create.call_args.kwargs
    assert args["model"] == "gemini-3.6-flash"
    assert args["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]


async def test_chat_per_call_model_override(client: LiteLLMClient) -> None:
    client.client.chat.completions.create.return_value = _resp("ok")
    await client.chat("hi", model="gemini-3.5-flash-lite")
    assert (
        client.client.chat.completions.create.call_args.kwargs["model"] == "gemini-3.5-flash-lite"
    )


async def test_chat_stream_invokes_on_chunk(client: LiteLLMClient) -> None:
    """on_chunk receives the accumulated text after each delta, not the delta itself."""
    client.client.chat.completions.create.return_value = _streaming_chunks(["he", "llo"])
    seen: list[str] = []
    out = await client.chat_stream("hi", on_chunk=seen.append)
    assert out == "hello"
    assert seen == ["he", "hello"]


async def test_direct_chat_stream_requests_usage(
    client: LiteLLMClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation = MagicMock()
    monkeypatch.setattr(
        "codify.core.llm.direct_observation", lambda *_a, **_kw: nullcontext(observation)
    )
    client.telemetry_mode = "direct"
    client.client.chat.completions.create.return_value = _streaming_chunks(["ok"])

    assert await client.chat_stream("hi") == "ok"

    kwargs = client.client.chat.completions.create.call_args.kwargs
    assert kwargs["stream_options"] == {"include_usage": True}


async def test_chat_stream_awaits_async_on_chunk(client: LiteLLMClient) -> None:
    """Async on_chunk callbacks are awaited; sync ones are called as-is."""
    client.client.chat.completions.create.return_value = _streaming_chunks(["he", "llo"])
    awaited: list[str] = []

    async def async_cb(text: str) -> None:
        awaited.append(text)

    out = await client.chat_stream("hi", on_chunk=async_cb)
    assert out == "hello"
    assert awaited == ["he", "hello"]


async def test_chat_stream_retries_on_transient(client: LiteLLMClient) -> None:
    err = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    client.client.chat.completions.create.side_effect = [
        err,
        _streaming_chunks(["ok"]),
    ]
    out = await client.chat_stream("hi")
    assert out == "ok"
    assert client.client.chat.completions.create.await_count == 2


async def test_chat_json_parses(client: LiteLLMClient) -> None:
    client.client.chat.completions.create.return_value = _resp('{"verdict": "aligned"}')
    result = await client.chat_json("classify this", seed=7)
    assert result == {"verdict": "aligned"}
    args = client.client.chat.completions.create.call_args.kwargs
    assert args["response_format"] == {"type": "json_object"}
    assert args["seed"] == 7


async def test_a_seed_reaches_the_model_as_an_instruction(client: LiteLLMClient) -> None:
    """Gemini has no ``seed`` and ignores ``temperature``/``top_p``/``top_k``, so a
    reproducibility requirement carried only by a request parameter is discarded in
    transit. The gateway does not error on that, so nothing else would notice."""
    client.client.chat.completions.create.return_value = _resp("{}")
    await client.chat_json("classify this", system="be terse", seed=7)
    args = client.client.chat.completions.create.call_args.kwargs
    system = args["messages"][0]
    assert system["role"] == "system"
    assert "be terse" in system["content"]
    assert "deterministically" in system["content"]
    assert "7" in system["content"]


async def test_seed_zero_is_a_seed(client: LiteLLMClient) -> None:
    """Zero is forwarded as a seed like any other integer, so it has to carry the
    instruction too; a truthiness check would silently exempt it."""
    client.client.chat.completions.create.return_value = _resp("{}")
    await client.chat_json("classify this", seed=0)
    args = client.client.chat.completions.create.call_args.kwargs
    assert args["seed"] == 0
    assert "deterministically" in args["messages"][0]["content"]


async def test_no_seed_leaves_the_instructions_alone(client: LiteLLMClient) -> None:
    client.client.chat.completions.create.return_value = _resp("{}")
    await client.chat_json("classify this", system="be terse")
    args = client.client.chat.completions.create.call_args.kwargs
    assert args["messages"][0]["content"] == "be terse"
    assert "seed" not in args


async def test_chat_json_invalid_raises(client: LiteLLMClient) -> None:
    client.client.chat.completions.create.return_value = _resp("not-json")
    with pytest.raises(ValueError, match="invalid JSON"):
        await client.chat_json("classify")


async def test_vision_encodes_image_as_data_url(client: LiteLLMClient) -> None:
    client.client.chat.completions.create.return_value = _resp("describes the image")
    img = b"\x89PNG\r\n\x1a\nFAKE"
    out = await client.vision("what is this", images=[img])
    assert out == "describes the image"
    msgs = client.client.chat.completions.create.call_args.kwargs["messages"]
    assert msgs[0]["role"] == "user"
    parts = msgs[0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this"}
    expected_url = f"data:image/png;base64,{base64.b64encode(img).decode('ascii')}"
    assert parts[1] == {"type": "image_url", "image_url": {"url": expected_url}}


async def test_chat_retries_on_transient(client: LiteLLMClient) -> None:
    err = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    client.client.chat.completions.create.side_effect = [err, err, _resp("ok")]
    out = await client.chat("hi")
    assert out == "ok"
    assert client.client.chat.completions.create.await_count == 3


async def test_chat_does_not_retry_on_permanent(client: LiteLLMClient) -> None:
    client.client.chat.completions.create.side_effect = ValueError("400 bad request")
    with pytest.raises(ValueError):
        await client.chat("hi")
    assert client.client.chat.completions.create.await_count == 1


def _api_status_error(code: int) -> openai.APIStatusError:
    response = httpx.Response(code, request=httpx.Request("POST", "http://x"))
    return openai.APIStatusError(message=f"{code}", response=response, body=None)


def test_is_transient_classifier() -> None:
    assert _is_transient(
        openai.RateLimitError(
            "rate",
            response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
            body=None,
        )
    )
    assert _is_transient(openai.APITimeoutError(request=httpx.Request("POST", "http://x")))
    assert _is_transient(openai.APIConnectionError(request=httpx.Request("POST", "http://x")))
    assert _is_transient(_api_status_error(503))
    assert _is_transient(_api_status_error(504))
    assert not _is_transient(_api_status_error(400))
    assert not _is_transient(ValueError("not an OpenAI error"))


def test_sniff_mime_detects_jpeg() -> None:
    assert _sniff_mime(b"\xff\xd8\xff\xe0FAKE") == "image/jpeg"


def test_sniff_mime_detects_png() -> None:
    assert _sniff_mime(b"\x89PNG\r\n\x1a\nFAKE") == "image/png"


def test_sniff_mime_detects_webp() -> None:
    assert _sniff_mime(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"FAKE") == "image/webp"


def test_sniff_mime_falls_back_to_png() -> None:
    assert _sniff_mime(b"unknown bytes") == "image/png"


async def test_vision_uses_sniffed_mime_for_jpeg(client: LiteLLMClient) -> None:
    client.client.chat.completions.create.return_value = _resp("ok")
    img = b"\xff\xd8\xffFAKE"
    await client.vision("what is this", images=[img])
    parts = client.client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize(
    "timeout, expected",
    [
        (None, {"connect": 10.0, "read": 600.0, "write": 600.0, "pool": 600.0}),
        (
            httpx.Timeout(connect=1.0, read=2.0, write=3.0, pool=4.0),
            {"connect": 1.0, "read": 2.0, "write": 3.0, "pool": 4.0},
        ),
        (httpx.Timeout(None), {"connect": None, "read": None, "write": None, "pool": None}),
    ],
)
async def test_sdk_transport_preserves_timeout_values(
    timeout: httpx.Timeout | None, expected: dict[str, float | None]
) -> None:
    client = LiteLLMClient(
        base_url="http://127.0.0.1:1", api_key="test", model="test", timeout=timeout
    )
    try:
        request = client.client._client.build_request("GET", "http://127.0.0.1:1")
        assert request.extensions["timeout"] == expected
    finally:
        await client.client.close()
