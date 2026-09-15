"""Provider-agnostic embedding client over an OpenAI-compatible endpoint.

Any Matryoshka embedding model exposed by the LiteLLM gateway works:
swap the model + dimensions in config, no code change. Vectors are
L2-normalised so reduced-dimension outputs are safe for both cosine
search and the comparator's dot-product cache.

Over-length inputs are handled adaptively: the full text is sent first,
and only on a context-length error is the batch shrunk and retried, so
there's no per-model character budget to keep in sync with tokenisers.
"""

from __future__ import annotations

import asyncio
import math
from typing import Literal

import openai
import structlog
from openai import AsyncOpenAI
from tenacity import RetryCallState, retry, retry_if_exception_type, wait_exponential

from codify.core.retry_after import parse_retry_after
from codify.core.tracing import direct_observation, record_usage
from codify.settings import EMBEDDING_BATCH_SIZE

logger = structlog.get_logger()

TaskType = Literal["document", "query"]

# Floor for adaptive truncation, any provision this short fits any model.
_MIN_CHARS = 500
# Non-empty stand-in for blank inputs; providers 400 on empty content.
_EMPTY_PLACEHOLDER = "(empty)"
# Substrings that mark a context-length rejection across providers.
_LENGTH_HINTS = ("token", "too long", "too large", "maximum context", "exceed", "input length")


class EmbeddingError(RuntimeError):
    """Raised when embedding generation fails (after retries)."""


class EmbeddingRateLimited(EmbeddingError):
    """The provider refused for quota; `retry_after` is its own hint in seconds, if given."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


_burst_wait = wait_exponential(min=1, max=30)


def _wait_past_the_quota_window(retry_state: RetryCallState) -> float:
    """A quota refusal lasts the rest of the minute; a burst fault does not."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, EmbeddingRateLimited):
        return min(65.0, max(exc.retry_after or 0.0, 20.0 * retry_state.attempt_number))
    return float(_burst_wait(retry_state))


def _stop_by_fault_class(retry_state: RetryCallState) -> bool:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    limit = 6 if isinstance(exc, EmbeddingRateLimited) else 3
    return retry_state.attempt_number >= limit


def _retry_after_seconds(exc: openai.OpenAIError) -> float | None:
    response = getattr(exc, "response", None)
    return parse_retry_after(response.headers.get("retry-after")) if response is not None else None


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        raise EmbeddingError("model returned a zero vector")
    return [x / norm for x in vec]


def _is_length_error(exc: openai.BadRequestError) -> bool:
    msg = str(exc).lower()
    return any(h in msg for h in _LENGTH_HINTS)


class EmbeddingClient:
    """Async batch-embedder over an OpenAI-compatible `/embeddings` route.

    ``model`` is the gateway alias (also the stored ``model_id``);
    ``dimensions`` requests a Matryoshka truncation to match the
    halfvec column width.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int = 768,
        concurrency: int = 5,
    ) -> None:
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.dimensions = dimensions
        self._sem = asyncio.Semaphore(concurrency)

    async def embed(self, texts: list[str], task: TaskType = "document") -> list[list[float]]:
        # Providers (e.g. Gemini) reject empty/whitespace inputs with a 400.
        # Substitute a placeholder so positional alignment is preserved and a
        # degenerate unit (empty container) still yields a vector.
        cleaned = [t if t and t.strip() else _EMPTY_PLACEHOLDER for t in texts]
        return await self._embed(cleaned)

    async def embed_one(self, text: str, task: TaskType = "document") -> list[float]:
        return (await self.embed([text], task=task))[0]

    async def embed_documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
        """Embed (title, text) pairs; the title is prepended for context."""
        texts = [f"{title}\n\n{text}" if title else text for title, text in items]
        return await self.embed(texts, "document")

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        batches = [
            texts[i : i + EMBEDDING_BATCH_SIZE] for i in range(0, len(texts), EMBEDDING_BATCH_SIZE)
        ]
        results = await asyncio.gather(*(self._embed_batch(b) for b in batches))
        return [vec for batch in results for vec in batch]

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        async with self._sem:
            budget: int | None = None
            while True:
                inputs = batch if budget is None else [t[:budget] for t in batch]
                try:
                    return await self._call(inputs)
                except openai.BadRequestError as exc:
                    if not _is_length_error(exc):
                        raise EmbeddingError(f"embeddings call failed: {exc}") from exc
                    budget = max(len(t) for t in inputs) // 2
                    if budget < _MIN_CHARS:
                        raise EmbeddingError(f"input too long even at floor: {exc}") from exc
                    logger.warning("embed_input_truncated", char_budget=budget)

    @retry(
        retry=retry_if_exception_type(EmbeddingError),
        stop=_stop_by_fault_class,
        wait=_wait_past_the_quota_window,
        reraise=True,
    )
    async def _call(self, inputs: list[str]) -> list[list[float]]:
        with direct_observation(
            "direct.embedding",
            as_type="embedding",
            model=self.model,
            metadata={
                "route": "embedding",
                "batch_size": len(inputs),
                "dimensions": self.dimensions,
            },
        ) as observation:
            try:
                resp = await self.client.embeddings.create(
                    model=self.model,
                    input=inputs,
                    dimensions=self.dimensions,
                )
            except openai.BadRequestError:
                raise  # length / malformed, handled by the caller, not retried
            except openai.RateLimitError as exc:
                logger.warning("embed_rate_limited", batch_size=len(inputs))
                raise EmbeddingRateLimited(
                    "embeddings call refused for quota", _retry_after_seconds(exc)
                ) from exc
            except openai.OpenAIError as exc:
                logger.warning(
                    "embed_call_failed", error_type=type(exc).__name__, batch_size=len(inputs)
                )
                raise EmbeddingError(f"embeddings call failed: {type(exc).__name__}") from exc
            usage = getattr(resp, "usage", None)
            if usage is not None:
                record_usage(
                    observation,
                    self.model,
                    {
                        "input": getattr(usage, "prompt_tokens", None),
                        "total": getattr(usage, "total_tokens", None),
                    },
                )
        return [_normalise(list(d.embedding)) for d in resp.data]


__all__ = ["EmbeddingClient", "EmbeddingError", "TaskType"]
