"""PoliteTransport, rate-limit, robots, etag injection, retries and host circuits."""

from __future__ import annotations

import time

import httpx
import pytest

from codify.acquisition import politeness
from codify.acquisition.base import PolitenessProfile
from codify.acquisition.politeness import (
    _CIRCUIT_COOLDOWN_S,
    _CIRCUIT_FAILURES,
    _MAX_RETRIES,
    _ROBOTS_FAIL_TTL_S,
    _ROBOTS_OK_TTL_S,
    HostCircuitOpen,
    PoliteTransport,
    host_bucket,
    host_bucket_state,
    host_circuit,
    host_circuit_state,
    reset_host_buckets,
)


@pytest.fixture(autouse=True)
def _fresh_buckets() -> None:
    # The pace registry is process-wide; a test must not inherit another's interval.
    reset_host_buckets()


def _ok(text: str = "ok", **headers: str) -> httpx.Response:
    return httpx.Response(200, text=text, headers=headers)


def _make_inner(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


@pytest.mark.asyncio

# The transport pins the address it validated, so a stubbed inner transport
# still needs a resolver: these hosts are fictional and would otherwise fail
# closed. A public answer keeps the guard satisfied without weakening it.
def _public(_host: str) -> list[str]:
    return ["93.184.216.34"]


async def test_rate_limit_throttles_consecutive_requests() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        calls += 1
        return _ok()

    inner = _make_inner(handler)
    profile = PolitenessProfile(rate_limit_per_minute=120, robots_respect=False)
    transport = PoliteTransport(profile=profile, inner=inner, resolver=_public)

    async with httpx.AsyncClient(transport=transport) as c:
        t0 = time.monotonic()
        await c.get("https://example.test/a")
        await c.get("https://example.test/b")
        elapsed = time.monotonic() - t0
    assert calls == 2
    # 120/min → 0.5s min interval; 1st request / 0 wait, 2nd / >=0.5s.
    assert elapsed >= 0.45


@pytest.mark.asyncio
async def test_robots_blocks_disallowed_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return _ok()

    inner = _make_inner(handler)
    profile = PolitenessProfile(robots_respect=True)
    transport = PoliteTransport(profile=profile, inner=inner, resolver=_public)

    async with httpx.AsyncClient(transport=transport) as c:
        blocked = await c.get("https://example.test/private/x")
        ok = await c.get("https://example.test/public/x")
    assert blocked.status_code == 451
    assert blocked.headers.get("x-codify-blocked-by") == "robots.txt"
    assert blocked.reason_phrase == "Blocked by robots.txt"
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_etag_injection() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        captured["if-none-match"] = request.headers.get("if-none-match", "")
        captured["if-modified-since"] = request.headers.get("if-modified-since", "")
        return httpx.Response(304)

    async def lookup(url: str) -> tuple[str | None, str | None]:
        return ('W/"abc123"', "Wed, 06 May 2026 12:00:00 GMT")

    inner = _make_inner(handler)
    profile = PolitenessProfile(robots_respect=False)
    transport = PoliteTransport(profile=profile, etag_lookup=lookup, inner=inner, resolver=_public)

    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://example.test/x")
    assert resp.status_code == 304
    assert captured["if-none-match"] == 'W/"abc123"'
    assert "May 2026" in captured["if-modified-since"]


@pytest.mark.asyncio
async def test_retries_on_503_then_succeeds() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        attempts += 1
        if attempts < 2:
            return httpx.Response(503, headers={"retry-after": "0"})
        return _ok()

    inner = _make_inner(handler)
    profile = PolitenessProfile(robots_respect=False)
    transport = PoliteTransport(profile=profile, inner=inner, resolver=_public)

    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://example.test/x")
    assert resp.status_code == 200
    assert attempts == 2


@pytest.mark.asyncio
async def test_user_agent_default_applied() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        captured["ua"] = request.headers.get("user-agent", "")
        return _ok()

    inner = _make_inner(handler)
    profile = PolitenessProfile(robots_respect=False, user_agent="codify-test/1.0")
    transport = PoliteTransport(profile=profile, inner=inner, resolver=_public)
    async with httpx.AsyncClient(transport=transport) as c:
        await c.get("https://example.test/x")
    assert captured["ua"] == "codify-test/1.0"


@pytest.mark.asyncio
async def test_default_user_agent_carries_a_contact_on_every_request() -> None:
    """A publisher reading its logs can reach us; the robots probe says so too."""
    agents: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        agents[request.url.path] = request.headers.get("user-agent", "")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return _ok()

    transport = PoliteTransport(
        profile=PolitenessProfile(), inner=_make_inner(handler), resolver=_public
    )
    async with httpx.AsyncClient(transport=transport) as c:
        await c.get("https://example.test/x")
    assert set(agents) == {"/robots.txt", "/x"}
    for agent in agents.values():
        assert agent.startswith("codify-acquire/") and "(+https://" in agent


# ---- REV-1 fixes -------------------------------------------------------------


@pytest.mark.asyncio
async def test_robots_check_does_not_close_inner_transport() -> None:
    """Item 1: 100 sequential calls survive without inner transport teardown."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        calls += 1
        return _ok()

    inner = _make_inner(handler)
    profile = PolitenessProfile(robots_respect=True)
    transport = PoliteTransport(profile=profile, inner=inner, resolver=_public)
    async with httpx.AsyncClient(transport=transport) as c:
        for _ in range(100):
            resp = await c.get("https://example.test/x")
            assert resp.status_code == 200
    assert calls == 100


@pytest.mark.asyncio
async def test_robots_503_default_denies_with_short_ttl(capsys) -> None:
    """Item 3: 5xx → default-deny + log + short TTL."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(503)
        return _ok()

    inner = _make_inner(handler)
    profile = PolitenessProfile(robots_respect=True)
    transport = PoliteTransport(profile=profile, inner=inner, resolver=_public)

    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://example.test/x")
    assert resp.status_code == 451
    assert resp.headers.get("x-codify-blocked-by") == "robots.txt-unavailable"
    cached = transport._robots.entries.get("example.test")
    assert cached is not None
    assert cached.fetch_failed is True
    assert cached.ttl_s == _ROBOTS_FAIL_TTL_S
    assert "robots_fetch_failed" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_robots_429_is_named_as_unavailable_not_disallowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(429)
        return _ok()

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=True), inner=_make_inner(handler), resolver=_public
    )
    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://example.org/x")
    assert resp.status_code == 451
    assert resp.headers["x-codify-blocked-by"] == "robots.txt-unavailable"
    assert resp.reason_phrase == "robots.txt unavailable (HTTP 429)"
    assert resp.text == (
        "robots.txt for example.org could not be read (HTTP 429); "
        "requests to example.org are held for up to 5 minutes"
    )
    with pytest.raises(httpx.HTTPStatusError, match="robots.txt unavailable"):
        resp.raise_for_status()


@pytest.mark.asyncio
async def test_robots_unavailable_is_named_on_a_non_default_port() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(429)
        return _ok()

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=True), inner=_make_inner(handler), resolver=_public
    )
    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://example.org:8443/x")
    assert resp.headers["x-codify-blocked-by"] == "robots.txt-unavailable"


@pytest.mark.asyncio
async def test_robots_transport_error_is_named_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("refused")
        return _ok()

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=True), inner=_make_inner(handler), resolver=_public
    )
    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://example.org/x")
    assert resp.headers["x-codify-blocked-by"] == "robots.txt-unavailable"
    assert resp.reason_phrase == "robots.txt unavailable (ConnectError)"
    assert "ConnectError" in resp.text


@pytest.mark.asyncio
async def test_robots_404_caches_as_allow_with_long_ttl() -> None:
    """Item 3: 404 robots = allow-all per RFC, success TTL."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return _ok()

    inner = _make_inner(handler)
    profile = PolitenessProfile(robots_respect=True)
    transport = PoliteTransport(profile=profile, inner=inner, resolver=_public)
    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://example.test/x")
    assert resp.status_code == 200
    cached = transport._robots.entries.get("example.test")
    assert cached is not None
    assert cached.fetch_failed is False
    assert cached.ttl_s == _ROBOTS_OK_TTL_S


@pytest.mark.asyncio
async def test_retry_exhaustion_logs_and_returns_readable_response(capsys) -> None:
    """Item 4: after _MAX_RETRIES of 503, log once + return body-readable response."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        attempts += 1
        return httpx.Response(503, text="server is sad", headers={"retry-after": "0"})

    inner = _make_inner(handler)
    profile = PolitenessProfile(robots_respect=False)
    transport = PoliteTransport(profile=profile, inner=inner, resolver=_public)

    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://example.test/x")
    assert resp.status_code == 503
    # Body still readable (not aclose()'d).
    assert resp.text == "server is sad"
    assert attempts == _MAX_RETRIES
    out = capsys.readouterr().out
    assert out.count("polite_retry_exhausted") == 1


@pytest.mark.asyncio
async def test_the_robots_probe_takes_its_turn_in_the_bucket() -> None:
    """A fresh client's first fetch is two requests to the host; the second
    waits the interval behind the probe rather than following it at once."""
    stamps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        stamps.append(time.monotonic())
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return _ok()

    profile = PolitenessProfile(rate_limit_per_minute=120, robots_respect=True)
    transport = PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)
    async with httpx.AsyncClient(transport=transport) as c:
        await c.get("https://example.test/a")
    assert len(stamps) == 2
    assert stamps[1] - stamps[0] >= 0.45


# --- process-wide adaptive bucket -------------------------------------------------


def _allow_all(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")
    return _ok()


def _transport(handler, rate: int = 120) -> PoliteTransport:  # type: ignore[no-untyped-def]
    profile = PolitenessProfile(rate_limit_per_minute=rate, robots_respect=False)
    return PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)


@pytest.mark.asyncio
async def test_two_transports_share_one_host_pace() -> None:
    """A second client in the same process waits behind the first's request."""
    stamps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        stamps.append(time.monotonic())
        return _allow_all(request)

    async with (
        httpx.AsyncClient(transport=_transport(handler)) as a,
        httpx.AsyncClient(transport=_transport(handler)) as b,
    ):
        await a.get("https://shared.test/a")
        await b.get("https://shared.test/b")
    assert len(stamps) == 2
    assert stamps[1] - stamps[0] >= 0.45


@pytest.mark.asyncio
async def test_different_hosts_do_not_share_a_pace() -> None:
    stamps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        stamps.append(time.monotonic())
        return _allow_all(request)

    async with httpx.AsyncClient(transport=_transport(handler)) as c:
        await c.get("https://one.test/a")
        await c.get("https://two.test/b")
    assert stamps[1] - stamps[0] < 0.3


@pytest.mark.asyncio
async def test_a_refusal_widens_the_interval_and_success_narrows_it_back() -> None:
    from structlog.testing import capture_logs

    seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        if seen == 1:
            return httpx.Response(429, headers={"retry-after": "0.6"})
        return _allow_all(request)

    transport = _transport(handler, rate=600)  # 0.1 s floor
    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=transport) as c:
            t0 = time.monotonic()
            resp = await c.get("https://backoff.test/x")
            first = time.monotonic() - t0
    assert resp.status_code == 200 and seen == 2
    # The retry waited the Retry-After, not the floor.
    assert first >= 0.55
    bucket = host_bucket("backoff.test", 0.1)
    assert bucket.backoffs == 1
    # One answered request after the refusal: 0.6 * 0.95.
    assert 0.56 <= bucket.interval_s <= 0.58
    backoff = [e for e in logs if e["event"] == "polite_backoff"]
    assert backoff and backoff[0]["status"] == 429 and backoff[0]["interval_s"] == 0.6

    async with httpx.AsyncClient(transport=transport) as c:
        for _ in range(3):
            await c.get("https://backoff.test/y")
    assert bucket.interval_s < 0.5
    assert bucket.interval_s >= bucket.floor_s


@pytest.mark.asyncio
async def test_a_bare_refusal_doubles_the_interval_and_436_counts() -> None:
    seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        return httpx.Response(436) if seen == 1 else _allow_all(request)

    async with httpx.AsyncClient(transport=_transport(handler, rate=600)) as c:
        t0 = time.monotonic()
        assert (await c.get("https://double.test/x")).status_code == 200
        elapsed = time.monotonic() - t0
    # 0.1 s floor doubled plus the half-second step: 0.7 s before the retry.
    assert elapsed >= 0.65
    assert host_bucket("double.test", 0.1).backoffs == 1


def test_the_strictest_configured_floor_wins_for_a_host() -> None:
    bucket = host_bucket("floors.test", 0.5)
    assert host_bucket("floors.test", 0.2) is bucket and bucket.floor_s == 0.5
    assert host_bucket("floors.test", 2.0).floor_s == 2.0
    assert bucket.interval_s == 2.0


def test_retry_after_accepts_seconds_and_http_dates() -> None:
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    from codify.acquisition.politeness import _parse_retry_after

    assert _parse_retry_after("7") == 7.0
    assert _parse_retry_after("-3") == 0.0
    soon = format_datetime(datetime.now(UTC) + timedelta(seconds=30))
    parsed = _parse_retry_after(soon)
    assert parsed is not None and 25 <= parsed <= 30
    assert _parse_retry_after("not a date") is None


@pytest.mark.asyncio
async def test_a_refusal_during_another_waiter_sleep_delays_that_waiter() -> None:
    """Two fetch slots: the second is asleep until the interval ends when the
    first draws a 429; it must wait the widened interval, not the old deadline."""
    import asyncio

    bucket = host_bucket("late.test", 0.2)
    bucket.last_request_at = time.monotonic()
    t0 = time.monotonic()

    async def waiter() -> float:
        await bucket.acquire()
        return time.monotonic() - t0

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    bucket.record_backoff(1.0)  # a refusal on another slot: the deadline moves out
    elapsed = await task
    assert elapsed >= 0.95


@pytest.fixture
def no_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    # Full jitter draws from [0, 2**attempt); zero keeps the retries instant.
    monkeypatch.setattr(politeness.random, "uniform", lambda _a, _b: 0.0)


def _quiet_transport(handler) -> PoliteTransport:  # type: ignore[no-untyped-def]
    profile = PolitenessProfile(robots_respect=False)
    return PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 504])
async def test_a_gateway_failure_is_retried_without_widening_the_pace(
    status: int, no_jitter: None
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status) if attempts < 3 else _ok()

    profile = PolitenessProfile(rate_limit_per_minute=6000, robots_respect=False)
    transport = PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)
    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://gateway.test/x")
    assert resp.status_code == 200
    assert attempts == 3
    state = host_bucket_state("gateway.test")
    assert state is not None and state["backoffs"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
async def test_a_transient_transport_error_on_a_get_is_retried(
    error: type[httpx.TransportError], no_jitter: None
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error("dropped", request=request)
        return _ok()

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        resp = await c.get("https://flaky.test/x")
    assert resp.status_code == 200
    assert attempts == 2


@pytest.mark.asyncio
async def test_a_request_with_a_body_is_not_replayed_after_a_transport_error(
    no_jitter: None,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("dropped", request=request)

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        with pytest.raises(httpx.ReadTimeout):
            await c.post("https://flaky.test/sparql", content=b"query=1")
    assert attempts == 1


@pytest.mark.asyncio
async def test_retries_exhausted_on_a_transport_error_raise_it(no_jitter: None) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("no answer", request=request)

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        with pytest.raises(httpx.ConnectTimeout):
            await c.get("https://down.test/x")
    assert attempts == _MAX_RETRIES
    # One request, however many attempts, is one failure.
    assert host_circuit_state("down.test") == {"failures": 1, "open": False, "trips": 0}


@pytest.mark.asyncio
async def test_consecutive_failed_requests_open_the_circuit() -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(503, headers={"retry-after": "0"})

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        for n in range(_CIRCUIT_FAILURES):
            assert (await c.get(f"https://down.test/{n}")).status_code == 503
        assert sent == _CIRCUIT_FAILURES * _MAX_RETRIES
        with pytest.raises(HostCircuitOpen):
            await c.get("https://down.test/next")
    # Refused unsent, and another host keeps its own circuit.
    assert sent == _CIRCUIT_FAILURES * _MAX_RETRIES
    assert host_circuit_state("other.test") is None


@pytest.mark.asyncio
async def test_an_answer_resets_the_failure_count(no_jitter: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/missing":
            return httpx.Response(404)
        return httpx.Response(502)

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        for _ in range(_CIRCUIT_FAILURES - 1):
            await c.get("https://half.test/x")
        # A 404 is the host answering: it is up, whatever the document's fate.
        assert (await c.get("https://half.test/missing")).status_code == 404
        await c.get("https://half.test/x")
    assert host_circuit_state("half.test") == {"failures": 1, "open": False, "trips": 0}


@pytest.mark.asyncio
async def test_after_the_cooldown_one_probe_closes_or_reopens_the_circuit(
    no_jitter: None,
) -> None:
    healthy = False

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok() if healthy else httpx.Response(502)

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        for _ in range(_CIRCUIT_FAILURES):
            await c.get("https://probe.test/x")
        circuit = host_circuit("probe.test")
        assert circuit.opened_at is not None

        # A failed probe reopens it at once, without another full run of failures.
        circuit.opened_at -= _CIRCUIT_COOLDOWN_S
        assert (await c.get("https://probe.test/x")).status_code == 502
        assert circuit.trips == 2
        with pytest.raises(HostCircuitOpen):
            await c.get("https://probe.test/x")

        healthy = True
        circuit.opened_at -= _CIRCUIT_COOLDOWN_S
        assert (await c.get("https://probe.test/x")).status_code == 200
        assert (await c.get("https://probe.test/y")).status_code == 200
    assert host_circuit_state("probe.test") == {"failures": 0, "open": False, "trips": 2}


def test_only_one_probe_is_admitted_while_the_circuit_is_half_open() -> None:
    circuit = host_circuit("busy.test")
    for _ in range(_CIRCUIT_FAILURES):
        circuit.record_failure()
    assert circuit.admit() is False
    assert circuit.opened_at is not None
    circuit.opened_at -= _CIRCUIT_COOLDOWN_S
    assert circuit.admit() is True
    assert circuit.admit() is False
    # A probe that ends without an outcome (robots refusal, cancellation) frees the slot.
    circuit.release()
    assert circuit.admit() is True


@pytest.mark.asyncio
async def test_a_robots_refusal_neither_counts_nor_clears_a_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return _ok()

    profile = PolitenessProfile(robots_respect=True)
    transport = PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)
    host_circuit("robots.test").record_failure()
    async with httpx.AsyncClient(transport=transport) as c:
        assert (await c.get("https://robots.test/private/x")).status_code == 451
    assert host_circuit_state("robots.test") == {"failures": 1, "open": False, "trips": 0}
