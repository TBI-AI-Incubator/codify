"""Read a zip archive a server publishes, by HTTP byte range, without downloading it."""

from __future__ import annotations

import asyncio
import io
import re
import struct
import threading
import time
import zipfile
import zlib
from dataclasses import dataclass
from types import TracebackType

import httpx

from codify.acquisition.base import PolitenessProfile
from codify.acquisition.politeness import PoliteTransport, client_tls_context
from codify.core.security import ssrf_guard

# zipfile's local file header: signature, five shorts, three longs, name and extra lengths.
_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_LOCAL_MAGIC = b"PK\x03\x04"
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


class RangeRefused(OSError):
    """The server never answered with exactly the bytes asked for."""


class ArchiveChanged(OSError):
    """A member is no longer where its location says: the archive was replaced."""


class ArchiveRefused(Exception):
    """The host answered 429, 403 or a 5xx. Never retried: the caller stops and waits.
    Not an OSError, which zipfile would turn into BadZipFile."""

    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"{url} answered {status}")
        self.status = status


def refused(status: int) -> bool:
    return status in (429, 403) or status >= 500


_STOP_HOLD_S = 900.0
_STOPPED: tuple[ArchiveRefused, float] | None = None


def archive_refusal() -> ArchiveRefused | None:
    """The refusal holding archive reads in this process, until it lapses or is reset."""
    if _STOPPED is not None and time.monotonic() < _STOPPED[1]:
        return _STOPPED[0]
    return None


def reset_archive_refusal() -> None:
    global _STOPPED
    _STOPPED = None


def _refuse(url: str, status: int) -> ArchiveRefused:
    """The first refusal holds every later read in the process for fifteen minutes, so a
    batch in flight sends nothing more to a host that has refused."""
    global _STOPPED
    held = archive_refusal()
    if held is None:
        _STOPPED = (ArchiveRefused(url, status), time.monotonic() + _STOP_HOLD_S)
        held = _STOPPED[0]
    return held


def _raise_if_stopped() -> None:
    if (held := archive_refusal()) is not None:
        raise held


@dataclass(frozen=True)
class MemberLocation:
    """Where one member's bytes sit, as the central directory records them."""

    name: str
    offset: int
    size: int  # compressed
    crc: int
    method: int

    @classmethod
    def of(cls, info: zipfile.ZipInfo) -> MemberLocation:
        return cls(
            info.filename, info.header_offset, info.compress_size, info.CRC, info.compress_type
        )

    def to_extra(self) -> dict[str, str]:
        return {
            "member": self.name,
            "offset": str(self.offset),
            "size": str(self.size),
            "crc": str(self.crc),
            "method": str(self.method),
        }

    @classmethod
    def from_extra(cls, extra: dict[str, str]) -> MemberLocation:
        return cls(
            extra["member"],
            int(extra["offset"]),
            int(extra["size"]),
            int(extra["crc"]),
            int(extra["method"]),
        )


class _StopOnRefusal(httpx.AsyncBaseTransport):
    """Under the polite transport, so its own backoff never retries a refusal."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # After the polite transport's waits, so a refusal recorded meanwhile holds this one back.
        _raise_if_stopped()
        response = await self._inner.handle_async_request(request)
        if refused(response.status_code):
            await response.aclose()
            raise _refuse(str(request.url), response.status_code)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


_RANGE_HEADER = re.compile(r"bytes=(\d+)-(\d+)")
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_LOCK = threading.Lock()


def _polite_loop() -> asyncio.AbstractEventLoop:
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None:
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, daemon=True, name="polite-archive").start()
        return _LOOP


class _PoliteBridge(httpx.BaseTransport):
    """Sends a sync client's requests through the polite transport on a background loop,
    so robots.txt, the host's pace and the concurrency cap apply to range reads."""

    def __init__(self, transport: PoliteTransport) -> None:
        self._sem = asyncio.Semaphore(transport.profile.concurrency)
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        asked = _RANGE_HEADER.fullmatch(request.headers.get("range", ""))
        wanted = int(asked.group(2)) - int(asked.group(1)) + 1 if asked else None

        async def send() -> httpx.Response:
            # The permit is held until the body is read or dropped, not only to the headers.
            async with self._sem:
                response = await self._client.send(
                    self._client.build_request(
                        request.method, str(request.url), headers=request.headers
                    ),
                    stream=True,
                )
                try:
                    announced = response.headers.get("content-length")
                    if (
                        request.method == "HEAD"
                        or response.status_code != 206
                        or (announced is not None and int(announced) != wanted)
                    ):
                        # Anything but the exact range asked for is rejected by the reader
                        # from its headers; its body is never downloaded.
                        return httpx.Response(response.status_code, headers=response.headers)
                    if announced is not None:
                        body = bytearray(await response.aread())
                    else:
                        # Unannounced (chunked): read to one byte past the range, which the
                        # reader's own size check then refuses.
                        body = bytearray()
                        async for chunk in response.aiter_raw():
                            body += chunk
                            if len(body) > (wanted or 0):
                                break
                    return httpx.Response(
                        response.status_code, headers=response.headers, content=bytes(body)
                    )
                finally:
                    await response.aclose()

        return asyncio.run_coroutine_threadsafe(send(), _polite_loop()).result()

    def close(self) -> None:
        asyncio.run_coroutine_threadsafe(self._client.aclose(), _polite_loop()).result()


_CLIENTS: dict[str, httpx.Client] = {}


def polite_http_client(profile: PolitenessProfile) -> httpx.Client:
    """One sync client per profile for the process, so the concurrency cap and the host's
    pace hold across every caller; no redirect is followed."""
    key = profile.model_dump_json()
    with _LOOP_LOCK:
        if key not in _CLIENTS:
            inner = _StopOnRefusal(httpx.AsyncHTTPTransport(verify=client_tls_context()))
            _CLIENTS[key] = httpx.Client(
                transport=_PoliteBridge(PoliteTransport(profile=profile, inner=inner)),
                follow_redirects=False,
            )
        return _CLIENTS[key]


def http_client() -> httpx.Client:
    """A client with the acquisition user agent that follows no redirect."""
    return httpx.Client(
        headers={"user-agent": PolitenessProfile().user_agent},
        timeout=httpx.Timeout(120.0, connect=10.0),
        follow_redirects=False,
    )


class RemoteFile:
    """Exact byte ranges of one published file, and the zip members inside it."""

    def __init__(
        self,
        url: str,
        *,
        client: httpx.Client | None = None,
        attempts: int = 6,
        backoff: float = 1.0,
    ) -> None:
        if client is None:
            ssrf_guard(url)  # a caller's own client is its own concern
        self.url = url
        self._owned = client is None
        self._client = client or http_client()
        self._attempts = attempts
        self._backoff = backoff
        self._validator: str | None = None
        self.size: int | None = None

    def __enter__(self) -> RemoteFile:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def read(self, start: int, length: int) -> bytes:
        """Exactly `length` bytes from `start`. Any answer but a 206 of that range and
        length, such as a 200 starting the whole file, is closed unread and retried."""
        end = start + length - 1
        headers = {"range": f"bytes={start}-{end}", "accept-encoding": "identity"}
        if self._validator:
            # A replaced file then answers 200, so two versions are never mixed.
            headers["if-range"] = self._validator
        last = "no answer"
        _raise_if_stopped()
        for attempt in range(self._attempts):
            if attempt:
                time.sleep(self._backoff * attempt)
            try:
                with self._client.stream("GET", self.url, headers=headers) as resp:
                    m = _CONTENT_RANGE.fullmatch(resp.headers.get("content-range", ""))
                    sent = resp.headers.get("content-length")
                    if refused(resp.status_code):
                        raise _refuse(self.url, resp.status_code)
                    if resp.status_code != 206 or m is None:
                        last = f"status {resp.status_code}"
                    elif (int(m.group(1)), int(m.group(2))) != (start, end):
                        last = f"range {m.group(1)}-{m.group(2)}"
                    elif sent is not None and int(sent) != length:
                        last = f"{sent} bytes announced for {length}"
                    else:
                        body = resp.read()
                        if len(body) == length:
                            return body
                        last = f"{len(body)} bytes for {length}"
            except httpx.TransportError as exc:
                last = type(exc).__name__
        raise RangeRefused(f"{self.url} bytes {start}-{end}: {last} after {self._attempts} tries")

    def directory(self) -> list[zipfile.ZipInfo]:
        """The archive's members, from its central directory alone."""
        _raise_if_stopped()
        # The length of the bytes the ranges will read, not of an encoded representation.
        head = self._client.head(self.url, headers={"accept-encoding": "identity"})
        if refused(head.status_code):
            raise _refuse(self.url, head.status_code)
        head.raise_for_status()
        self.size = int(head.headers["content-length"])
        self._validator = head.headers.get("last-modified")
        reader = io.BufferedReader(_RangeIO(self), buffer_size=1 << 20)
        with zipfile.ZipFile(reader) as zf:
            return zf.infolist()

    def member(self, loc: MemberLocation) -> bytes:
        """One member's bytes, checked against the CRC its location records."""
        raw = self._inflate(loc, self.read(self._data_start(loc), loc.size), None)
        if zlib.crc32(raw) != loc.crc:
            raise ArchiveChanged(f"{loc.name} does not match its recorded CRC")
        return raw

    def member_prefix(self, loc: MemberLocation, *, limit: int) -> bytes:
        """A member's first `limit` bytes, from one read of at most `limit` stored bytes."""
        data = self.read(self._data_start(loc), min(loc.size, limit))
        return self._inflate(loc, data, limit)[:limit]

    def _data_start(self, loc: MemberLocation) -> int:
        """Where the member's data begins, after the local header it records."""
        name = loc.name.encode("utf-8")
        header = self.read(loc.offset, _LOCAL_HEADER.size + len(name))
        fields = _LOCAL_HEADER.unpack_from(header)
        name_len, extra_len = int(fields[-2]), int(fields[-1])
        if (
            fields[0] != _LOCAL_MAGIC
            or header[_LOCAL_HEADER.size :] != name
            or name_len != len(name)
        ):
            raise ArchiveChanged(f"{loc.name} is not at offset {loc.offset}")
        return loc.offset + _LOCAL_HEADER.size + name_len + extra_len

    @staticmethod
    def _inflate(loc: MemberLocation, data: bytes, limit: int | None) -> bytes:
        if loc.method == zipfile.ZIP_STORED:
            return data
        if loc.method != zipfile.ZIP_DEFLATED:
            raise zipfile.BadZipFile(f"compression method {loc.method} is not read")
        try:
            # A head is a prefix: inflate only as much of it as is asked for.
            return zlib.decompressobj(-15).decompress(data, limit or 0)
        except zlib.error as exc:
            raise ArchiveChanged(f"{loc.name} does not inflate: {exc}") from exc


class _RangeIO(io.RawIOBase):
    """A seekable read-only view of a RemoteFile, for zipfile's directory read."""

    def __init__(self, remote: RemoteFile) -> None:
        assert remote.size is not None
        self._remote = remote
        self._size = remote.size
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = base + offset
        return self._pos

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        n = min(len(buffer), self._size - self._pos)
        if n <= 0:
            return 0
        data = self._remote.read(self._pos, n)
        buffer[: len(data)] = data
        self._pos += len(data)
        return len(data)


__all__ = [
    "ArchiveChanged",
    "ArchiveRefused",
    "archive_refusal",
    "MemberLocation",
    "RangeRefused",
    "RemoteFile",
    "http_client",
    "polite_http_client",
    "refused",
    "reset_archive_refusal",
]
