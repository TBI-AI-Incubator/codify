"""Unit tests for the gateway-backed EmbeddingClient. AsyncOpenAI mocked."""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openai
import pytest

from codify.embed.client import EmbeddingClient, EmbeddingError


def _client() -> EmbeddingClient:
    return EmbeddingClient(base_url="http://fake", api_key="fake", model="m", dimensions=4)


def _resp(*vectors: list[float]) -> SimpleNamespace:
    return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])


@pytest.mark.asyncio
async def test_vectors_are_l2_normalised() -> None:
    c = _client()
    c.client.embeddings.create = AsyncMock(return_value=_resp([3.0, 4.0, 0.0, 0.0]))
    [vec] = await c.embed(["x"])
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-9)


@pytest.mark.asyncio
async def test_zero_vector_raises() -> None:
    c = _client()
    c.client.embeddings.create = AsyncMock(return_value=_resp([0.0, 0.0, 0.0, 0.0]))
    with pytest.raises(EmbeddingError):
        await c.embed(["x"])


@pytest.mark.asyncio
async def test_length_error_triggers_truncation_retry() -> None:
    c = _client()
    long_text = "word " * 10_000
    err = openai.BadRequestError(
        message="input is too long: token count exceeds maximum context length",
        response=SimpleNamespace(status_code=400, request=None, headers={}),  # type: ignore[arg-type]
        body=None,
    )
    calls: list[int] = []

    async def fake_create(*, model, input, dimensions):  # noqa: ARG001
        calls.append(len(input[0]))
        if len(input[0]) > 5_000:
            raise err
        return _resp([1.0, 0.0, 0.0, 0.0])

    c.client.embeddings.create = fake_create  # type: ignore[assignment]
    [vec] = await c.embed([long_text])
    # First call sent the full string; later calls shrank it under the limit.
    assert calls[0] > 5_000
    assert calls[-1] <= 5_000
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-9)


@pytest.mark.asyncio
async def test_non_length_bad_request_does_not_retry() -> None:
    c = _client()
    err = openai.BadRequestError(
        message="model not found",
        response=SimpleNamespace(status_code=400, request=None, headers={}),  # type: ignore[arg-type]
        body=None,
    )
    create = AsyncMock(side_effect=err)
    c.client.embeddings.create = create
    with pytest.raises(EmbeddingError):
        await c.embed(["x"])
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_embed_documents_prepends_title() -> None:
    c = _client()
    seen: list[str] = []

    async def fake_create(*, model, input, dimensions):  # noqa: ARG001
        seen.extend(input)
        return _resp(*([1.0, 0.0, 0.0, 0.0] for _ in input))

    c.client.embeddings.create = fake_create  # type: ignore[assignment]
    await c.embed_documents([("Title", "body text")])
    assert seen == ["Title\n\nbody text"]
