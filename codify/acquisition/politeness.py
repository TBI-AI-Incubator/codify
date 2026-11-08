"""httpx transport with rate limits, robots, ETags, retry backoff and host circuits."""

from __future__ import annotations

import asyncio
import os
import random
import ssl
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib import robotparser
from urllib.parse import urlparse

import certifi
import httpx
import structlog

from codify.acquisition.base import PolitenessProfile
from codify.core.retry_after import parse_retry_after as _parse_retry_after
from codify.core.security import Resolver, SSRFBlocked, safe_addresses, ssrf_guard
from codify.jurisdictions import SourceAdapter

logger = structlog.get_logger()


_MAX_RETRIES = 5
_MAX_BACKOFF_S = 60
_ROBOTS_OK_TTL_S = 24 * 3600
_ROBOTS_FAIL_TTL_S = 5 * 60


_BACKOFF_STATUSES = frozenset({429, 436, 503})
# A gateway's failure says nothing about our pace: retried with jitter, interval untouched.
_TRANSIENT_STATUSES = frozenset({502, 504})
_FAILED_STATUSES = _BACKOFF_STATUSES | _TRANSIENT_STATUSES
# Failures before the request was answered; replayed only for a bodyless request.
_TRANSIENT_ERRORS: tuple[type[httpx.TransportError], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)
_BACKOFF_FACTOR = 2.0
_RECOVERY_FACTOR = 0.95
_MAX_INTERVAL_S = 300.0


@dataclass
class _HostBucket:
    """Adaptive interval per host, shared by every client in the process.

    A refusal (429, 436, 503) doubles the interval or takes Retry-After; each
    answered request brings it back by 0.95 toward the configured floor."""

    floor_s: float
    interval_s: float = 0.0
    last_request_at: float = 0.0
    backoffs: int = 0
    answered: int = 0
    _lock: asyncio.Lock | None = None
    _loop: asyncio.AbstractEventLoop | None = None

    def __post_init__(self) -> None:
        if not self.interval_s:
            self.interval_s = self.floor_s

    def _lock_for_loop(self) -> asyncio.Lock:
        # A lock binds to one loop; a bucket shared across loops guards each loop's own turn.
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock, self._loop = asyncio.Lock(), loop
        return self._lock

    async def acquire(self) -> None:
        async with self._lock_for_loop():
            # Re-read after every sleep: a refusal on another request moves the deadline.
            while (wait := self.last_request_at + self.interval_s - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            self.last_request_at = time.monotonic()

    def record_answer(self) -> bool:
        """Count an answered request; True when it brought the interval back to the floor."""
        self.answered += 1
        if self.interval_s <= self.floor_s:
            return False
        self.interval_s = max(self.floor_s, self.interval_s * _RECOVERY_FACTOR)
        return self.interval_s <= self.floor_s

    def record_backoff(self, retry_after: float | None) -> float:
        self.backoffs += 1
        if retry_after is not None:
            self.interval_s = min(_MAX_INTERVAL_S, max(self.floor_s, retry_after))
        else:
            self.interval_s = min(_MAX_INTERVAL_S, self.interval_s * _BACKOFF_FACTOR + 0.5)
        # The wait counts from the refusal, not from the request that drew it.
        self.last_request_at = time.monotonic()
        return self.interval_s

    def snapshot(self) -> dict[str, float | int]:
        return {
            "interval_s": round(self.interval_s, 3),
            "floor_s": self.floor_s,
            "backoffs": self.backoffs,
            "answered": self.answered,
        }


_BUCKETS: dict[str, _HostBucket] = {}


def host_bucket(host: str, floor_s: float) -> _HostBucket:
    """The process-wide bucket for a host; the strictest configured floor wins."""
    bucket = _BUCKETS.get(host)
    if bucket is None:
        bucket = _BUCKETS[host] = _HostBucket(floor_s=floor_s)
    elif floor_s > bucket.floor_s:
        bucket.floor_s = floor_s
        bucket.interval_s = max(bucket.interval_s, floor_s)
    return bucket


def host_bucket_state(host: str) -> dict[str, float | int] | None:
    bucket = _BUCKETS.get(host)
    return bucket.snapshot() if bucket is not None else None


def reset_host_buckets() -> None:
    """Tests only: forget every host's pace."""
    _BUCKETS.clear()


_CIRCUIT_FAILURES = 5
_CIRCUIT_COOLDOWN_S = 60.0


class HostCircuitOpen(httpx.TransportError):
    """A host failed enough requests in a row that this one was refused unsent."""


@dataclass(frozen=True, eq=False)
class _Ticket:
    """One admitted request. Compared by identity, so only the probe's own
    ticket can free or settle the probe slot."""

    probe: bool


@dataclass
class _HostCircuit:
    """Consecutive failed requests per host, shared by every client in the process.

    A request counts once, after its own retries, so one bad document cannot
    trip it alone. Open, it refuses every request for the cooldown; then one
    probe goes through while the others are still refused, and its outcome
    closes or reopens the circuit. A request admitted before the trip and still
    in flight is a straggler: it counts, but it never holds the probe slot."""

    failures: int = 0
    opened_at: float | None = None
    probe: _Ticket | None = None
    trips: int = 0

    def admit(self) -> _Ticket | None:
        """A ticket to send, or None to refuse unsent."""
        if self.opened_at is None:
            return _Ticket(probe=False)
        if self.probe is not None or time.monotonic() - self.opened_at < _CIRCUIT_COOLDOWN_S:
            return None
        self.probe = _Ticket(probe=True)
        return self.probe

    def record_success(self, ticket: _Ticket | None = None) -> None:
        # Any answer shows the host is up: the circuit closes, and a probe still
        # in flight becomes an ordinary request.
        self.failures, self.opened_at, self.probe = 0, None, None

    def record_failure(self, ticket: _Ticket | None = None) -> bool:
        """Count a failed request; True when it opened (or reopened) the circuit."""
        self.failures += 1
        probing = ticket is not None and ticket is self.probe
        opens = probing or (self.opened_at is None and self.failures >= _CIRCUIT_FAILURES)
        if self.opened_at is not None or opens:
            # A straggler admitted before the trip restarts the cooldown too.
            self.opened_at = time.monotonic()
        if opens:
            self.trips += 1
        if probing:
            self.probe = None
        return opens

    def release(self, ticket: _Ticket) -> None:
        """Neither outcome (robots or guard refusal, cancellation). Only the probe's
        own ticket frees the slot; a straggler ending this way leaves it held."""
        if ticket is self.probe:
            self.probe = None

    def snapshot(self) -> dict[str, int | bool]:
        return {
            "failures": self.failures,
            "open": self.opened_at is not None,
            "trips": self.trips,
        }


_CIRCUITS: dict[str, _HostCircuit] = {}


def host_circuit(host: str) -> _HostCircuit:
    """The process-wide circuit for a host."""
    circuit = _CIRCUITS.get(host)
    if circuit is None:
        circuit = _CIRCUITS[host] = _HostCircuit()
    return circuit


def host_circuit_state(host: str) -> dict[str, int | bool] | None:
    circuit = _CIRCUITS.get(host)
    return circuit.snapshot() if circuit is not None else None


def reset_host_circuits() -> None:
    """Tests only: close every host's circuit."""
    _CIRCUITS.clear()


#: Request extension: `client.stream(..., extensions={UNBUFFERED: True})` keeps a
#: body too large to hold streaming from the socket. Its download then happens
#: after the transport returns, so a failure partway through is not retried.
UNBUFFERED = "codify_unbuffered"


async def _buffered(response: httpx.Response) -> httpx.Response:
    """The response with its body downloaded now, inside the retry boundary.

    The raw bytes are kept, still encoded, behind a fresh stream, so the client
    decodes them as it would have and `aiter_raw` still works once."""
    if response.is_stream_consumed:
        # Already in memory (a transport that built it from bytes): nothing left to drop.
        return response
    try:
        raw = b"".join([part async for part in response.aiter_raw()])
    finally:
        await response.aclose()
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        stream=httpx.ByteStream(raw),
        # The connection is closed; the rest (http_version, reason_phrase) still holds.
        extensions={k: v for k, v in response.extensions.items() if k != "network_stream"},
    )


def _jitter_wait(attempt: int, retry_after: float | None) -> float:
    """Retry-After when the host gave one, else exponential with full jitter."""
    if retry_after is not None:
        return min(retry_after, _MAX_BACKOFF_S)
    return min(random.uniform(0, 2**attempt), _MAX_BACKOFF_S)  # noqa: S311


@dataclass
class _RobotsCacheEntry:
    cached_at: float
    parser: robotparser.RobotFileParser
    ttl_s: float
    # When True, the parser is empty because the fetch failed; default-deny.
    fetch_failed: bool = False
    failure: str = ""


@dataclass
class _RobotsCache:
    """LRU cache of robots.txt. Success caches 24h; failure caches 5 min."""

    max_entries: int = 256
    entries: OrderedDict[str, _RobotsCacheEntry] = field(default_factory=OrderedDict)

    def is_fresh(self, host: str) -> bool:
        """True when `allowed` would answer from the cache without a fetch."""
        cached = self.entries.get(host)
        return cached is not None and (time.monotonic() - cached.cached_at) < cached.ttl_s

    async def allowed(self, client: httpx.AsyncClient, url: str, ua: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme:
            return True
        host = parsed.netloc
        now = time.monotonic()
        cached = self.entries.get(host)
        if cached is not None and (now - cached.cached_at) < cached.ttl_s:
            self.entries.move_to_end(host)
            if cached.fetch_failed:
                return False
            return cached.parser.can_fetch(ua, url)

        entry = await self._fetch(client, parsed.scheme, host)
        self.entries[host] = entry
        if len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)
        if entry.fetch_failed:
            return False
        return entry.parser.can_fetch(ua, url)

    async def _fetch(self, client: httpx.AsyncClient, scheme: str, host: str) -> _RobotsCacheEntry:
        rp = robotparser.RobotFileParser()
        robots_url = f"{scheme}://{host}/robots.txt"
        now = time.monotonic()
        try:
            resp = await client.get(robots_url, timeout=httpx.Timeout(10.0))
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            logger.warning("robots_fetch_failed", host=host, error=str(exc))
            rp.parse([])
            return _RobotsCacheEntry(
                now, rp, _ROBOTS_FAIL_TTL_S, fetch_failed=True, failure=type(exc).__name__
            )

        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
            return _RobotsCacheEntry(now, rp, _ROBOTS_OK_TTL_S)

        if resp.status_code in (301, 302, 401, 403, 404, 410):  # RFC 9309, no robots
            rp.parse([])
            return _RobotsCacheEntry(now, rp, _ROBOTS_OK_TTL_S)

        logger.warning("robots_fetch_failed", host=host, status=resp.status_code)
        rp.parse([])
        failure = f"HTTP {resp.status_code}"
        return _RobotsCacheEntry(now, rp, _ROBOTS_FAIL_TTL_S, fetch_failed=True, failure=failure)


_REPLAYABLE = frozenset({"GET", "HEAD"})


def _replayable(request: httpx.Request) -> bool:
    """Bodyless: a stream the first send consumed cannot be sent again."""
    if request.method not in _REPLAYABLE:
        return False
    if request.headers.get("content-length", "0") != "0":
        return False
    return "transfer-encoding" not in request.headers


class _NoAlpnContext(ssl.SSLContext):
    """A client context whose handshake never offers ALPN.

    A library-shaped ALPN offer is a bot signature some edges refuse outright;
    with no offer the server serves HTTP/1.1 as it does to any plain client.
    """

    def set_alpn_protocols(self, alpn_protocols: object) -> None:
        return None


def client_tls_context() -> ssl.SSLContext:
    default = ssl.create_default_context()
    ctx = _NoAlpnContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.options |= default.options
    ctx.verify_flags = default.verify_flags
    if default.keylog_filename:
        ctx.keylog_filename = default.keylog_filename
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    # Same trust roots httpx would pick: SSL_CERT_FILE, else SSL_CERT_DIR, else certifi.
    if cafile := os.environ.get("SSL_CERT_FILE"):
        ctx.load_verify_locations(cafile=cafile)
    elif capath := os.environ.get("SSL_CERT_DIR"):
        ctx.load_verify_locations(capath=capath)
    else:
        ctx.load_verify_locations(cafile=certifi.where())
    return ctx


class GuardedTransport(httpx.AsyncBaseTransport):
    """Resolve once, refuse private addresses, connect to the address checked.

    Wraps every hop, so a redirect chain cannot walk from a public host to an
    internal one, and the socket cannot resolve to something the guard never
    saw (DNS rebinding). The Host header and TLS SNI keep the original
    hostname, so virtual hosting and certificate validation are unaffected.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        resolver: Resolver | None = None,
        before_retry: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._inner = inner
        self._resolver = resolver
        # Awaited with the host before a replay, so the caller's rate limit
        # charges the second hop too.
        self._before_retry = before_retry

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        loop = asyncio.get_running_loop()
        addresses = await loop.run_in_executor(None, self._resolve, url_str)
        host = request.url.host
        if not addresses:
            # Fail closed. An empty answer now and a private answer when the
            # socket resolves is the same check-then-use window this class
            # exists to shut.
            raise SSRFBlocked(url_str, "host did not resolve to a usable address")
        # A connection that never opened moves on for any request, a 403 only for
        # a bodyless one; a reset after bytes were sent is raised, never replayed.
        last = len(addresses) - 1
        failed: httpx.ConnectError | httpx.ConnectTimeout | None = None
        for index, address in enumerate(addresses):
            if index:
                if self._before_retry is not None:
                    await self._before_retry(host)
            try:
                response = await self._send_pinned(request, address, host)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                failed = exc
                logger.warning(
                    "guard_connect_failed",
                    host=host,
                    address=address,
                    error=str(exc)[:120],
                )
                if index < last:
                    logger.warning(
                        "guard_retry_next_address",
                        host=host,
                        refused=address,
                        retried=addresses[index + 1],
                    )
                continue
            if response.status_code == 403 and _replayable(request) and index < last:
                await response.aclose()
                logger.warning(
                    "guard_retry_next_address",
                    host=host,
                    refused=address,
                    retried=addresses[index + 1],
                )
                continue
            return response
        if failed is not None:
            raise failed
        return response

    async def _send_pinned(self, request: httpx.Request, address: str, host: str) -> httpx.Response:
        # A copy, so the caller's request keeps its hostname for its own retries.
        literal = f"[{address}]" if ":" in address else address
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=literal),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        pinned.headers["host"] = f"{host}:{request.url.port}" if request.url.port else host
        return await self._inner.handle_async_request(pinned)

    def _resolve(self, url: str) -> list[str]:
        if self._resolver is not None:
            return safe_addresses(url, resolver=self._resolver)
        return safe_addresses(url)

    async def aclose(self) -> None:
        await self._inner.aclose()


EtagLookup = Callable[[str], Awaitable[tuple[str | None, str | None]]]


class PoliteTransport(httpx.AsyncBaseTransport):
    """httpx transport adding rate-limit + robots + ETag injection + 429/503 backoff."""

    def __init__(
        self,
        *,
        profile: PolitenessProfile,
        etag_lookup: EtagLookup | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.profile = profile
        self.etag_lookup = etag_lookup
        # Guarded at the transport seam so the robots probe, the redirect
        # hops and the main request all share one check.
        self._inner = GuardedTransport(
            inner or httpx.AsyncHTTPTransport(verify=client_tls_context()),
            resolver=resolver,
            before_retry=self._take_token,
        )
        self._robots = _RobotsCache()
        self._sem = asyncio.Semaphore(profile.concurrency)
        self._probe = httpx.AsyncClient(
            transport=self._inner,
            follow_redirects=True,
            # Robots probe identifies like the real requests (WAFs flag default UAs).
            headers={"user-agent": profile.user_agent},
        )

    async def _take_token(self, host: str) -> None:
        bucket = self._bucket(host)
        if bucket is not None:
            await bucket.acquire()

    def _bucket(self, host: str) -> _HostBucket | None:
        if not self.profile.rate_limit_per_minute:
            return None
        return host_bucket(host, 60.0 / max(1, self.profile.rate_limit_per_minute))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with self._sem:
            host = request.url.host
            circuit = host_circuit(host)
            ticket = circuit.admit()
            if ticket is None:
                logger.warning("polite_circuit_refused", host=host, **circuit.snapshot())
                raise HostCircuitOpen(
                    f"{host} failed {circuit.failures} requests in a row; refusing "
                    f"requests for {_CIRCUIT_COOLDOWN_S:.0f}s from the last failure",
                    request=request,
                )
            answered: bool | None = None
            try:
                response = await self._send(request, host)
                # A robots refusal is ours, not the host's answer.
                if "x-codify-blocked-by" not in response.headers:
                    answered = response.status_code not in _FAILED_STATUSES
                return response
            except _TRANSIENT_ERRORS:
                answered = False
                raise
            finally:
                if answered is None:
                    circuit.release(ticket)
                elif answered:
                    circuit.record_success(ticket)
                elif circuit.record_failure(ticket):
                    logger.warning("polite_circuit_opened", host=host, **circuit.snapshot())

    async def _send(self, request: httpx.Request, host: str) -> httpx.Response:
        url_str = str(request.url)

        bucket = self._bucket(host)
        if self.profile.robots_respect:
            # The robots probe is a request to the host too, so it takes
            # its turn in the bucket rather than riding ahead of the fetch.
            if bucket is not None and not self._robots.is_fresh(host):
                await bucket.acquire()
            allowed = await self._robots.allowed(self._probe, url_str, self.profile.user_agent)
            if not allowed:
                # Explicit reason: a bare sentinel reads as a remote error.
                entry = self._robots.entries.get(urlparse(url_str).netloc)
                if entry is not None and entry.fetch_failed:
                    why = entry.failure
                    return httpx.Response(
                        status_code=451,
                        headers={"x-codify-blocked-by": "robots.txt-unavailable"},
                        extensions={"reason_phrase": f"robots.txt unavailable ({why})".encode()},
                        text=(
                            f"robots.txt for {host} could not be read ({why}); "
                            f"requests to {host} are held for up to "
                            f"{int(_ROBOTS_FAIL_TTL_S // 60)} minutes"
                        ),
                        request=request,
                    )
                return httpx.Response(
                    status_code=451,
                    headers={"x-codify-blocked-by": "robots.txt"},
                    extensions={"reason_phrase": b"Blocked by robots.txt"},
                    text=(
                        f"blocked by {host}/robots.txt for "
                        f"user-agent {self.profile.user_agent!r} "
                        "(set robots_respect=false on the source to override)"
                    ),
                    request=request,
                )

        if self.etag_lookup is not None and "if-none-match" not in request.headers:
            etag, last_modified = await self.etag_lookup(url_str)
            if etag:
                request.headers["if-none-match"] = etag
            if last_modified:
                request.headers["if-modified-since"] = last_modified

        request.headers["user-agent"] = self.profile.user_agent

        replayable = _replayable(request)
        # A dropped download is replayed with the request, so a replayable body is
        # read here rather than by the client after this returns.
        buffer = replayable and not request.extensions.get(UNBUFFERED)
        response: httpx.Response | None = None
        for attempt in range(_MAX_RETRIES):
            last = attempt == _MAX_RETRIES - 1
            if bucket is not None:
                await bucket.acquire()
            try:
                response = await self._inner.handle_async_request(request)
                # A failed status is retried or returned as it stands, unread.
                if buffer and response.status_code not in _FAILED_STATUSES:
                    response = await _buffered(response)
            except _TRANSIENT_ERRORS as exc:
                if not replayable:
                    raise
                if last:
                    logger.warning(
                        "polite_retry_exhausted",
                        host=host,
                        attempts=_MAX_RETRIES,
                        last_error=type(exc).__name__,
                    )
                    raise
                wait = _jitter_wait(attempt, None)
                logger.warning(
                    "polite_retry",
                    host=host,
                    error=type(exc).__name__,
                    attempt=attempt + 1,
                    wait_s=round(wait, 3),
                )
                await asyncio.sleep(wait)
                continue
            retry_after = _parse_retry_after(response.headers.get("retry-after"))
            if response.status_code in _TRANSIENT_STATUSES and replayable:
                if last:
                    logger.warning(
                        "polite_retry_exhausted",
                        host=host,
                        attempts=_MAX_RETRIES,
                        last_status=response.status_code,
                    )
                    return response
                wait = _jitter_wait(attempt, retry_after)
                logger.warning(
                    "polite_retry",
                    host=host,
                    status=response.status_code,
                    attempt=attempt + 1,
                    wait_s=round(wait, 3),
                )
                await response.aclose()
                await asyncio.sleep(wait)
                continue
            if response.status_code not in _BACKOFF_STATUSES:
                if bucket is not None and bucket.record_answer():
                    logger.info("polite_recovered", host=host, **bucket.snapshot())
                return response
            if bucket is None:
                # No configured pace to adapt: exponential with full jitter.
                wait = _jitter_wait(attempt, retry_after)
            else:
                wait = bucket.record_backoff(retry_after)
            logger.warning(
                "polite_backoff",
                host=host,
                status=response.status_code,
                retry_after=retry_after,
                attempt=attempt + 1,
                wait_s=round(wait, 3),
                **(bucket.snapshot() if bucket is not None else {}),
            )
            # Last attempt, keep response open so caller can read body.
            if last:
                logger.warning(
                    "polite_retry_exhausted",
                    host=host,
                    attempts=_MAX_RETRIES,
                    last_status=response.status_code,
                )
                return response
            await response.aclose()
            if bucket is None:
                await asyncio.sleep(wait)
            # With a bucket, the next acquire waits the new interval from the refusal.
        assert response is not None
        return response

    async def aclose(self) -> None:
        await self._probe.aclose()
        await self._inner.aclose()


def make_polite_client(
    adapter: SourceAdapter,
    *,
    etag_lookup: EtagLookup | None = None,
    timeout: httpx.Timeout | None = None,
    user_agent: str | None = None,
    profile: PolitenessProfile | None = None,
) -> httpx.AsyncClient:
    """`profile`, when given, is the acquirer's resolved pace and concurrency."""
    profile = (
        profile.model_copy()
        if profile is not None
        else PolitenessProfile(
            rate_limit_per_minute=adapter.rate_limit_per_minute,
            robots_respect=adapter.robots_respect,
        )
    )
    if user_agent:
        profile.user_agent = user_agent
    transport = PoliteTransport(profile=profile, etag_lookup=etag_lookup)
    return httpx.AsyncClient(
        transport=transport,
        timeout=timeout or httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    )


__all__ = [
    "EtagLookup",
    "HostCircuitOpen",
    "client_tls_context",
    "PoliteTransport",
    "SSRFBlocked",
    "UNBUFFERED",
    "host_bucket",
    "host_bucket_state",
    "host_circuit",
    "host_circuit_state",
    "make_polite_client",
    "reset_host_buckets",
    "reset_host_circuits",
    "ssrf_guard",
]
