"""A quota refusal is retried past the minute boundary; other faults are not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import openai
import pytest

from codify.embed.client import (
    EmbeddingClient,
    EmbeddingError,
    EmbeddingRateLimited,
    _stop_by_fault_class,
    _wait_past_the_quota_window,
)


def _state(exc: BaseException | None, attempt: int) -> SimpleNamespace:
    outcome = None if exc is None else SimpleNamespace(exception=lambda: exc, failed=True)
    return SimpleNamespace(outcome=outcome, attempt_number=attempt, idle_for=0.0)


def test_quota_refusal_waits_into_the_next_minute() -> None:
    waits = [
        _wait_past_the_quota_window(_state(EmbeddingRateLimited("q"), n))  # type: ignore[arg-type]
        for n in (1, 2, 3, 4)
    ]
    assert waits == [20.0, 40.0, 60.0, 65.0]


def test_quota_refusal_honours_a_longer_retry_after() -> None:
    wait = _wait_past_the_quota_window(_state(EmbeddingRateLimited("q", retry_after=50), 1))  # type: ignore[arg-type]
    assert wait == 50.0


def test_burst_fault_keeps_the_short_wait() -> None:
    wait = _wait_past_the_quota_window(_state(EmbeddingError("x"), 1))  # type: ignore[arg-type]
    assert wait <= 30.0


def test_quota_refusal_gets_six_attempts_and_a_burst_fault_three() -> None:
    assert not _stop_by_fault_class(_state(EmbeddingRateLimited("q"), 5))  # type: ignore[arg-type]
    assert _stop_by_fault_class(_state(EmbeddingRateLimited("q"), 6))  # type: ignore[arg-type]
    assert not _stop_by_fault_class(_state(EmbeddingError("x"), 2))  # type: ignore[arg-type]
    assert _stop_by_fault_class(_state(EmbeddingError("x"), 3))  # type: ignore[arg-type]


def _rate_limit(retry_after: str | None) -> openai.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(
        429, headers=headers, request=httpx.Request("POST", "https://example.invalid/v1")
    )
    return openai.RateLimitError("quota", response=response, body=None)


async def test_call_raises_the_quota_class_with_the_providers_hint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = EmbeddingClient.__new__(EmbeddingClient)
    client.model = "m"
    client.dimensions = 8

    async def refuse(**_: object) -> None:
        raise _rate_limit("7")

    client.client = SimpleNamespace(embeddings=SimpleNamespace(create=refuse))
    monkeypatch.setattr("codify.embed.client._stop_by_fault_class", lambda s: True)
    with pytest.raises(EmbeddingRateLimited) as info:
        await EmbeddingClient._call.__wrapped__(client, ["x"])  # type: ignore[attr-defined]
    assert info.value.retry_after == 7.0


async def test_call_reads_an_http_date_retry_after(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = EmbeddingClient.__new__(EmbeddingClient)
    client.model = "m"
    client.dimensions = 8
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=40), usegmt=True)

    async def refuse(**_: object) -> None:
        raise _rate_limit(when)

    client.client = SimpleNamespace(embeddings=SimpleNamespace(create=refuse))
    with pytest.raises(EmbeddingRateLimited) as info:
        await EmbeddingClient._call.__wrapped__(client, ["x"])  # type: ignore[attr-defined]
    assert info.value.retry_after is not None and 35.0 <= info.value.retry_after <= 40.0


def test_a_zoneless_date_is_not_a_hint() -> None:
    from codify.core.retry_after import parse_retry_after

    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
