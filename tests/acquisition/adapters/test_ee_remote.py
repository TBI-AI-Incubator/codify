"""Reading a published zip by byte range, against a server stood in by a transport."""

from __future__ import annotations

import base64
import io
import random
import re
import time
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest

import codify.acquisition.adapters  # noqa: F401
from codify.acquisition import DocumentRef
from codify.acquisition.adapters.ee import remote
from codify.acquisition.adapters.ee.datadump import (
    EeDatadumpAcquirer,
    TooManyMembers,
    build_ee_index,
    index_remote_ee_archive,
)
from codify.acquisition.adapters.ee.remote import (
    ArchiveChanged,
    MemberLocation,
    RangeRefused,
    RemoteFile,
)
from codify.core.security import SSRFBlocked
from codify.jurisdictions import SourceAdapter
from tests.acquisition.adapters.test_ee_datadump import _member

_URL = "https://archive.example/xml.zip"
_STAMP = "Sun, 18 Oct 2026 11:48:02 GMT"
_RANGE = re.compile(r"bytes=(\d+)-(\d+)")


class _Unread(httpx.SyncByteStream):
    """A body that must never be read."""

    def __iter__(self) -> Iterator[bytes]:
        raise AssertionError("a refused answer's body was read")


class _Server:
    """Serves one file by range. `ignore` answers that many reads with a whole-file 200."""

    def __init__(self, data: bytes, *, ignore: int = 0) -> None:
        self.data = data
        self.ignore = ignore
        self.ranges: list[tuple[int, int, str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        size = len(self.data)
        if request.method == "HEAD":
            return httpx.Response(
                200, headers={"content-length": str(size), "last-modified": _STAMP}
            )
        m = _RANGE.fullmatch(request.headers.get("range", ""))
        assert m is not None, "every read names a range"
        start, end = int(m.group(1)), min(int(m.group(2)), size - 1)
        self.ranges.append((start, end, request.headers.get("if-range")))
        if self.ignore:
            self.ignore -= 1
            return httpx.Response(200, headers={"content-length": str(size)}, stream=_Unread())
        return httpx.Response(
            206,
            headers={"content-range": f"bytes {start}-{end}/{size}"},
            content=self.data[start : end + 1],
        )


def _zip(members: dict[str, tuple[bytes, tuple[int, int, int]]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, (data, day) in members.items():
            info = zipfile.ZipInfo(name, date_time=(*day, 12, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def _remote(server: _Server, attempts: int = 6) -> RemoteFile:
    return RemoteFile(_URL, client=_client(server), attempts=attempts, backoff=0)


def _members() -> dict[str, tuple[bytes, tuple[int, int, int]]]:
    return {
        "000000000101.xml": (_member("101"), (2026, 10, 2)),
        "000000000102.xml": (_member("102", issuer="Näidise Vallavalitsus"), (2026, 10, 5)),
        "000000000103.xml": (_member("103", issuer="Riigikogu", kind="seadus"), (2026, 9, 1)),
    }


def _member_reads(data: bytes, server: _Server) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        offsets = {i.header_offset: i.filename for i in zf.infolist()}
    return {offsets[r[0]] for r in server.ranges if r[0] in offsets}


# --- the reader ----------------------------------------------------------------------


def test_the_directory_and_each_member_round_trip() -> None:
    members = _members()
    with _remote(_Server(_zip(members))) as f:
        infos = f.directory()
        assert [i.filename for i in infos] == list(members)
        for info in infos:
            assert f.member(MemberLocation.of(info)) == members[info.filename][0]


def test_reads_after_the_directory_name_its_date_so_a_replaced_file_is_never_mixed() -> None:
    server = _Server(_zip(_members()))
    with _remote(server) as f:
        f.directory()
    assert server.ranges and {r[2] for r in server.ranges} == {_STAMP}


def test_a_whole_file_answer_is_closed_unread_and_the_range_asked_again() -> None:
    data = _zip(_members())
    server = _Server(data, ignore=2)
    with _remote(server) as f:
        assert f.read(10, 20) == data[10:30]
    assert [r[:2] for r in server.ranges] == [(10, 29)] * 3


def test_a_server_that_never_answers_a_range_is_refused_by_name() -> None:
    with pytest.raises(RangeRefused, match="status 200 after 3 tries"):
        _remote(_Server(_zip(_members()), ignore=99), attempts=3).read(0, 10)


def test_only_a_206_is_taken_even_when_a_200_names_the_range() -> None:
    def ok_with_range(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-range": "bytes 0-9/99"}, content=b"x" * 10)

    f = RemoteFile(_URL, client=_client(ok_with_range), attempts=2, backoff=0)
    with pytest.raises(RangeRefused, match="status 200"):
        f.read(0, 10)


def test_another_range_than_the_one_asked_is_refused() -> None:
    def from_the_start(request: httpx.Request) -> httpx.Response:
        return httpx.Response(206, headers={"content-range": "bytes 0-9/99"}, content=b"x" * 10)

    f = RemoteFile(_URL, client=_client(from_the_start), attempts=2, backoff=0)
    with pytest.raises(RangeRefused, match="range 0-9"):
        f.read(5, 10)


def test_an_answer_announcing_another_length_is_closed_unread() -> None:
    def longer(request: httpx.Request) -> httpx.Response:
        headers = {"content-range": "bytes 0-9/99", "content-length": "99"}
        return httpx.Response(206, headers=headers, stream=_Unread())

    f = RemoteFile(_URL, client=_client(longer), attempts=2, backoff=0)
    with pytest.raises(RangeRefused, match="99 bytes announced for 10"):
        f.read(0, 10)


class _Short(httpx.SyncByteStream):
    """Five bytes with no length announced."""

    def __iter__(self) -> Iterator[bytes]:
        yield b"x" * 5


def test_a_short_answer_is_asked_again() -> None:
    answers = iter([False, True])

    def short_then_whole(request: httpx.Request) -> httpx.Response:
        headers = {"content-range": "bytes 0-9/99"}
        if next(answers):
            return httpx.Response(206, headers=headers, content=b"y" * 10)
        return httpx.Response(206, headers=headers, stream=_Short())

    f = RemoteFile(_URL, client=_client(short_then_whole), backoff=0)
    assert f.read(0, 10) == b"y" * 10


def test_a_member_no_longer_where_its_location_says_is_named() -> None:
    data = bytearray(_zip(_members()))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        first = MemberLocation.of(zf.infolist()[0])
    data[first.offset : first.offset + 4] = b"XXXX"  # the signature alone
    with _remote(_Server(bytes(data))) as f:
        with pytest.raises(ArchiveChanged, match="not at offset"):
            f.member(first)
    with _remote(_Server(_zip(_members()))) as f:
        loc = MemberLocation.of(f.directory()[1])
        with pytest.raises(ArchiveChanged, match="not at offset"):
            f.member(MemberLocation(loc.name, loc.offset + 1, loc.size, loc.crc, loc.method))
        with pytest.raises(ArchiveChanged, match="CRC"):
            f.member(MemberLocation(loc.name, loc.offset, loc.size, loc.crc ^ 1, loc.method))


def test_a_member_that_does_not_inflate_is_named() -> None:
    data = bytearray(_zip(_members()))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        loc = MemberLocation.of(zf.infolist()[0])
    start = loc.offset + 30 + len(loc.name) + 4
    data[start : start + 8] = b"\xff" * 8
    with _remote(_Server(bytes(data))) as f:
        with pytest.raises(ArchiveChanged, match="does not inflate"):
            f.member(loc)


def test_a_head_is_one_bounded_read_cut_at_the_title() -> None:
    # Incompressible padding, so the member is longer than the head.
    body = _member("601") + b"<!--" + random.Random(0).randbytes(200_000) + b"-->"
    server = _Server(_zip({"000000000601.xml": (body, (2026, 10, 2))}))
    with _remote(server) as f:
        loc = MemberLocation.of(f.directory()[0])
        server.ranges.clear()
        head = f.member_head(loc, until=b"</aktinimi>", limit=4096)
    assert body.startswith(head) and head.endswith(b"</aktinimi>")
    data_reads = [r for r in server.ranges if r[0] != loc.offset]
    assert loc.size > 4096 and len(data_reads) == 1
    assert data_reads[0][1] - data_reads[0][0] + 1 == 4096


def test_a_url_to_a_private_address_is_refused_before_any_request() -> None:
    with pytest.raises(SSRFBlocked):
        RemoteFile("http://127.0.0.1/xml.zip")


# --- the remote index --------------------------------------------------------------


def _index(data: bytes, since: date, **kwargs: object) -> dict[str, object]:
    server = _Server(data)
    return index_remote_ee_archive(_URL, modified_since=since, client=_client(server), **kwargs)  # type: ignore[arg-type]


def test_the_remote_index_reads_only_members_changed_since_and_keeps_the_state_ones() -> None:
    data = _zip(_members())
    server = _Server(data)
    payload = index_remote_ee_archive(
        _URL, modified_since=date(2026, 10, 1), client=_client(server)
    )
    assert payload["stats"] == {"files_scanned": 2, "indexed": 1, "skipped": 1}
    assert list(payload["entries"]) == ["101"]
    assert _member_reads(data, server) == {"000000000101.xml", "000000000102.xml"}


def test_the_remote_index_refuses_more_changed_members_than_asked_before_reading_them() -> None:
    data = _zip(_members())
    server = _Server(data)
    with pytest.raises(TooManyMembers, match="2 members changed since 2026-10-01"):
        index_remote_ee_archive(
            _URL, modified_since=date(2026, 10, 1), max_members=1, client=_client(server)
        )
    assert server.ranges and _member_reads(data, server) == set()


def test_the_remote_index_agrees_with_the_local_walk_past_a_long_head(tmp_path: Path) -> None:
    """Text after the title never reaches the index, whichever path read the head."""
    padding = base64.b64encode(random.Random(1).randbytes(30_000))
    long_head = (
        _member("701")
        .replace(b"\t</metaandmed>", b"\t<!--" + padding + b"-->\n\t</metaandmed>")
        .replace(b"</sisu>", b"<lyhend>PS</lyhend></sisu>")
    )
    members = {**_members(), "000000000701.xml": (long_head, (2026, 10, 2))}
    archive = tmp_path / "xml.zip"
    archive.write_bytes(_zip(members))
    entries = _index(archive.read_bytes(), date(2000, 1, 1))["entries"]
    assert {k: {f: v for f, v in e.items() if f != "location"} for k, e in entries.items()} == (  # type: ignore[union-attr]
        build_ee_index(archive)["entries"]
    )


def test_a_member_with_an_unset_date_is_passed_over_rather_than_failing_the_index() -> None:
    data = _zip({**_members(), "000000000104.xml": (_member("104"), (2026, 0, 0))})
    assert sorted(_index(data, date(2026, 1, 1))["entries"]) == ["101", "103"]  # type: ignore[arg-type]


def test_a_member_with_an_unreadable_end_date_is_skipped_as_the_local_walk_skips_it(
    tmp_path: Path,
) -> None:
    bad = _member("105", end="kehtiv")
    data = _zip({**_members(), "000000000105.xml": (bad, (2026, 10, 3))})
    archive = tmp_path / "xml.zip"
    archive.write_bytes(data)
    assert "105" not in _index(data, date(2026, 10, 1))["entries"]  # type: ignore[operator]
    assert "105" not in build_ee_index(archive)["entries"]


def test_a_member_dated_only_by_publication_is_dated_alike_on_both_paths(tmp_path: Path) -> None:
    published = (
        _member("106")
        .replace(b"\t\t\t<aktikuupaev>2026-09-28</aktikuupaev>\n", b"")
        .replace(b"<kehtivuseAlgus>2026-10-04+03:00</kehtivuseAlgus>", b"")
    )
    data = _zip({"000000000106.xml": (published, (2026, 10, 3))})
    archive = tmp_path / "xml.zip"
    archive.write_bytes(data)
    for entry in (
        _index(data, date(2026, 10, 1))["entries"]["106"],
        build_ee_index(archive)["entries"]["106"],
    ):  # type: ignore[index]
        assert (entry["expression_date"], entry["year"]) == ("2026-10-01", 2026)


def test_a_failure_behind_a_slow_member_stops_the_rest() -> None:
    members = {f"{n:012d}.xml": (_member(str(n)), (2026, 10, 2)) for n in range(1, 21)}
    data = bytearray(_zip(members))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        infos = zf.infolist()
    second = infos[1].header_offset
    data[second + 30 : second + 42] = b"x" * 12  # the second member's name
    server = _Server(bytes(data))
    first = infos[0].header_offset

    def slow_first(request: httpx.Request) -> httpx.Response:
        if request.headers.get("range", "").startswith(f"bytes={first}-"):
            time.sleep(1.5)
        return server(request)

    with pytest.raises(ArchiveChanged):
        index_remote_ee_archive(
            _URL, modified_since=date(2026, 10, 1), workers=2, client=_client(slow_first)
        )
    assert len(_member_reads(bytes(data), server)) < 8


@pytest.mark.asyncio
async def test_a_located_member_is_fetched_from_the_archive_without_an_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    members = _members()
    server = _Server(_zip(members))
    monkeypatch.setattr(remote, "http_client", lambda: _client(server))
    monkeypatch.setattr(
        remote, "ssrf_guard", lambda url: None
    )  # the stand-in host resolves nowhere
    entry = index_remote_ee_archive(_URL, modified_since=date(2026, 10, 1))["entries"]["101"]
    acquirer = EeDatadumpAcquirer(
        SourceAdapter(kind="bulk_xml_archive", name="riigi_teataja_archive"),
        index_path=tmp_path / "absent.json",
    )
    ref = DocumentRef(
        jurisdiction_code="ee",
        doctype=entry["doctype"],
        year=entry["year"],
        number="101",
        extra={"archive": _URL, **entry["location"]},
    )
    doc = await acquirer.fetch(ref)
    assert doc.bodies[0].content == members["000000000101.xml"][0]
    assert doc.source_url == "https://www.riigiteataja.ee/akt/101.xml"
    assert doc.frbr_work_uri.endswith("/2026/naidistoetuse-andmise-tingimused-ja-kord")
