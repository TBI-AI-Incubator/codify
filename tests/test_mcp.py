"""The MCP server is one tool per library read; these pin the tool set, each tool's
shape over faked library calls, and the errors a client reads back."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, redirect_stdout
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from codify.cli import main
from codify.storage.models import Law, Version

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402

from codify.mcp import TOOL_NAMES, create_server  # noqa: E402, needs the mcp extra

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-atlantis-1992.akn.xml"
OTHER_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-zerzura-1994.akn.xml"
_DIM = 768


@asynccontextmanager
async def _no_session() -> AsyncIterator[None]:
    yield None  # every library call below is faked; nothing reads the session


class _UnitVectors:
    model = "test-model"

    async def embed_documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
        return [[float(j == i % _DIM) for j in range(_DIM)] for i in range(len(items))]

    async def embed_one(self, text: str, **_: object) -> list[float]:
        return [1.0] + [0.0] * (_DIM - 1)


class _GapLlm:
    async def chat_json(self, prompt: str, **_: object) -> dict[str, object]:
        return {
            "verdict": "gap",
            "confidence": 0.9,
            "note": "n",
            "citations": [],
            "actionable": True,
            "provision_kind": "obligation",
            "clause_method": "normal",
        }


def _version(law_id: uuid.UUID, xml: str = "<akomaNtoso/>") -> Version:
    return Version(
        law_id=law_id,
        expression_uri=f"/akn/xa/act/1992/7/eng@1992-01-01/{uuid.uuid4().hex[:4]}",
        language="eng",
        expression_date=date(1992, 1, 1),
        akn_xml=xml,
        structural_quality_grade="clean",
    )


def _law() -> Law:
    return Law(
        jurisdiction_id=uuid.uuid4(),
        title="Records Act",
        doctype="act",
        year=1992,
        number="7",
        frbr_work_uri="/akn/xa/act/1992/7",
    )


async def _call(server: Any, name: str, **arguments: Any) -> Any:
    async with Client(server) as c:
        return await c.call_tool(name, arguments)


async def test_the_tool_set_is_exactly_the_reads() -> None:
    async with Client(create_server(sessions=_no_session)) as c:
        listed = {t.name for t in (await c.list_tools()).tools}

    expected = {
        "search_provisions",
        "list_laws",
        "get_law",
        "get_version",
        "list_jurisdictions",
        "get_jurisdiction",
        "compare_versions",
    }
    assert listed == expected
    assert TOOL_NAMES == expected


async def test_every_tool_says_what_it_returns() -> None:
    async with Client(create_server(sessions=_no_session)) as c:
        tools = (await c.list_tools()).tools
    for tool in tools:
        assert tool.description and "Returns" in tool.description, tool.name


async def test_search_without_an_embeddings_endpoint_is_a_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("EMBEDDING_BASE_URL", "LITELLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    result = await _call(
        create_server(sessions=_no_session), "search_provisions", query="x", jurisdiction="xa"
    )

    assert result.is_error
    assert "EMBEDDING_BASE_URL" in result.content[0].text


async def test_search_returns_matches_best_first(monkeypatch: pytest.MonkeyPatch) -> None:
    import codify.retrieve.hybrid as hybrid
    from codify.retrieve.hybrid import ProvisionMatch

    seen: dict[str, Any] = {}
    pid = uuid.uuid4()

    async def fake_retrieve(session: Any, query: str, **kw: Any) -> list[ProvisionMatch]:
        seen.update(kw, query=query)
        return [ProvisionMatch(provision_id=pid, rrf_score=0.5, text="t", akn_eid="sec_1")]

    monkeypatch.setattr(hybrid, "retrieve", fake_retrieve)

    result = await _call(
        create_server(sessions=_no_session, embedding_client=_UnitVectors),
        "search_provisions",
        query="access",
        jurisdiction=" XA ",
        language="eng",
        k=3,
    )

    assert not result.is_error
    assert result.structured_content == {
        "query": "access",
        "matches": [{"provision_id": str(pid), "eid": "sec_1", "score": 0.5, "text": "t"}],
    }
    assert (seen["jurisdiction_code"], seen["language"], seen["k"]) == ("xa", "eng", 3)
    assert isinstance(seen["embedding_client"], _UnitVectors)


async def test_an_unknown_jurisdiction_is_refused_before_any_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As the HTTP routes: a code with no config is an error, not an empty answer."""
    import codify.retrieve.hybrid as hybrid
    import codify.storage

    async def never(*_: Any, **__: Any) -> Any:
        raise AssertionError("the store was read for an unknown jurisdiction")

    monkeypatch.setattr(hybrid, "retrieve", never)
    monkeypatch.setattr(codify.storage, "list_laws", never)
    server = create_server(sessions=_no_session, embedding_client=_UnitVectors)

    search = await _call(server, "search_provisions", query="x", jurisdiction="zz")
    laws = await _call(server, "list_laws", jurisdiction="zz")

    assert search.is_error and "no jurisdiction config for 'zz'" in search.content[0].text
    assert laws.is_error and "no jurisdiction config for 'zz'" in laws.content[0].text


async def test_out_of_range_k_limit_and_offset_are_refused() -> None:
    server = create_server(sessions=_no_session, embedding_client=_UnitVectors)
    for name, key, value in (
        ("search_provisions", "k", 0),
        ("search_provisions", "k", 101),
        ("list_laws", "limit", 0),
        ("list_laws", "limit", 501),
        ("list_laws", "offset", -1),
    ):
        args = {"query": "x", "jurisdiction": "xa", key: value}
        result = await _call(server, name, **args)
        # A crash on the faked session is also an error; only a refusal names the argument.
        text = result.content[0].text
        assert result.is_error and "validation error" in text and key in text, (name, args, text)


async def test_list_laws_pages_the_summaries(monkeypatch: pytest.MonkeyPatch) -> None:
    import codify.storage
    from codify.storage.laws import LawSummary

    seen: dict[str, Any] = {}
    row = LawSummary(
        id=uuid.uuid4(),
        jurisdiction_code="xa",
        title="Records Act",
        doctype="act",
        status="enacted",
        year=1992,
        number="7",
        frbr_work_uri="/akn/xa/act/1992/7",
        latest_expression_date=date(1992, 1, 1),
    )

    async def fake_list_laws(session: Any, **kw: Any) -> tuple[list[LawSummary], None]:
        seen.update(kw)
        return [row], None

    monkeypatch.setattr(codify.storage, "list_laws", fake_list_laws)

    result = await _call(
        create_server(sessions=_no_session),
        "list_laws",
        jurisdiction="XA",
        doctype="act",
        year=1992,
        q="records",
        limit=5,
        offset=10,
    )

    assert not result.is_error
    assert result.structured_content == {
        "items": [row.model_dump(mode="json")],
        "limit": 5,
        "offset": 10,
    }
    assert seen == {
        "jurisdictions": ["xa"],
        "doctype": "act",
        "year": 1992,
        "q": "records",
        "limit": 5,
        "offset": 10,
    }


async def test_get_law_carries_its_versions_without_xml(monkeypatch: pytest.MonkeyPatch) -> None:
    import codify.storage
    import codify.storage.laws as laws

    law = _law()
    version = _version(law.id)
    asked: list[dict[str, Any]] = []

    async def fake_get(session: Any, law_id: uuid.UUID) -> tuple[Law, str] | None:
        return (law, "xa") if law_id == law.id else None

    async def fake_versions(
        session: Any, law_id: uuid.UUID, **kw: Any
    ) -> tuple[list[Version], None]:
        asked.append(kw)
        return [version], None

    monkeypatch.setattr(laws, "get_law_with_jurisdiction", fake_get)
    monkeypatch.setattr(codify.storage, "list_versions", fake_versions)
    server = create_server(sessions=_no_session)

    result = await _call(server, "get_law", law_id=str(law.id))
    missing = await _call(server, "get_law", law_id=str(uuid.uuid4()))
    malformed = await _call(server, "get_law", law_id="not-an-id")

    assert not result.is_error
    out = result.structured_content
    assert (out["title"], out["year"], out["number"], out["jurisdiction"]) == (
        "Records Act",
        1992,
        "7",
        "xa",
    )
    assert out["work_uri"] == "/akn/xa/act/1992/7"
    assert [v["id"] for v in out["versions"]] == [str(version.id)]
    assert out["versions"][0]["expression_date"] == "1992-01-01"
    assert "akn_xml" not in out["versions"][0]
    assert [kw.get("with_akn") for kw in asked] == [False]  # the bodies stay unloaded
    assert missing.is_error and "no law" in missing.content[0].text
    assert malformed.is_error and "not a law id" in malformed.content[0].text


async def test_get_law_follows_every_version_page(monkeypatch: pytest.MonkeyPatch) -> None:
    import codify.storage
    import codify.storage.laws as laws

    law = _law()
    pages = [[_version(law.id) for _ in range(3)] for _ in range(3)]
    asked: list[uuid.UUID | None] = []
    bodies: list[bool | None] = []

    async def fake_get(session: Any, law_id: uuid.UUID) -> tuple[Law, str]:
        return law, "xa"

    async def fake_versions(
        session: Any, law_id: uuid.UUID, *, cursor: uuid.UUID | None = None, **kw: Any
    ) -> tuple[list[Version], uuid.UUID | None]:
        asked.append(cursor)
        bodies.append(kw.get("with_akn"))
        index = (
            0 if cursor is None else next(i for i, p in enumerate(pages) if p[-1].id == cursor) + 1
        )
        page = pages[index]
        # A fresh list, as the store returns: the tool extends what it is handed.
        return list(page), page[-1].id if index < len(pages) - 1 else None

    monkeypatch.setattr(laws, "get_law_with_jurisdiction", fake_get)
    monkeypatch.setattr(codify.storage, "list_versions", fake_versions)

    result = await _call(create_server(sessions=_no_session), "get_law", law_id=str(law.id))

    ids = [v["id"] for v in result.structured_content["versions"]]
    assert ids == [str(v.id) for page in pages for v in page]
    assert asked == [None, pages[0][-1].id, pages[1][-1].id]
    assert bodies == [False, False, False]


async def test_get_version_respects_the_xml_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import codify.storage

    xml = "<akomaNtoso>" + "x" * 500 + "</akomaNtoso>"
    version = _version(uuid.uuid4(), xml)
    bodies: list[bool | None] = []

    async def fake_get(session: Any, version_id: uuid.UUID, **kw: Any) -> Version | None:
        bodies.append(kw.get("with_akn"))
        return version if version_id == version.id else None

    async def fake_length(session: Any, version_id: uuid.UUID) -> int | None:
        return 4321  # not len(xml): a metadata read measures in the database, not the row

    monkeypatch.setattr(codify.storage, "get_version", fake_get)
    monkeypatch.setattr(codify.storage, "get_version_akn_length", fake_length)
    server = create_server(sessions=_no_session, xml_cap=100)

    bare = await _call(server, "get_version", version_id=str(version.id))
    capped = await _call(server, "get_version", version_id=str(version.id), include_xml=True)
    missing = await _call(server, "get_version", version_id=str(uuid.uuid4()))

    assert not bare.is_error
    assert "akn_xml" not in bare.structured_content
    assert bare.structured_content["akn_xml_length"] == 4321
    assert bare.structured_content["structural_quality_grade"] == "clean"
    assert bodies == [False, True, False]  # the body is loaded only when asked for
    out = capped.structured_content
    assert out["akn_xml"] == xml[:100]
    assert out["akn_xml_truncated"] is True
    assert out["akn_xml_length"] == len(xml)
    assert missing.is_error and "no version" in missing.content[0].text


async def test_get_version_under_the_cap_is_whole_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codify.storage

    version = _version(uuid.uuid4())

    async def fake_get(session: Any, version_id: uuid.UUID, **kw: Any) -> Version | None:
        return version

    monkeypatch.setattr(codify.storage, "get_version", fake_get)

    result = await _call(
        create_server(sessions=_no_session),
        "get_version",
        version_id=str(version.id),
        include_xml=True,
    )

    out = result.structured_content
    assert out["akn_xml"] == "<akomaNtoso/>"
    assert out["akn_xml_truncated"] is False


async def test_jurisdictions_list_and_read_from_the_configs() -> None:
    server = create_server(sessions=_no_session)

    listed = await _call(server, "list_jurisdictions")
    one = await _call(server, "get_jurisdiction", code="XA")
    missing = await _call(server, "get_jurisdiction", code="zz-none")
    malformed = await _call(server, "get_jurisdiction", code="../xa")

    codes = [j["code"] for j in listed.structured_content["items"]]
    assert "xa" in codes and codes == sorted(codes)
    assert {"code", "name", "languages"} == set(listed.structured_content["items"][0])
    out = one.structured_content
    assert (out["code"], out["languages"], out["tradition"]) == ("xa", ["eng"], ["common_law"])
    assert set(out["document_classes"]["act"]) == {
        "label",
        "short_label",
        "akn_element",
        "basic_unit",
        "frbr_subtype",
    }
    assert "structuring" not in out and "ocr_header_patterns" not in out
    assert missing.is_error and "no jurisdiction" in missing.content[0].text
    assert malformed.is_error and "no jurisdiction" in malformed.content[0].text


@pytest.mark.parametrize(
    ("name", "args"), [("get_jurisdiction", {"code": "xa"}), ("list_jurisdictions", {})]
)
async def test_an_install_without_jurisdiction_data_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, args: dict[str, Any]
) -> None:
    import codify.jurisdictions as jurisdictions

    monkeypatch.setattr(jurisdictions, "JURISDICTIONS_DIR", tmp_path / "absent")

    result = await _call(create_server(sessions=_no_session), name, **args)

    assert result.is_error
    assert "no jurisdiction data" in result.content[0].text


async def test_compare_reads_both_versions_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    left, right = _two_stored_fixtures(monkeypatch)
    server = create_server(sessions=_no_session, embedding_client=_UnitVectors, llm_client=_GapLlm)

    result = await _call(
        server,
        "compare_versions",
        reference_version_id=str(left.id),
        domestic_version_id=str(right.id),
    )
    missing = await _call(
        server,
        "compare_versions",
        reference_version_id=str(left.id),
        domestic_version_id=str(uuid.uuid4()),
    )

    assert not result.is_error, result.content
    report = result.structured_content
    assert report["summary"]["total"] > 0
    assert report["summary"]["gap"] == report["summary"]["total"]
    assert {r["verdict"] for r in report["results"]} == {"gap"}
    # The first id is the reference side, whatever the caller's order of loading.
    assert report["directive_frbr_uri"].startswith("/akn/xa/act/1992/7")
    assert report["domestic_frbr_uri"].startswith("/akn/xz/")
    assert missing.is_error and "no version" in missing.content[0].text


def _two_stored_fixtures(monkeypatch: pytest.MonkeyPatch) -> tuple[Version, Version]:
    import codify.storage

    left = _version(uuid.uuid4(), FIXTURE.read_text(encoding="utf-8"))
    right = _version(uuid.uuid4(), OTHER_FIXTURE.read_text(encoding="utf-8"))
    rows = {left.id: left, right.id: right}

    async def fake_get(session: Any, version_id: uuid.UUID) -> Version | None:
        return rows.get(version_id)

    monkeypatch.setattr(codify.storage, "get_version", fake_get)
    return left, right


async def test_compare_without_a_chat_endpoint_is_a_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    left, right = _two_stored_fixtures(monkeypatch)

    result = await _call(
        create_server(sessions=_no_session, embedding_client=_UnitVectors),
        "compare_versions",
        reference_version_id=str(left.id),
        domestic_version_id=str(right.id),
    )

    assert result.is_error
    assert "LITELLM_BASE_URL" in result.content[0].text


async def test_compare_over_the_provision_cap_is_refused_before_any_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left, right = _two_stored_fixtures(monkeypatch)
    built: list[str] = []

    def llm() -> _GapLlm:
        built.append("llm")
        return _GapLlm()

    server = create_server(
        sessions=_no_session, embedding_client=_UnitVectors, llm_client=llm, compare_cap=10
    )

    result = await _call(
        server,
        "compare_versions",
        reference_version_id=str(left.id),
        domestic_version_id=str(right.id),
    )

    assert result.is_error
    text = result.content[0].text
    # 42 of the reference's 58 assessable provisions reach the model; 16 are headings.
    assert "42 provisions to assess" in text and "at most 10" in text
    assert built == []

    at_the_cap = create_server(
        sessions=_no_session, embedding_client=_UnitVectors, llm_client=llm, compare_cap=42
    )
    ran = await _call(
        at_the_cap,
        "compare_versions",
        reference_version_id=str(left.id),
        domestic_version_id=str(right.id),
    )

    assert not ran.is_error and built == ["llm"]


async def test_a_crash_inside_a_tool_does_not_leak_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codify.storage

    async def broken(session: Any, version_id: uuid.UUID) -> Version | None:
        raise RuntimeError("connection string with a secret")

    monkeypatch.setattr(codify.storage, "get_version", broken)

    result = await _call(
        create_server(sessions=_no_session), "get_version", version_id=str(uuid.uuid4())
    )

    assert result.is_error
    assert "secret" not in result.content[0].text


def test_mcp_is_registered_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    import codify.mcp

    runs: list[tuple[Any, ...]] = []

    class FakeServer:
        def run(self, transport: str, **kw: Any) -> None:
            runs.append((transport, kw))

    monkeypatch.setattr(codify.mcp, "create_server", lambda: FakeServer())

    with pytest.raises(SystemExit):
        main(["mcp", "--help"])
    assert main(["mcp"]) == 0
    assert main(["mcp", "--http", "--port", "9"]) == 0

    assert runs == [
        ("stdio", {}),
        ("streamable-http", {"host": "127.0.0.1", "port": 9}),
    ]


def test_the_command_serves_the_tools_over_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real transport: the command as a subprocess, spoken to as a client would."""
    from mcp import StdioServerParameters

    monkeypatch.setenv("POSTGRES_URL", "postgresql://x:y@localhost:5432/unused")
    params = StdioServerParameters(
        command=sys.executable, args=["-c", "from codify.cli import main; main(['mcp'])"]
    )

    async def run() -> tuple[set[str], Any]:
        async with Client(params, read_timeout_seconds=60) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            one = await c.call_tool("get_jurisdiction", {"code": "xa"})
        return names, one.structured_content

    names, one = asyncio.run(run())

    assert names == TOOL_NAMES
    assert one["code"] == "xa"


def test_the_command_serves_the_tools_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real transport: `--http` as a subprocess, spoken to at /mcp as a client would."""
    import socket
    import subprocess
    import time

    monkeypatch.setenv("POSTGRES_URL", "postgresql://x:y@localhost:5432/unused")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from codify.cli import main; main(['mcp', '--http', '--port', '{port}'])",
        ]
    )

    async def run() -> tuple[set[str], Any]:
        async with Client(f"http://127.0.0.1:{port}/mcp", read_timeout_seconds=60) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            one = await c.call_tool("get_jurisdiction", {"code": "xa"})
        return names, one.structured_content

    try:
        deadline = time.monotonic() + 60
        while True:  # the port answers once uvicorn is listening
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert proc.poll() is None, "the server exited before listening"
                assert time.monotonic() < deadline, "the server never listened"
                time.sleep(0.1)
        names, one = asyncio.run(run())
    finally:
        proc.terminate()
        proc.wait(timeout=30)

    assert names == TOOL_NAMES
    assert one["code"] == "xa"


def test_the_default_engine_is_disposed_when_the_server_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    monkeypatch.setenv("POSTGRES_URL", "postgresql://x:y@localhost:5432/unused")
    disposed: list[AsyncEngine] = []
    original = AsyncEngine.dispose

    async def spy(self: AsyncEngine, close: bool = True) -> None:
        disposed.append(self)
        await original(self, close)

    monkeypatch.setattr(AsyncEngine, "dispose", spy)

    async def run() -> None:
        async with Client(create_server()) as c:
            await c.list_tools()

    asyncio.run(run())

    assert len(disposed) == 1


async def test_one_embeddings_client_serves_every_search_and_closes_when_the_server_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codify.retrieve.hybrid as hybrid

    class _Transport:
        closed = 0

        async def close(self) -> None:
            _Transport.closed += 1

    class _Embeddings:
        built = 0

        def __init__(self) -> None:
            _Embeddings.built += 1
            self.client = _Transport()

    async def fake_retrieve(session: Any, query: str, **kw: Any) -> list[Any]:
        return []

    monkeypatch.setattr(hybrid, "retrieve", fake_retrieve)

    async with Client(create_server(sessions=_no_session, embedding_client=_Embeddings)) as c:
        for _ in range(3):
            result = await c.call_tool("search_provisions", {"query": "x", "jurisdiction": "xa"})
            assert not result.is_error
        assert (_Embeddings.built, _Transport.closed) == (1, 0)

    assert (_Embeddings.built, _Transport.closed) == (1, 1)


@pytest.mark.integration
async def test_a_metadata_read_leaves_the_body_in_the_database() -> None:
    """The real store: `with_akn=False` defers the body; the length is measured where it lies."""
    from sqlalchemy import delete, inspect, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from codify.akn.io import parse_akn
    from codify.settings import database_url
    from codify.storage import get_version, get_version_akn_length, save_document
    from codify.storage.models import Law, Version

    xml = FIXTURE.read_text(encoding="utf-8")
    engine = create_async_engine(database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as s:
        vid = await save_document(
            s, parse_akn(xml), jurisdiction_code="xa", law_title="Deferred", akn_xml=xml
        )
        law_id = (await s.execute(select(Version.law_id).where(Version.id == vid))).scalar_one()
        await s.commit()
    try:
        async with sessions() as s:
            bare = await get_version(s, vid, with_akn=False)
            assert bare is not None and "akn_xml" in inspect(bare).unloaded
            length = await get_version_akn_length(s, vid)
            whole = await get_version(s, vid)  # the same identity: this loads the body
            assert whole is not None and "akn_xml" not in inspect(whole).unloaded
            assert length == len(whole.akn_xml) == len(xml)
            assert await get_version_akn_length(s, uuid.uuid4()) is None
    finally:
        async with sessions() as s:
            await s.execute(delete(Law).where(Law.id == law_id))
            await s.commit()
        await engine.dispose()


@pytest.mark.integration
def test_a_loaded_bundle_is_searchable_over_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """`codify load --embed`, then every read tool over the real store."""
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from codify import cli_store
    from codify.settings import database_url

    monkeypatch.setattr(cli_store, "_embedding_client", _UnitVectors)
    title = f"Over MCP {uuid.uuid4().hex[:8]}"
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(["load", str(FIXTURE), "--jurisdiction", "xa", "--title", title, "--embed"])
    assert code == 0
    version_id = json.loads(out.getvalue().strip().splitlines()[-1])["version_id"]

    async def read() -> dict[str, Any]:
        server = create_server(embedding_client=_UnitVectors)
        async with Client(server) as c:
            version = await c.call_tool("get_version", {"version_id": version_id})
            law = await c.call_tool("get_law", {"law_id": version.structured_content["law_id"]})
            laws = await c.call_tool("list_laws", {"jurisdiction": "xa", "q": title})
            hits = await c.call_tool(
                "search_provisions", {"query": "official text", "jurisdiction": "xa", "k": 3}
            )
        return {
            "version": version.structured_content,
            "law": law.structured_content,
            "laws": laws.structured_content,
            "hits": hits.structured_content,
        }

    async def cleanup() -> None:
        engine = create_async_engine(database_url())
        async with async_sessionmaker(engine)() as s:
            law_id = select(Version.law_id).where(Version.id == uuid.UUID(version_id))
            await s.execute(delete(Law).where(Law.id == law_id.scalar_subquery()))
            await s.commit()
        await engine.dispose()

    try:
        got = asyncio.run(read())
        assert got["law"]["title"] == title
        assert got["version"]["id"] in [v["id"] for v in got["law"]["versions"]]
        assert [item["title"] for item in got["laws"]["items"]] == [title]
        matches = got["hits"]["matches"]
        assert matches and all({"provision_id", "eid", "score", "text"} <= set(m) for m in matches)
        assert matches == sorted(matches, key=lambda m: -m["score"])
    finally:
        asyncio.run(cleanup())
