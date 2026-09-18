"""`codify load`, `search` and `compare` are plumbing over the library; these
pin the plumbing. The bundle reader and the environment fallbacks run without
a database; the round trip from load to search runs against Postgres."""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from codify import cli_store
from codify.akn.document import Document
from codify.cli import main
from codify.settings import database_url
from codify.storage.models import Law, Version

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-atlantis-1992.akn.xml"
OTHER_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-zerzura-1994.akn.xml"


def test_a_bundle_gives_its_akn_and_manifest(tmp_path: Path) -> None:
    (tmp_path / "final.akn.xml").write_text("<akomaNtoso/>")
    (tmp_path / "manifest.json").write_text(json.dumps({"jurisdiction": "xa"}))

    xml, manifest = cli_store._bundle_akn(tmp_path)

    assert xml == "<akomaNtoso/>"
    assert manifest == {"jurisdiction": "xa"}


def test_a_bare_akn_file_has_no_manifest(tmp_path: Path) -> None:
    f = tmp_path / "law.akn.xml"
    f.write_text("<akomaNtoso/>")

    assert cli_store._bundle_akn(f) == ("<akomaNtoso/>", {})


def test_a_directory_without_akn_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="final.akn.xml"):
        cli_store._bundle_akn(tmp_path)


def test_descriptors_come_from_the_work_uri_with_the_unknown_year_folded() -> None:
    def doc(uri: str, work_date: date | None = None) -> Document:
        return Document(
            frbr_work_uri=uri,
            frbr_expression_uri=f"{uri}/eng",
            language="eng",
            expression_date=date(2026, 1, 1),
            work_date=work_date,
        )

    assert cli_store._descriptors(doc("/akn/al/act/vendim/2021/285")) == ("vendim", 2021, "285")
    assert cli_store._descriptors(doc("/akn/xa/act/0001/abc")) == ("act", None, "abc")
    assert cli_store._descriptors(doc("/akn/xa/act/1992/7", date(1991, 12, 31))) == (
        "act",
        1991,
        "7",
    )


def test_the_first_set_variable_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A_UNSET", raising=False)
    monkeypatch.setenv("B_SET", "b")
    monkeypatch.setenv("C_SET", "c")

    assert cli_store._env("A_UNSET", "B_SET", "C_SET") == "b"
    assert cli_store._env("A_UNSET") is None


def test_the_database_url_takes_the_asyncpg_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@h:5432/d")
    assert database_url() == "postgresql+asyncpg://u:p@h:5432/d"

    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@h:5432/d")
    assert database_url() == "postgresql+asyncpg://u:p@h:5432/d"


def test_an_embeddings_endpoint_is_required_and_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("EMBEDDING_BASE_URL", "LITELLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="EMBEDDING_BASE_URL"):
        cli_store._embedding_client()


def test_the_three_commands_are_registered() -> None:
    with pytest.raises(SystemExit):
        main(["load", "--help"])
    with pytest.raises(SystemExit):
        main(["search", "--help"])
    with pytest.raises(SystemExit):
        main(["compare", "--help"])


_DIM = 768


class _FixedVectors:
    """Deterministic embeddings, so a search has something to rank."""

    model = "test-model"

    async def embed_documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
        return [[1.0, 0.1 * (i + 1)] + [0.0] * (_DIM - 2) for i in range(len(items))]

    async def embed_one(self, text: str, **_: object) -> list[float]:
        return [1.0, 0.1] + [0.0] * (_DIM - 2)


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue()


async def _delete_law_of(version_id: uuid.UUID) -> None:
    engine = create_async_engine(database_url())
    async with async_sessionmaker(engine)() as s:
        law_id = select(Version.law_id).where(Version.id == version_id).scalar_subquery()
        await s.execute(delete(Law).where(Law.id == law_id))
        await s.commit()
    await engine.dispose()


@pytest.mark.integration
def test_load_then_search_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_store, "_embedding_client", _FixedVectors)
    title = f"Round trip {uuid.uuid4().hex[:8]}"

    code, out = _run(["load", str(FIXTURE), "--jurisdiction", "xa", "--title", title, "--embed"])
    loaded = json.loads(out.strip().splitlines()[-1])
    version_id = uuid.UUID(loaded["version_id"])

    try:
        assert code == 0
        assert loaded["embedded"] > 0
        code, out = _run(["search", "official text", "--jurisdiction", "xa", "-k", "3"])
        hits = [json.loads(line) for line in out.strip().splitlines()]
        assert code == 0
        assert hits and all({"eid", "score", "text"} <= set(h) for h in hits)
        assert hits == sorted(hits, key=lambda h: -h["score"])
    finally:
        asyncio.run(_delete_law_of(version_id))


@pytest.mark.integration
def test_loading_the_same_file_twice_returns_the_held_version() -> None:
    """The README says so; `save_document` returns the expression it already holds."""
    title = f"Twice {uuid.uuid4().hex[:8]}"
    argv = ["load", str(FIXTURE), "--jurisdiction", "xa", "--title", title]

    first = json.loads(_run(argv)[1].strip().splitlines()[-1])["version_id"]
    second = json.loads(_run(argv)[1].strip().splitlines()[-1])["version_id"]

    try:
        assert first == second
    finally:
        asyncio.run(_delete_law_of(uuid.UUID(first)))


@pytest.mark.integration
def test_a_search_with_no_match_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scripts can tell an empty result from a successful one."""
    monkeypatch.setattr(cli_store, "_embedding_client", _FixedVectors)

    code, out = _run(["search", "anything", "--jurisdiction", "zz-none", "-k", "3"])

    assert code == 1
    assert out.strip() == ""


def test_a_bare_file_without_flags_says_what_is_missing(tmp_path: Path) -> None:
    f = tmp_path / "law.akn.xml"
    f.write_text("<akomaNtoso/>")

    with pytest.raises(SystemExit, match="--jurisdiction"):
        main(["load", str(f)])
    with pytest.raises(SystemExit, match="--title"):
        main(["load", str(f), "--jurisdiction", "xa"])


def test_search_requires_a_jurisdiction() -> None:
    with pytest.raises(SystemExit):
        main(["search", "anything"])


def test_an_absent_url_is_an_error_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="POSTGRES_URL"):
        database_url()

    monkeypatch.setenv("ENVIRONMENT", "development")
    assert database_url().startswith("postgresql+asyncpg://")


@pytest.mark.integration
def test_a_bundle_loads_with_no_flags_and_its_descriptors_come_from_the_uri(
    tmp_path: Path,
) -> None:
    """The manifest carries the title the model read and, as `ingest-one` writes them,
    a blank year and no doctype; doctype, year and number come from the work URI."""
    title = f"Read title {uuid.uuid4().hex[:8]}"
    (tmp_path / "final.akn.xml").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "jurisdiction": "xa",
                "metadata": {"title": title, "doctype": None, "year": "", "number": "", "date": ""},
            }
        )
    )

    code, out = _run(["load", str(tmp_path)])
    version_id = uuid.UUID(json.loads(out.strip().splitlines()[-1])["version_id"])

    try:
        assert code == 0
        law = asyncio.run(_law_of(version_id))
        assert (law.title, law.doctype, law.year, law.number) == (title, "act", 1992, "7")
    finally:
        asyncio.run(_delete_law_of(version_id))


async def _law_of(version_id: uuid.UUID) -> Law:
    engine = create_async_engine(database_url())
    async with async_sessionmaker(engine)() as s:
        law_id = select(Version.law_id).where(Version.id == version_id).scalar_subquery()
        law = (await s.execute(select(Law).where(Law.id == law_id))).scalar_one()
    await engine.dispose()
    return law


@pytest.mark.integration
def test_each_command_disposes_its_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_store, "_embedding_client", _FixedVectors)
    disposed: list[AsyncEngine] = []
    original = AsyncEngine.dispose

    async def spy(self: AsyncEngine, close: bool = True) -> None:
        disposed.append(self)
        await original(self, close)

    monkeypatch.setattr(AsyncEngine, "dispose", spy)

    _run(["search", "anything", "--jurisdiction", "zz-none"])

    assert len(disposed) == 1


def test_compare_writes_a_report_from_two_akn_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Llm:
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

    class _UnitVectors(_FixedVectors):
        async def embed_documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
            return [[float(j == i % _DIM) for j in range(_DIM)] for i in range(len(items))]

    monkeypatch.setattr(cli_store, "_llm_client", lambda model: _Llm())
    monkeypatch.setattr(cli_store, "_embedding_client", _UnitVectors)
    out = tmp_path / "report.json"

    code, printed = _run(["compare", str(FIXTURE), str(OTHER_FIXTURE), "--out", str(out)])
    report = json.loads(out.read_text())

    assert code == 0 and printed == ""
    assert len(report["results"]) >= report["summary"]["total"] > 0
    assert {r["verdict"] for r in report["results"]} == {"gap"}
    assert report["summary"]["gap"] == report["summary"]["total"]
