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
    UNBUFFERED,
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
async def test_consecutive_failed_requests_open_the_circuit(no_jitter: None) -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(502)

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        for n in range(_CIRCUIT_FAILURES - 1):
            assert (await c.get(f"https://down.test/{n}")).status_code == 502
        assert host_circuit_state("down.test") == {
            "failures": _CIRCUIT_FAILURES - 1,
            "open": False,
            "trips": 0,
        }
        assert (await c.get("https://down.test/last")).status_code == 502
        assert sent == _CIRCUIT_FAILURES * _MAX_RETRIES
        with pytest.raises(HostCircuitOpen):
            await c.get("https://down.test/next")
    # Refused unsent, and another host keeps its own circuit.
    assert sent == _CIRCUIT_FAILURES * _MAX_RETRIES
    assert host_circuit("down.test").cooldown_s == _CIRCUIT_COOLDOWN_S
    assert host_circuit_state("other.test") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 436, 503])
async def test_a_request_still_refused_after_its_backoffs_opens_the_circuit_at_once(
    status: int,
) -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(status, headers={"retry-after": "0"})

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        assert (await c.get("https://refuses.test/x")).status_code == status
        assert sent == _MAX_RETRIES
        assert host_circuit_state("refuses.test") == {"failures": 1, "open": True, "trips": 1}
        with pytest.raises(HostCircuitOpen):
            await c.get("https://refuses.test/y")
    assert sent == _MAX_RETRIES
    # A Retry-After shorter than the cooldown does not shorten it.
    assert host_circuit("refuses.test").cooldown_s == _CIRCUIT_COOLDOWN_S


@pytest.mark.asyncio
async def test_a_refusal_holds_the_circuit_for_its_longer_retry_after() -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        # Only the last refusal asks for longer, so the backoffs stay instant.
        wait = "300" if sent == _MAX_RETRIES else "0"
        return httpx.Response(429, headers={"retry-after": wait})

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        await c.get("https://slowdown.test/x")
        circuit = host_circuit("slowdown.test")
        assert circuit.cooldown_s == 300.0
        assert circuit.opened_at is not None
        circuit.opened_at -= _CIRCUIT_COOLDOWN_S
        with pytest.raises(HostCircuitOpen, match="300s"):
            await c.get("https://slowdown.test/x")
        circuit.opened_at -= 300.0 - _CIRCUIT_COOLDOWN_S
        assert circuit.admit() is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 451])
async def test_a_host_refusing_the_client_counts_as_a_failure(status: int) -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(status)

    profile = PolitenessProfile(rate_limit_per_minute=6000, robots_respect=False)
    transport = PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)
    bucket = host_bucket("forbids.test", 0.01)
    bucket.interval_s = 0.02
    async with httpx.AsyncClient(transport=transport) as c:
        for n in range(_CIRCUIT_FAILURES):
            assert (await c.get(f"https://forbids.test/{n}")).status_code == status
        with pytest.raises(HostCircuitOpen):
            await c.get("https://forbids.test/next")
    # Returned at once, never retried; and a refusal is no answer to recover the pace on.
    assert sent == _CIRCUIT_FAILURES
    assert bucket.interval_s == 0.02
    assert host_bucket_state("forbids.test") == {
        "interval_s": 0.02,
        "floor_s": 0.01,
        "backoffs": 0,
        "answered": 0,
    }


@pytest.mark.asyncio
async def test_a_request_in_flight_stops_retrying_once_the_circuit_opens(
    no_jitter: None,
) -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        # Other requests to the host trip its circuit while this one is out.
        circuit = host_circuit("tripped.test")
        for _ in range(_CIRCUIT_FAILURES):
            circuit.record_failure()
        return httpx.Response(502)

    profile = PolitenessProfile(rate_limit_per_minute=6000, robots_respect=False)
    transport = PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)
    bucket = host_bucket("tripped.test", 0.01)
    real_acquire = bucket.acquire
    turns = 0

    async def acquire() -> None:
        nonlocal turns
        turns += 1
        await real_acquire()

    bucket.acquire = acquire  # type: ignore[method-assign]
    async with httpx.AsyncClient(transport=transport) as c:
        with pytest.raises(HostCircuitOpen, match="stopped after 1 attempt"):
            await c.get("https://tripped.test/x")
    assert sent == 1
    # Stopped before waiting for another turn, not after.
    assert turns == 1
    # It failed at the host before it stopped, so it counts.
    assert host_circuit_state("tripped.test") == {
        "failures": _CIRCUIT_FAILURES + 1,
        "open": True,
        "trips": 1,
    }


@pytest.mark.asyncio
async def test_a_request_stops_when_the_circuit_opens_during_its_pace_wait() -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(503, headers={"retry-after": "0"})

    profile = PolitenessProfile(rate_limit_per_minute=6000, robots_respect=False)
    transport = PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)
    bucket = host_bucket("waits.test", 0.01)
    real_acquire = bucket.acquire
    turns = 0

    async def acquire() -> None:
        nonlocal turns
        turns += 1
        await real_acquire()
        if turns == 2:
            # The circuit opens while this request waits its turn to retry.
            circuit = host_circuit("waits.test")
            for _ in range(_CIRCUIT_FAILURES):
                circuit.record_failure()

    bucket.acquire = acquire  # type: ignore[method-assign]
    async with httpx.AsyncClient(transport=transport) as c:
        with pytest.raises(HostCircuitOpen):
            await c.get("https://waits.test/x")
    assert sent == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["502", "503", "ConnectError"])
async def test_the_probe_is_sent_once(outcome: str, no_jitter: None) -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        if outcome == "ConnectError":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(int(outcome), headers={"retry-after": "0"})

    circuit = host_circuit("once.test")
    for _ in range(_CIRCUIT_FAILURES):
        circuit.record_failure()
    assert circuit.opened_at is not None
    circuit.opened_at -= _CIRCUIT_COOLDOWN_S
    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        if outcome == "ConnectError":
            with pytest.raises(httpx.ConnectError):
                await c.get("https://once.test/x")
        else:
            assert (await c.get("https://once.test/x")).status_code == int(outcome)
    assert sent == 1
    # Its failure reopens the circuit at once.
    assert host_circuit_state("once.test") == {
        "failures": _CIRCUIT_FAILURES + 1,
        "open": True,
        "trips": 2,
    }


def test_a_straggler_never_shortens_a_refusal_hold() -> None:
    circuit = host_circuit("hold.test")
    straggler = circuit.admit()
    assert straggler is not None
    assert circuit.record_failure(refused=True, retry_after=300.0)
    assert circuit.cooldown_s == 300.0
    # Already open: restarting the cooldown, not opening it again, keeps the hold.
    assert not circuit.record_failure(straggler)
    assert circuit.cooldown_s == 300.0
    assert circuit.trips == 1
    assert not circuit.record_failure(straggler, refused=True, retry_after=600.0)
    assert circuit.cooldown_s == 600.0
    circuit.record_success()
    assert circuit.cooldown_s == _CIRCUIT_COOLDOWN_S


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
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return _ok() if healthy else httpx.Response(502)

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        for _ in range(_CIRCUIT_FAILURES):
            await c.get("https://probe.test/x")
        circuit = host_circuit("probe.test")
        assert circuit.opened_at is not None

        # A failed probe, sent once, reopens it without another full run of failures.
        circuit.opened_at -= _CIRCUIT_COOLDOWN_S
        sent = 0
        assert (await c.get("https://probe.test/x")).status_code == 502
        assert sent == 1
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
    straggler = circuit.admit()
    assert straggler is not None and not straggler.probe
    for _ in range(_CIRCUIT_FAILURES):
        circuit.record_failure()
    assert circuit.admit() is None
    assert circuit.opened_at is not None
    circuit.opened_at -= _CIRCUIT_COOLDOWN_S
    probe = circuit.admit()
    assert probe is not None and probe.probe
    assert circuit.admit() is None
    # A straggler admitted before the trip cannot free the probe's slot, whether
    # it ends without an outcome or fails.
    circuit.release(straggler)
    assert circuit.admit() is None
    circuit.record_failure(straggler)
    assert circuit.probe is probe
    # The probe itself, ending without an outcome (robots refusal, cancellation), frees it.
    assert circuit.opened_at is not None
    circuit.opened_at -= _CIRCUIT_COOLDOWN_S
    circuit.release(probe)
    assert circuit.admit() is not None


@pytest.mark.asyncio
async def test_cancelling_an_older_request_keeps_the_probe_single() -> None:
    import asyncio

    gates = {"/old": asyncio.Event(), "/probe": asyncio.Event()}

    async def handler(request: httpx.Request) -> httpx.Response:
        if gate := gates.get(request.url.path):
            await gate.wait()
        return _ok()

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        old = asyncio.create_task(c.get("https://slow.test/old"))
        await asyncio.sleep(0.01)  # admitted while the circuit is closed
        circuit = host_circuit("slow.test")
        for _ in range(_CIRCUIT_FAILURES):
            circuit.record_failure()
        assert circuit.opened_at is not None
        circuit.opened_at -= _CIRCUIT_COOLDOWN_S
        probe = asyncio.create_task(c.get("https://slow.test/probe"))
        await asyncio.sleep(0.01)
        assert circuit.probe is not None

        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
        # The probe is still in flight, so a second one is refused.
        with pytest.raises(HostCircuitOpen):
            await c.get("https://slow.test/x")

        gates["/probe"].set()
        assert (await probe).status_code == 200
    assert host_circuit_state("slow.test") == {"failures": 0, "open": False, "trips": 1}


class _DroppedAfter(httpx.AsyncByteStream):
    """A body that resets partway through the download."""

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield b"part of the "
        raise httpx.ReadError("connection reset mid-body")


@pytest.mark.asyncio
async def test_a_download_dropped_mid_body_is_retried(no_jitter: None) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, stream=_DroppedAfter())
        return _ok("whole document")

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        resp = await c.get("https://reset.test/doc")
    assert resp.text == "whole document"
    assert attempts == 2
    assert host_circuit_state("reset.test") == {"failures": 0, "open": False, "trips": 0}


@pytest.mark.asyncio
async def test_a_buffered_body_still_decodes_and_streams_raw() -> None:
    import gzip

    packed = gzip.compress(b"<akomaNtoso/>")

    def handler(request: httpx.Request) -> httpx.Response:
        # `stream=` leaves the body unread, as a network transport hands it over.
        return httpx.Response(
            200, stream=httpx.ByteStream(packed), headers={"content-encoding": "gzip"}
        )

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        assert (await c.get("https://gzip.test/doc")).content == b"<akomaNtoso/>"
        async with c.stream("GET", "https://gzip.test/doc") as resp:
            raw = b"".join([part async for part in resp.aiter_raw()])
    assert raw == packed


@pytest.mark.asyncio
async def test_an_unbuffered_stream_is_left_to_the_caller(no_jitter: None) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, stream=_DroppedAfter())

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        async with c.stream(
            "GET", "https://big.test/dump.zip", extensions={UNBUFFERED: True}
        ) as resp:
            assert resp.status_code == 200
            with pytest.raises(httpx.ReadError):
                await resp.aread()
    assert attempts == 1


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


def _two_addresses(_host: str) -> list[str]:
    return ["93.184.216.34", "93.184.216.35"]


def _trip(host: str) -> None:
    circuit = host_circuit(host)
    for _ in range(_CIRCUIT_FAILURES):
        circuit.record_failure()


def _paced(handler, resolver=_public, **kw) -> PoliteTransport:  # type: ignore[no-untyped-def]
    profile = PolitenessProfile(rate_limit_per_minute=6000, **kw)
    return PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=resolver)


def _trip_on_turn(host: str, turn: int) -> None:
    """Open the host's circuit while a request waits its `turn`th pace turn."""
    bucket = host_bucket(host, 0.01)
    real_acquire = bucket.acquire
    turns = 0

    async def acquire() -> None:
        nonlocal turns
        turns += 1
        await real_acquire()
        if turns == turn:
            _trip(host)

    bucket.acquire = acquire  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_no_request_reaches_the_host_once_its_circuit_opens(no_jitter: None) -> None:
    import asyncio

    sent_while_open = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent_while_open
        if host_circuit("crowd.test").is_open:
            sent_while_open += 1
        return httpx.Response(502)

    profile = PolitenessProfile(robots_respect=False, concurrency=8)
    transport = PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)
    async with httpx.AsyncClient(transport=transport) as c:
        outcomes = await asyncio.gather(
            *(c.get(f"https://crowd.test/{n}") for n in range(_CIRCUIT_FAILURES + 3)),
            return_exceptions=True,
        )
    assert sent_while_open == 0
    assert any(isinstance(o, HostCircuitOpen) for o in outcomes)
    assert host_circuit("crowd.test").is_open


@pytest.mark.asyncio
async def test_a_first_attempt_waiting_its_turn_is_not_sent_once_the_circuit_opens() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return _ok()

    _trip_on_turn("queued.test", 1)
    async with httpx.AsyncClient(transport=_paced(handler, robots_respect=False)) as c:
        with pytest.raises(HostCircuitOpen, match="at all"):
            await c.get("https://queued.test/x")
    assert sent == []
    # Never sent, it is neither outcome: the count stands and the circuit stays open.
    assert host_circuit_state("queued.test") == {
        "failures": _CIRCUIT_FAILURES,
        "open": True,
        "trips": 1,
    }


@pytest.mark.asyncio
async def test_the_robots_fetch_is_not_sent_once_the_circuit_opens() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return _ok()

    _trip_on_turn("rules.test", 1)
    transport = _paced(handler, robots_respect=True)
    async with httpx.AsyncClient(transport=transport) as c:
        with pytest.raises(HostCircuitOpen):
            await c.get("https://rules.test/x")
    assert sent == []
    # Our refusal to send is not the host's robots.txt failing: nothing is cached.
    assert "rules.test" not in transport._robots.entries


@pytest.mark.asyncio
async def test_a_request_is_not_sent_to_the_next_address_once_the_circuit_opens(
    no_jitter: None,
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.host)
        if request.url.host == "93.184.216.34":
            raise httpx.ConnectError("refused", request=request)
        return _ok()

    # The guard's turn before the second address is the request's second.
    _trip_on_turn("pair.test", 2)
    transport = _paced(handler, resolver=_two_addresses, robots_respect=False)
    async with httpx.AsyncClient(transport=transport) as c:
        with pytest.raises(HostCircuitOpen):
            await c.get("https://pair.test/x")
    assert sent == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_the_probe_unable_to_connect_tries_no_other_address() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.host)
        raise httpx.ConnectError("refused", request=request)

    _trip("unreachable.test")
    circuit = host_circuit("unreachable.test")
    assert circuit.opened_at is not None
    circuit.opened_at -= _CIRCUIT_COOLDOWN_S
    profile = PolitenessProfile(robots_respect=False)
    transport = PoliteTransport(
        profile=profile, inner=_make_inner(handler), resolver=_two_addresses
    )
    async with httpx.AsyncClient(transport=transport) as c:
        # What the host did, not the circuit, is what the caller sees.
        with pytest.raises(httpx.ConnectError):
            await c.get("https://unreachable.test/x")
    assert sent == ["93.184.216.34"]
    assert circuit.trips == 2


@pytest.mark.asyncio
async def test_the_probe_refused_at_one_address_tries_no_other() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.host)
        return httpx.Response(403)

    _trip("pair403.test")
    circuit = host_circuit("pair403.test")
    assert circuit.opened_at is not None
    circuit.opened_at -= _CIRCUIT_COOLDOWN_S
    profile = PolitenessProfile(robots_respect=False)
    transport = PoliteTransport(
        profile=profile, inner=_make_inner(handler), resolver=_two_addresses
    )
    async with httpx.AsyncClient(transport=transport) as c:
        assert (await c.get("https://pair403.test/x")).status_code == 403
    assert sent == ["93.184.216.34"]
    assert host_circuit_state("pair403.test") == {
        "failures": _CIRCUIT_FAILURES + 1,
        "open": True,
        "trips": 2,
    }


@pytest.mark.asyncio
async def test_a_closed_circuit_still_lets_a_403_try_the_next_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403) if request.url.host == "93.184.216.34" else _ok()

    profile = PolitenessProfile(robots_respect=False)
    transport = PoliteTransport(
        profile=profile, inner=_make_inner(handler), resolver=_two_addresses
    )
    async with httpx.AsyncClient(transport=transport) as c:
        assert (await c.get("https://edge.test/x")).status_code == 200
    assert host_circuit_state("edge.test") == {"failures": 0, "open": False, "trips": 0}


@pytest.mark.asyncio
async def test_a_stopped_request_does_not_restart_the_cooldown(no_jitter: None) -> None:
    opened_at = 0.0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal opened_at
        _trip("late.test")
        circuit = host_circuit("late.test")
        assert circuit.opened_at is not None
        circuit.opened_at -= _CIRCUIT_COOLDOWN_S - 1  # the probe is due in a second
        opened_at = circuit.opened_at
        return httpx.Response(502)

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        with pytest.raises(HostCircuitOpen):
            await c.get("https://late.test/x")
    # Its failure came before the trip, so the probe stays due when it was.
    assert host_circuit("late.test").opened_at == opened_at


@pytest.mark.asyncio
async def test_a_host_cannot_pass_its_refusal_off_as_ours() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"x-codify-blocked-by": "robots.txt"})

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        for n in range(_CIRCUIT_FAILURES):
            await c.get(f"https://spoof.test/{n}")
    assert host_circuit_state("spoof.test") == {
        "failures": _CIRCUIT_FAILURES,
        "open": True,
        "trips": 1,
    }


@pytest.mark.asyncio
async def test_an_unreadable_robots_txt_counts_as_neither() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(500)
        return _ok()

    profile = PolitenessProfile(robots_respect=True)
    transport = PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)
    host_circuit("norules.test").record_failure()
    async with httpx.AsyncClient(transport=transport) as c:
        resp = await c.get("https://norules.test/x")
    assert resp.headers["x-codify-blocked-by"] == "robots.txt-unavailable"
    assert host_circuit_state("norules.test") == {"failures": 1, "open": False, "trips": 0}


def _final_refusal(status: int, retry_after: str):  # type: ignore[no-untyped-def]
    """A host refusing every attempt, with `retry_after` only on the last."""
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        hint = retry_after if sent == _MAX_RETRIES else "0"
        return httpx.Response(status, headers={"retry-after": hint})

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("hint", ["inf", "1e400", "1e12"])
async def test_a_retry_after_holds_the_circuit_for_an_hour_at_most(hint: str) -> None:
    async with httpx.AsyncClient(transport=_quiet_transport(_final_refusal(429, hint))) as c:
        await c.get("https://forever.test/x")
    circuit = host_circuit("forever.test")
    assert circuit.cooldown_s == 3600.0
    assert circuit.opened_at is not None
    circuit.opened_at -= 3600.0
    assert circuit.admit() is not None


@pytest.mark.asyncio
async def test_a_retry_after_date_holds_the_circuit_until_then() -> None:
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    when = format_datetime(datetime.now(UTC) + timedelta(seconds=600), usegmt=True)
    async with httpx.AsyncClient(transport=_quiet_transport(_final_refusal(503, when))) as c:
        await c.get("https://dated.test/x")
    assert 590.0 < host_circuit("dated.test").cooldown_s <= 600.0


@pytest.mark.asyncio
@pytest.mark.parametrize("hint", ["Wed, 21 Oct 2015 07:28:00 GMT", "soon", "-5"])
async def test_a_past_or_unreadable_retry_after_holds_the_circuit_for_the_cooldown(
    hint: str,
) -> None:
    async with httpx.AsyncClient(transport=_quiet_transport(_final_refusal(503, hint))) as c:
        await c.get("https://stale.test/x")
    assert host_circuit("stale.test").cooldown_s == _CIRCUIT_COOLDOWN_S
    assert host_circuit_state("stale.test") == {"failures": 1, "open": True, "trips": 1}


@pytest.mark.asyncio
async def test_a_request_with_a_body_still_refused_opens_the_circuit_at_once() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "0"})

    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        await c.post("https://form.test/x", content=b"query=1")
    assert host_circuit_state("form.test") == {"failures": 1, "open": True, "trips": 1}


@pytest.mark.asyncio
async def test_a_refused_probe_reopens_the_circuit_for_its_retry_after() -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(429, headers={"retry-after": "120"})

    _trip("again.test")
    circuit = host_circuit("again.test")
    assert circuit.opened_at is not None
    circuit.opened_at -= _CIRCUIT_COOLDOWN_S
    async with httpx.AsyncClient(transport=_quiet_transport(handler)) as c:
        assert (await c.get("https://again.test/x")).status_code == 429
    assert sent == 1
    assert circuit.cooldown_s == 120.0
    assert circuit.trips == 2


@pytest.mark.asyncio
async def test_the_probe_reads_robots_txt_and_still_sends_its_request() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return _ok()

    _trip("back.test")
    circuit = host_circuit("back.test")
    assert circuit.opened_at is not None
    circuit.opened_at -= _CIRCUIT_COOLDOWN_S
    profile = PolitenessProfile(robots_respect=True)
    transport = PoliteTransport(profile=profile, inner=_make_inner(handler), resolver=_public)
    async with httpx.AsyncClient(transport=transport) as c:
        assert (await c.get("https://back.test/x")).status_code == 200
    # The robots fetch is not the probe's one attempt; the request is.
    assert sent == ["/robots.txt", "/x"]
    assert host_circuit_state("back.test") == {"failures": 0, "open": False, "trips": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", [1, _MAX_RETRIES])
async def test_a_request_stopped_at_the_next_address_stops_without_a_backoff(
    attempt: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    draws: list[float] = []
    monkeypatch.setattr(politeness.random, "uniform", lambda _a, b: draws.append(b) or 0.0)
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.host)
        raise httpx.ConnectError("refused", request=request)

    circuit = host_circuit("stuck.test")
    bucket = host_bucket("stuck.test", 0.01)
    real_acquire = bucket.acquire
    turns = 0
    tripped_at: float | None = None

    async def acquire() -> None:
        nonlocal turns, tripped_at
        turns += 1
        await real_acquire()
        # Each attempt takes a turn, then the guard one more before the second address.
        if turns == 2 * attempt:
            _trip("stuck.test")
            tripped_at = circuit.opened_at

    bucket.acquire = acquire  # type: ignore[method-assign]
    transport = _paced(handler, resolver=_two_addresses, robots_respect=False)
    async with httpx.AsyncClient(transport=transport) as c:
        with pytest.raises(HostCircuitOpen):
            await c.get("https://stuck.test/x")
    # Both addresses on every attempt before the trip, then only the first.
    assert len(sent) == 2 * attempt - 1
    # No backoff drawn for the attempt it stopped on.
    assert len(draws) == attempt - 1
    # Counted, but the cooldown runs from the trip, not from the stop.
    assert tripped_at is not None and circuit.opened_at == tripped_at
    assert host_circuit_state("stuck.test") == {
        "failures": _CIRCUIT_FAILURES + 1,
        "open": True,
        "trips": 1,
    }


def _robots_redirects(hops: int, sent: list[str]):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(f"{request.headers['host']}{request.url.path}")
        if request.url.path.startswith("/robots"):
            n = int(request.url.params.get("n", "0"))
            if n < hops:
                return httpx.Response(302, headers={"location": f"/robots.txt?n={n + 1}"})
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return _ok()

    return handler


@pytest.mark.asyncio
async def test_the_probe_follows_a_robots_redirect_like_any_read() -> None:
    sent: list[str] = []
    _trip("moved.test")
    circuit = host_circuit("moved.test")
    assert circuit.opened_at is not None
    circuit.opened_at -= _CIRCUIT_COOLDOWN_S
    profile = PolitenessProfile(robots_respect=True)
    transport = PoliteTransport(
        profile=profile, inner=_make_inner(_robots_redirects(4, sent)), resolver=_public
    )
    async with httpx.AsyncClient(transport=transport) as c:
        assert (await c.get("https://moved.test/x")).status_code == 200
    # However many hops robots.txt takes, the host can still recover.
    assert sent == ["moved.test/robots.txt"] * 5 + ["moved.test/x"]
    assert not circuit.is_open
