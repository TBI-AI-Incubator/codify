"""SSRF guard: refuse outbound requests targeting private/loopback/metadata addresses."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlparse

_BLOCKED_NETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)

_BLOCKED_HOSTS: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.com",
        "metadata.amazonaws.com",
    }
)


class SSRFBlocked(Exception):
    """Raised when a request would resolve to a private/metadata address."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"SSRF blocked: {reason} ({url})")
        self.url = url
        self.reason = reason


class Resolver(Protocol):
    def __call__(self, host: str) -> Sequence[str]: ...


_DNS_NOT_FOUND = (
    {socket.EAI_NONAME, socket.EAI_NODATA} if hasattr(socket, "EAI_NODATA") else {socket.EAI_NONAME}
)


def _default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if exc.errno in _DNS_NOT_FOUND:
            return []
        raise SSRFBlocked(host, f"DNS lookup failed (errno={exc.errno})") from exc
    return list({str(info[4][0]) for info in infos})


def safe_addresses(url: str, *, resolver: Resolver = _default_resolver) -> list[str]:
    """Every address `url` resolves to, once, provided all of them are public.

    Returns the addresses so a caller can connect to one it has validated.
    Validating a hostname and then letting the socket layer resolve it again is
    a rebinding hole: the second answer is free to differ from the first.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise SSRFBlocked(url, "missing host")
    if host in _BLOCKED_HOSTS:
        raise SSRFBlocked(url, f"blocked host {host}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses: Sequence[str] = [str(literal)] if literal is not None else resolver(host)
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        for net in _BLOCKED_NETS:
            if ip in net:
                raise SSRFBlocked(url, f"{addr} in {net}")
    return list(addresses)


def ssrf_guard(url: str, *, resolver: Resolver = _default_resolver) -> None:
    """Refuse private / loopback / link-local / metadata addresses before fetch.

    Check-only: prefer `safe_addresses` where the caller controls the socket,
    since only pinning the validated address closes the rebinding window.
    """
    safe_addresses(url, resolver=resolver)


__all__ = ["Resolver", "SSRFBlocked", "safe_addresses", "ssrf_guard"]
