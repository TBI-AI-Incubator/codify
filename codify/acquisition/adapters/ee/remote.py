"""Read a zip archive a server publishes, by HTTP byte range, without downloading it."""

from __future__ import annotations

import io
import re
import struct
import time
import zipfile
import zlib
from dataclasses import dataclass
from types import TracebackType

import httpx

from codify.acquisition.base import PolitenessProfile
from codify.core.security import ssrf_guard

# zipfile's local file header: signature, five shorts, three longs, name and extra lengths.
_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_LOCAL_MAGIC = b"PK\x03\x04"
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


class RangeRefused(OSError):
    """The server never answered with exactly the bytes asked for."""


class ArchiveChanged(OSError):
    """A member is no longer where its location says: the archive was replaced."""


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
        for attempt in range(self._attempts):
            if attempt:
                time.sleep(self._backoff * attempt)
            try:
                with self._client.stream("GET", self.url, headers=headers) as resp:
                    m = _CONTENT_RANGE.fullmatch(resp.headers.get("content-range", ""))
                    sent = resp.headers.get("content-length")
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
        # The length of the bytes the ranges will read, not of an encoded representation.
        head = self._client.head(self.url, headers={"accept-encoding": "identity"})
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
    "MemberLocation",
    "RangeRefused",
    "RemoteFile",
    "http_client",
]
