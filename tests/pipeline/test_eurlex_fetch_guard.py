"""The EU fetcher refuses any hop, redirects included, that resolves to a private address."""

from __future__ import annotations

import httpx
import pytest

from codify.acquisition.base import MissingUserAgent
from codify.core.security import SSRFBlocked
from codify.pipeline.fetchers.eurlex import fetch_url


def _resolver(host: str) -> list[str]:
    return {
        "public.test": ["93.184.216.34"],
        "other-public.test": ["203.0.113.7"],
        "internal.test": ["10.0.0.5"],
    }.get(host, [])


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["http://10.0.0.5/admin", "http://internal.test/admin"])
async def test_a_redirect_to_a_private_address_is_refused(target: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text="leaked")

    with pytest.raises(SSRFBlocked):
        await fetch_url(
            "https://public.test/start", resolver=_resolver, inner=httpx.MockTransport(handler)
        )


@pytest.mark.asyncio
async def test_a_redirect_between_public_hosts_is_followed() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers["host"]))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://other-public.test/final"})
        return httpx.Response(200, text="<FORMEX/>")

    body = await fetch_url(
        "https://public.test/start", resolver=_resolver, inner=httpx.MockTransport(handler)
    )
    assert body == "<FORMEX/>"
    # Each hop connects to its own checked address while the Host header keeps the hostname.
    assert seen == [("93.184.216.34", "public.test"), ("203.0.113.7", "other-public.test")]


@pytest.mark.asyncio
async def test_the_first_hop_is_guarded_too() -> None:
    with pytest.raises(SSRFBlocked):
        await fetch_url(
            "http://internal.test/x",
            resolver=_resolver,
            inner=httpx.MockTransport(lambda r: httpx.Response(200)),
        )


@pytest.mark.asyncio
async def test_the_callers_user_agent_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACQUISITION_USER_AGENT", "eu-test/1 (+https://example.test)")
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["user-agent"])
        return httpx.Response(200, text="<FORMEX/>")

    await fetch_url("https://public.test/a", resolver=_resolver, inner=httpx.MockTransport(handler))
    assert sent == ["eu-test/1 (+https://example.test)"]


@pytest.mark.asyncio
async def test_no_request_is_sent_without_a_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACQUISITION_USER_AGENT")
    sent: list[httpx.Request] = []
    with pytest.raises(MissingUserAgent):
        await fetch_url(
            "https://public.test/a",
            resolver=_resolver,
            inner=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)),
        )
    assert sent == []


@pytest.mark.asyncio
async def test_other_headers_do_not_drop_the_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACQUISITION_USER_AGENT", "eu-test/1 (+https://example.test)")
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, text="<FORMEX/>")

    await fetch_url(
        "https://public.test/a",
        headers={"Accept": "application/xml"},
        resolver=_resolver,
        inner=httpx.MockTransport(handler),
    )
    assert sent[0].headers["user-agent"] == "eu-test/1 (+https://example.test)"
    assert sent[0].headers["accept"] == "application/xml"


@pytest.mark.asyncio
async def test_an_explicit_user_agent_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACQUISITION_USER_AGENT", raising=False)
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["user-agent"])
        return httpx.Response(200, text="<FORMEX/>")

    await fetch_url(
        "https://public.test/a",
        headers={"user-agent": "explicit/2"},
        resolver=_resolver,
        inner=httpx.MockTransport(handler),
    )
    assert sent == ["explicit/2"]
