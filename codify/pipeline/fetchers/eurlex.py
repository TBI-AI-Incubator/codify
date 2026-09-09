"""EUR-Lex / Publications Office URL fetcher."""

from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_USER_AGENT = "codify-fetch/0.1"


async def fetch_url(
    url: str,
    *,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> str:
    """GET a URL and return the body. Raises on non-2xx."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers or {"User-Agent": _USER_AGENT})
        response.raise_for_status()
        return response.text
