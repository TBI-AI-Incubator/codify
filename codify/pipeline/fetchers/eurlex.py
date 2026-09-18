"""EUR-Lex / Publications Office URL fetcher."""

from __future__ import annotations

import httpx

from codify.acquisition.politeness import GuardedTransport, client_tls_context
from codify.core.security import Resolver

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_USER_AGENT = "codify-fetch/0.1"


async def fetch_url(
    url: str,
    *,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    resolver: Resolver | None = None,
    inner: httpx.AsyncBaseTransport | None = None,
) -> str:
    """GET a URL and return the body. Raises on non-2xx or on any hop to a private address."""
    # Every hop, redirects included, is resolved by the guard and pinned to the address it checked.
    transport = GuardedTransport(
        inner or httpx.AsyncHTTPTransport(verify=client_tls_context()), resolver=resolver
    )
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, transport=transport
    ) as client:
        response = await client.get(url, headers=headers or {"User-Agent": _USER_AGENT})
        response.raise_for_status()
        return response.text
