"""ssrf_guard + PoliteTransport per-hop block + ingest-url integration."""

from __future__ import annotations

import httpx
import pytest

from codify.acquisition.base import PolitenessProfile
from codify.acquisition.politeness import PoliteTransport
from codify.core.security import SSRFBlocked, ssrf_guard


@pytest.mark.parametrize(
    "url, addr",
    [
        ("http://127.0.0.1/x", None),
        ("http://10.0.0.5/x", None),
        ("http://192.168.1.1/x", None),
        ("http://172.16.0.1/x", None),
        ("http://169.254.169.254/", None),
        ("http://attacker.example/", "10.0.0.5"),
        ("http://attacker.example/", "127.0.0.1"),
        ("http://attacker.example/", "169.254.169.254"),
        ("http://[::1]/", None),
        ("http://[fc00::1]/", None),
        ("http://[fe80::1]/", None),
        ("http://metadata.google.internal/", None),
        ("http://metadata.azure.com/", None),
    ],
)
def test_ssrf_guard_blocks_dangerous(url: str, addr: str | None) -> None:
    resolver = (lambda h: [addr]) if addr else (lambda h: [])
    with pytest.raises(SSRFBlocked):
        ssrf_guard(url, resolver=resolver)  # type: ignore[arg-type]


def test_ssrf_guard_allows_public() -> None:
    ssrf_guard("https://eur-lex.europa.eu/", resolver=lambda h: ["1.1.1.1"])
    ssrf_guard("https://example.com/", resolver=lambda h: ["8.8.8.8", "2606:4700::1"])


def test_ssrf_guard_trailing_dot_blocked() -> None:
    with pytest.raises(SSRFBlocked):
        ssrf_guard("http://metadata.google.internal./latest", resolver=lambda h: [])


def test_default_resolver_fails_closed_on_transient_gaierror() -> None:
    import socket as _socket

    from codify.core.security import _default_resolver

    def _boom(*_a, **_kw):
        raise _socket.gaierror(_socket.EAI_AGAIN, "tryagain")

    real = _socket.getaddrinfo
    _socket.getaddrinfo = _boom  # type: ignore[assignment]
    try:
        with pytest.raises(SSRFBlocked):
            _default_resolver("flapping.example")
    finally:
        _socket.getaddrinfo = real  # type: ignore[assignment]


def test_default_resolver_returns_empty_on_nxdomain() -> None:
    import socket as _socket

    from codify.core.security import _default_resolver

    def _boom(*_a, **_kw):
        raise _socket.gaierror(_socket.EAI_NONAME, "nxdomain")

    real = _socket.getaddrinfo
    _socket.getaddrinfo = _boom  # type: ignore[assignment]
    try:
        assert _default_resolver("does-not-exist.example") == []
    finally:
        _socket.getaddrinfo = real  # type: ignore[assignment]


@pytest.mark.asyncio
@pytest.mark.parametrize("redirect_target", ["http://10.0.0.5/admin", "http://[::1]/admin"])
async def test_polite_transport_blocks_per_hop(redirect_target: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://public.test/path":
            return httpx.Response(302, headers={"location": redirect_target})
        return httpx.Response(200, text="leaked")

    inner = httpx.MockTransport(handler)
    profile = PolitenessProfile(robots_respect=False)
    transport = PoliteTransport(profile=profile, inner=inner)

    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        with pytest.raises(SSRFBlocked):
            await client.get("https://public.test/path")


@pytest.mark.asyncio
async def test_robots_probe_cannot_redirect_into_the_private_network() -> None:
    """The robots probe used its own unguarded client, so a source could point
    /robots.txt at an internal address and have the server fetch it."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data"}
            )
        return httpx.Response(200, text="ok")

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=True),
        inner=httpx.MockTransport(handler),
        resolver=lambda _host: ["93.184.216.34"],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SSRFBlocked):
            await client.get("https://public.test/doc.pdf")
    assert not any("169.254.169.254" in url for url in seen), seen


@pytest.mark.asyncio
async def test_the_connection_uses_the_address_that_was_validated() -> None:
    """Rebinding: the guard's lookup and the socket's lookup are different
    questions unless the validated address is the one dialled. The second
    answer here is private; only the pinned first answer reaches the inner
    transport, and Host/SNI still carry the original name."""
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    def rebinding_resolver(host: str) -> list[str]:
        return next(answers, ["127.0.0.1"])

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=httpx.MockTransport(handler),
        resolver=rebinding_resolver,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://public.test/doc.pdf")

    assert seen, "request never reached the inner transport"
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "public.test"
    assert seen[0].extensions.get("sni_hostname") == "public.test"


def _edge_pair(first_status: int) -> tuple[list[tuple[str, str]], httpx.MockTransport]:
    # One fresh pinned copy per hop; record what each one dialled and named.
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers["host"]))
        if request.url.host == "203.0.113.64":
            return httpx.Response(first_status, text="forbidden")
        return httpx.Response(200, text="page")

    return seen, httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_a_403_from_the_pinned_edge_is_retried_once_on_the_next_address() -> None:
    """One edge of a multi-address host refuses what its siblings serve. The
    guard replays the bodyless request on the next validated address and says
    so once, naming both."""
    from structlog.testing import capture_logs

    seen, inner = _edge_pair(403)
    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=inner,
        resolver=lambda _host: ["203.0.113.64", "203.0.113.12"],
    )
    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get("https://public.test/page")

    assert resp.status_code == 200 and resp.text == "page"
    assert seen == [("203.0.113.64", "public.test"), ("203.0.113.12", "public.test")]
    retries = [entry for entry in logs if entry["event"] == "guard_retry_next_address"]
    assert len(retries) == 1
    assert retries[0]["refused"] == "203.0.113.64" and retries[0]["retried"] == "203.0.113.12"


@pytest.mark.asyncio
async def test_a_403_with_one_address_is_returned_as_is() -> None:
    from structlog.testing import capture_logs

    seen, inner = _edge_pair(403)
    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=inner,
        resolver=lambda _host: ["203.0.113.64"],
    )
    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get("https://public.test/page")

    assert resp.status_code == 403
    assert len(seen) == 1
    assert not [entry for entry in logs if entry["event"] == "guard_retry_next_address"]


@pytest.mark.asyncio
async def test_a_404_is_not_retried_on_another_address() -> None:
    seen, inner = _edge_pair(404)
    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=inner,
        resolver=lambda _host: ["203.0.113.64", "203.0.113.12"],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://public.test/page")

    assert resp.status_code == 404
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_polite_retry_still_carries_the_hostname() -> None:
    """The polite 429/503 loop resends the same request; the guard must not
    have rewritten its host to the pinned literal in between."""
    seen: list[tuple[str, str, str]] = []
    answers = iter([503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers["host"], request.extensions["sni_hostname"]))
        return httpx.Response(next(answers), headers={"retry-after": "0"}, text="page")

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=httpx.MockTransport(handler),
        resolver=lambda _host: ["93.184.216.34"],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://public.test/page")

    assert resp.status_code == 200
    assert seen == [("93.184.216.34", "public.test", "public.test")] * 2


@pytest.mark.asyncio
async def test_a_403_on_a_request_with_a_body_is_not_replayed() -> None:
    seen, inner = _edge_pair(403)
    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=inner,
        resolver=lambda _host: ["203.0.113.64", "203.0.113.12"],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.request("GET", "https://public.test/page", content=b"filter=1")

    assert resp.status_code == 403
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_every_address_refusing_passes_the_last_403_through() -> None:
    from structlog.testing import capture_logs

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(403, text="forbidden")

    addresses = ["203.0.113.64", "203.0.113.12", "203.0.113.7", "203.0.113.9"]
    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=httpx.MockTransport(handler),
        resolver=lambda _host: list(addresses),
    )
    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get("https://public.test/page")

    assert resp.status_code == 403
    assert seen == addresses
    assert len([e for e in logs if e["event"] == "guard_retry_next_address"]) == 3


@pytest.mark.asyncio
async def test_two_refusing_edges_are_walked_past_on_three_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from structlog.testing import capture_logs

    from codify.acquisition import politeness

    taken: list[str] = []
    original = politeness._HostBucket.acquire

    async def counting_acquire(self: politeness._HostBucket) -> None:
        taken.append("token")
        await original(self)

    monkeypatch.setattr(politeness._HostBucket, "acquire", counting_acquire)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host in ("203.0.113.64", "203.0.113.12"):
            return httpx.Response(403, text="forbidden")
        return httpx.Response(200, text="page")

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False, rate_limit_per_minute=60_000),
        inner=httpx.MockTransport(handler),
        resolver=lambda _host: ["203.0.113.64", "203.0.113.12", "203.0.113.7", "203.0.113.9"],
    )
    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get("https://public.test/page")

    assert resp.status_code == 200 and resp.text == "page"
    assert seen == ["203.0.113.64", "203.0.113.12", "203.0.113.7"]
    assert len(taken) == 3
    retries = [e for e in logs if e["event"] == "guard_retry_next_address"]
    assert [(e["refused"], e["retried"]) for e in retries] == [
        ("203.0.113.64", "203.0.113.12"),
        ("203.0.113.12", "203.0.113.7"),
    ]


@pytest.mark.asyncio
async def test_a_403_on_a_post_is_not_replayed() -> None:
    seen, inner = _edge_pair(403)
    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=inner,
        resolver=lambda _host: ["203.0.113.64", "203.0.113.12"],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.post("https://public.test/page", content=b"q=1")

    assert resp.status_code == 403
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_replay_takes_a_second_rate_limit_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The replay happens below the polite loop's bucket, so the guard asks
    for the token itself; two hops, two tokens."""
    from codify.acquisition import politeness

    taken: list[str] = []
    original = politeness._HostBucket.acquire

    async def counting_acquire(self: politeness._HostBucket) -> None:
        taken.append("token")
        await original(self)

    monkeypatch.setattr(politeness._HostBucket, "acquire", counting_acquire)
    _seen, inner = _edge_pair(403)
    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False, rate_limit_per_minute=60_000),
        inner=inner,
        resolver=lambda _host: ["203.0.113.64", "203.0.113.12"],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://public.test/page")

    assert resp.status_code == 200
    assert len(taken) == 2


@pytest.mark.asyncio
async def test_an_unroutable_first_address_is_walked_past_to_the_second() -> None:
    """A host with an address family the box cannot reach: the pinned first
    address never connects, the second answers, and the log names the fallback."""
    from structlog.testing import capture_logs

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "2001:db8::1":
            raise httpx.ConnectError("All connection attempts failed", request=request)
        return httpx.Response(200, text="page")

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=httpx.MockTransport(handler),
        resolver=lambda _host: ["2001:db8::1", "203.0.113.7"],
    )
    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.post("https://public.test/page", content=b"q=1")

    assert resp.status_code == 200 and seen == ["2001:db8::1", "203.0.113.7"]
    retries = [e for e in logs if e["event"] == "guard_retry_next_address"]
    assert [(e["refused"], e["retried"]) for e in retries] == [("2001:db8::1", "203.0.113.7")]
    assert [e["address"] for e in logs if e["event"] == "guard_connect_failed"] == ["2001:db8::1"]


@pytest.mark.asyncio
async def test_every_address_refusing_the_connection_raises_the_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=httpx.MockTransport(handler),
        resolver=lambda _host: ["2001:db8::1", "2001:db8::2"],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError, match="no route"):
            await client.get("https://public.test/page")


@pytest.mark.asyncio
async def test_a_robots_redirect_to_another_host_is_validated_and_walked_too() -> None:
    """robots.txt answering 301 to a sibling host: the probe follows it through
    the guard, so the new host is resolved and its unroutable address walked."""
    resolved: list[str] = []
    seen: list[str] = []

    def resolver(host: str) -> list[str]:
        resolved.append(host)
        return ["2001:db8::9", "203.0.113.9"] if host == "robots.test" else ["203.0.113.1"]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers["host"], request.url.host))
        if request.url.path == "/robots.txt" and request.headers["host"] == "public.test":
            return httpx.Response(301, headers={"location": "https://robots.test/robots.txt"})
        if request.url.host == "2001:db8::9":
            raise httpx.ConnectError("no route", request=request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text="page")

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=True),
        inner=httpx.MockTransport(handler),
        resolver=resolver,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://public.test/page")

    assert resp.status_code == 200
    assert "robots.test" in resolved
    assert ("robots.test", "2001:db8::9") in seen and ("robots.test", "203.0.113.9") in seen


@pytest.mark.asyncio
async def test_a_connect_timeout_on_the_first_address_is_walked_past() -> None:
    """A routed but dead address family times out rather than refusing; it is walked too."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "2001:db8::1":
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, text="page")

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=httpx.MockTransport(handler),
        resolver=lambda _host: ["2001:db8::1", "203.0.113.7"],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://public.test/page")
    assert resp.status_code == 200 and seen == ["2001:db8::1", "203.0.113.7"]


@pytest.mark.parametrize("error", [httpx.WriteError, httpx.ReadError])
@pytest.mark.asyncio
async def test_a_reset_after_bytes_were_sent_contacts_one_address_and_propagates(
    error: type[httpx.TransportError],
) -> None:
    """A POST that reached the wire is never replayed on another address."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        raise error("reset", request=request)

    transport = PoliteTransport(
        profile=PolitenessProfile(robots_respect=False),
        inner=httpx.MockTransport(handler),
        resolver=lambda _host: ["203.0.113.64", "203.0.113.12"],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(error):
            await client.post("https://public.test/page", content=b"q=1")
    assert seen == ["203.0.113.64"]
