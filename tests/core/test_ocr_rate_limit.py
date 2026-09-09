"""The OCR path under a requests-per-minute ceiling.

A 429 from the Foundry deployment used to be indistinguishable from a 400 and
was not retried, so the caller fell through to the chat gateway carrying an
OCR-only model name and burned one 400 per page, silently.
"""

import asyncio
import time

import httpx
import pytest

from codify.core.llm import LiteLLMClient, OcrStatusError, RateLimiter, _is_transient
from codify.pipeline.enrich.ocr import _vision_model


def test_rate_limiter_holds_callers_to_the_ceiling() -> None:
    """The 21st call in a 20-per-minute window waits for the window to roll."""

    async def run() -> float:
        limiter = RateLimiter(per_minute=20)
        for _ in range(20):
            await limiter.acquire()
        started = time.monotonic()
        # No slot left, so this one cannot return promptly.
        try:
            await asyncio.wait_for(limiter.acquire(), timeout=0.3)
        except TimeoutError:
            return time.monotonic() - started
        return -1.0

    assert asyncio.run(run()) > 0.0


def test_rate_limiter_lets_the_allowance_through_without_waiting() -> None:
    async def run() -> float:
        limiter = RateLimiter(per_minute=40)
        started = time.monotonic()
        for _ in range(40):
            await limiter.acquire()
        return time.monotonic() - started

    assert asyncio.run(run()) < 1.0


def test_rate_limiter_never_admits_more_than_the_ceiling_under_contention() -> None:
    """Concurrent callers share the allowance rather than each getting one."""

    async def run() -> int:
        limiter = RateLimiter(per_minute=5)
        admitted = 0

        async def one() -> None:
            nonlocal admitted
            await limiter.acquire()
            admitted += 1

        tasks = [asyncio.create_task(one()) for _ in range(12)]
        await asyncio.sleep(0.2)
        for t in tasks:
            t.cancel()
        return admitted

    assert asyncio.run(run()) == 5


@pytest.mark.parametrize(
    ("status", "retriable"),
    [(429, True), (500, True), (503, True), (400, False), (401, False), (404, False)],
)
def test_only_a_wait_is_worth_retrying(status: int, retriable: bool) -> None:
    """400 means the request is wrong; retrying it just repeats the mistake."""
    assert _is_transient(OcrStatusError(status)) is retriable


def test_ocr_status_error_carries_retry_after() -> None:
    assert OcrStatusError(429, retry_after=7.0).retry_after == 7.0


# The legacy deployment is no longer routable, but the guard still covers its
# name: a hand-set env var must not reach the chat gateway either.
@pytest.mark.parametrize("model", ["mistral-ocr-4-0", "mistral-document-ai-2512"])
def test_ocr_only_models_never_reach_the_chat_vision_route(model: str) -> None:
    """This is the 400 storm: an OCR deployment cannot serve chat completions."""
    assert _vision_model(model) is None


def test_a_real_vision_model_still_travels_to_the_page_loop() -> None:
    assert _vision_model("gemini-3.6-flash") == "gemini-3.6-flash"
    assert _vision_model(None) is None


def test_a_rate_limited_ocr_call_retries_into_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: a 429 costs a wait, not the document."""

    attempts = 0

    class _Response:
        def __init__(self, status: int) -> None:
            self.status_code = status
            self.headers = {"retry-after": "1"} if status == 429 else {}
            self.text = "rate limited" if status == 429 else ""

        def json(self) -> dict[str, object]:
            return {"pages": [{"index": 0, "markdown": "hello"}]}

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> _Response:
            nonlocal attempts
            attempts += 1
            return _Response(429 if attempts == 1 else 200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())
    client = LiteLLMClient(
        base_url="http://x/v1",
        api_key="k",
        model="m",
        azure_foundry_ocr_url="http://foundry/ocr",
        azure_foundry_ocr_key="key",
    )
    pages = asyncio.run(client.ocr(pdf=b"%PDF-1.4", model="mistral-ocr-4-0"))
    assert attempts == 2
    assert pages == [{"index": 0, "markdown": "hello"}]


def test_a_bad_request_fails_fast_instead_of_retrying(monkeypatch: pytest.MonkeyPatch) -> None:

    attempts = 0

    class _Response:
        status_code = 400
        headers: dict[str, str] = {}
        # A real body quotes the submitted page back at us.
        text = "unsupported: مادة (٧٩) على جميع الجهات المختصة"

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> _Response:
            nonlocal attempts
            attempts += 1
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())
    client = LiteLLMClient(
        base_url="http://x/v1",
        api_key="k",
        model="m",
        azure_foundry_ocr_url="http://foundry/ocr",
        azure_foundry_ocr_key="key",
    )
    with pytest.raises(OcrStatusError) as caught:
        asyncio.run(client.ocr(pdf=b"%PDF-1.4", model="mistral-ocr-4-0"))
    assert caught.value.status_code == 400
    assert attempts == 1
    # Both OCR fallback handlers log only the exception representation.
    assert "مادة" not in str(caught.value)


def _stub_client(monkeypatch: pytest.MonkeyPatch, status: int) -> tuple[LiteLLMClient, list[int]]:
    calls: list[int] = []

    class _Response:
        status_code = status
        headers = {"retry-after": "1"} if status == 429 else {}
        text = "rate limited"

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> _Response:
            calls.append(1)
            return _Response()

    real_sleep = asyncio.sleep
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())
    # Skip the backoff waits; this is about how many attempts happen, not when.
    monkeypatch.setattr(asyncio, "sleep", lambda _: real_sleep(0))
    return (
        LiteLLMClient(
            base_url="http://x/v1",
            api_key="k",
            model="m",
            azure_foundry_ocr_url="http://foundry/ocr",
            azure_foundry_ocr_key="key",
        ),
        calls,
    )


def test_a_sustained_rate_limit_stops_at_the_attempt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two stacked retry decorators quietly multiplied into 18 attempts."""
    client, calls = _stub_client(monkeypatch, 429)
    with pytest.raises(OcrStatusError):
        asyncio.run(client.ocr(pdf=b"%PDF-1.4", model="mistral-ocr-4-0"))
    assert len(calls) == 6


def test_the_caller_still_learns_the_status_after_the_retries_run_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapping in a marker type threw away the one field worth alerting on."""
    client, _ = _stub_client(monkeypatch, 429)
    with pytest.raises(OcrStatusError) as caught:
        asyncio.run(client.ocr(pdf=b"%PDF-1.4", model="mistral-ocr-4-0"))
    assert caught.value.status_code == 429


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("t"),
        httpx.ReadTimeout("t"),
        httpx.ConnectError("c"),
        httpx.RemoteProtocolError("p"),
    ],
)
def test_transport_failures_on_the_direct_route_are_worth_retrying(exc: Exception) -> None:
    """The OCR route is raw httpx, so none of the openai types ever reach it."""
    assert _is_transient(exc) is True


def test_a_limiter_that_would_admit_nothing_is_rejected() -> None:
    """Zero reads as "no limit" and used to clamp to one call a minute."""
    with pytest.raises(ValueError):
        RateLimiter(per_minute=0)
