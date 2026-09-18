"""The run table is what can go wrong in the thin server: these pin watch, cancel,
retry and replay through the HTTP surface, with a scripted runner for the pipeline."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, inspect

from codify.akn.io import parse_akn
from codify.cli import main
from codify.pipeline.events import Complete, Failed, IngestionEvent, Structured

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402

from codify.serve import create_app  # noqa: E402, needs the serve extra
from codify.serve.runs import RunTable  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-atlantis-1992.akn.xml"


def _complete() -> Complete:
    xml = FIXTURE.read_text(encoding="utf-8")
    return Complete(document=parse_akn(xml), akn_xml=xml)


def _sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.strip().split("\n\n"):
        kind, data = block.split("\n", 1)
        out.append((kind.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def _app(runner: Any) -> FastAPI:
    return create_app(runner=runner)


def _client(runner: Any, app: FastAPI | None = None) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app or _app(runner)), base_url="http://t")


async def _enqueue(c: AsyncClient, jurisdiction: str = "xa", title: str | None = None) -> str:
    r = await c.post(
        "/runs/ingest",
        files={"file": ("act.pdf", b"%PDF-", "application/pdf")},
        data={"jurisdiction": jurisdiction, **({"title": title} if title else {})},
    )
    assert r.status_code == 202, r.text
    return str(r.json()["id"])


async def test_a_run_is_watched_to_complete_without_the_document() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield Structured(bluebell_len=3)
        yield _complete()

    async with _client(runner) as c:
        rid = await _enqueue(c)
        events = _sse((await c.get(f"/runs/{rid}/stream")).text)
        state = (await c.get(f"/runs/{rid}")).json()

    assert [k for k, _ in events] == ["structured", "complete", "end"]
    complete = events[1][1]
    assert complete["work_uri"] == "/akn/xa/act/1992/7"
    assert "akn_xml" not in complete and "document" not in complete
    assert state["status"] == "succeeded"
    assert state["result"]["work_uri"] == "/akn/xa/act/1992/7"


async def test_a_failed_event_fails_the_run_with_its_stage() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield Failed(stage="structure", error="boom")

    async with _client(runner) as c:
        rid = await _enqueue(c)
        await c.get(f"/runs/{rid}/stream")
        state = (await c.get(f"/runs/{rid}")).json()

    assert state["status"] == "failed"
    assert state["error"] == "structure: boom"


async def test_a_runner_that_ends_without_a_verdict_is_a_failure() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield Structured(bluebell_len=1)

    async with _client(runner) as c:
        rid = await _enqueue(c)
        await c.get(f"/runs/{rid}/stream")
        state = (await c.get(f"/runs/{rid}")).json()

    assert state["status"] == "failed"
    assert "without Complete or Failed" in state["error"]


async def test_a_runner_that_raises_fails_the_run_not_the_server() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        raise ValueError("no such file")
        yield  # unreachable; makes this an async generator

    async with _client(runner) as c:
        rid = await _enqueue(c)
        await c.get(f"/runs/{rid}/stream")
        state = (await c.get(f"/runs/{rid}")).json()
        assert (await c.get("/health")).status_code == 200

    assert state["status"] == "failed"
    assert state["error"] == "ValueError: no such file"


async def test_a_run_can_be_cancelled_mid_stream() -> None:
    started = asyncio.Event()

    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield Structured(bluebell_len=1)
        started.set()
        await asyncio.sleep(30)
        yield _complete()

    async with _client(runner) as c:
        rid = await _enqueue(c)
        await asyncio.wait_for(started.wait(), 5)
        assert (await c.post(f"/runs/{rid}/cancel")).status_code == 200
        events = _sse((await c.get(f"/runs/{rid}/stream")).text)
        state = (await c.get(f"/runs/{rid}")).json()

    assert [k for k, _ in events] == ["structured", "end"]
    assert state["status"] == "cancelled"
    assert state["completed_at"] is not None


async def test_a_queued_run_cancels_without_ever_running() -> None:
    ran: list[str] = []

    async def runner(p: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        ran.append(p["url"])
        await asyncio.sleep(30)
        yield _complete()

    table = RunTable(runner, concurrency=1)
    first = table.enqueue("ingest", {"url": "a"})
    second = table.enqueue("ingest", {"url": "b"})
    await asyncio.sleep(0.05)

    assert table.cancel(second.id)
    await asyncio.sleep(0.05)
    assert second.status == "cancelled"
    assert ran == ["a"]
    assert first.status == "running"
    table.cancel(first.id)


async def test_a_run_cancelled_before_its_first_step_still_ends() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield _complete()

    table = RunTable(runner)
    run = table.enqueue("ingest", {})
    assert table.cancel(run.id)

    events = await asyncio.wait_for(_collect(table, run.id), 5)

    assert events == []
    assert run.status == "cancelled"


async def _collect(table: RunTable, run_id: uuid.UUID) -> list[dict[str, Any]]:
    return [e async for e in table.stream(run_id)]


async def test_a_finished_run_is_retried_as_a_new_run() -> None:
    calls = 0

    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield Failed(stage="ocr", error="flaky")
        else:
            yield _complete()

    async with _client(runner) as c:
        rid = await _enqueue(c)
        await c.get(f"/runs/{rid}/stream")
        retry = (await c.post(f"/runs/{rid}/retry")).json()
        await c.get(f"/runs/{retry['id']}/stream")
        old = (await c.get(f"/runs/{rid}")).json()
        new = (await c.get(f"/runs/{retry['id']}")).json()
        listed = (await c.get("/runs")).json()

    assert retry["retry_of"] == rid
    assert old["status"] == "failed" and new["status"] == "succeeded"
    assert [r["id"] for r in listed] == [new["id"], old["id"]]


async def test_only_a_finished_run_can_be_retried_or_cancelled() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        await asyncio.sleep(30)
        yield _complete()

    async with _client(runner) as c:
        rid = await _enqueue(c)
        assert (await c.post(f"/runs/{rid}/retry")).status_code == 409
        assert (await c.post(f"/runs/{rid}/cancel")).status_code == 200
        await c.get(f"/runs/{rid}/stream")
        assert (await c.post(f"/runs/{rid}/cancel")).status_code == 409
        missing = uuid.uuid4()
        assert (await c.get(f"/runs/{missing}")).status_code == 404
        assert (await c.get(f"/runs/{missing}/stream")).status_code == 404


async def test_a_late_subscriber_sees_every_event() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield Structured(bluebell_len=1)
        yield Structured(bluebell_len=2)
        yield _complete()

    table = RunTable(runner)
    run = table.enqueue("ingest", {})
    await asyncio.sleep(0.05)
    assert run.status == "succeeded"

    kinds = [e["kind"] async for e in table.stream(run.id)]

    assert kinds == ["structured", "structured", "complete"]


async def test_an_event_between_replay_and_subscribe_is_not_lost() -> None:
    gate = asyncio.Event()

    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield Structured(bluebell_len=1)
        await gate.wait()
        yield Structured(bluebell_len=2)
        yield _complete()

    table = RunTable(runner)
    run = table.enqueue("ingest", {})
    await asyncio.sleep(0.02)
    seen: list[int] = []

    async def watch() -> None:
        async for e in table.stream(run.id):
            seen.append(e.get("bluebell_len", 0))
            if len(seen) == 1:
                gate.set()

    await asyncio.wait_for(watch(), 5)
    assert seen == [1, 2, 0]


async def test_a_run_that_ends_during_replay_still_delivers_its_last_events() -> None:
    gate = asyncio.Event()

    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield Structured(bluebell_len=1)
        yield Structured(bluebell_len=2)
        await gate.wait()
        yield _complete()

    table = RunTable(runner)
    run = table.enqueue("ingest", {})
    await asyncio.sleep(0.02)
    seen: list[str] = []

    async def watch() -> None:
        async for e in table.stream(run.id):
            seen.append(e["kind"])
            # Let the run finish while this subscriber is still replaying.
            gate.set()
            await asyncio.sleep(0.01)

    await asyncio.wait_for(watch(), 5)
    assert seen == ["structured", "structured", "complete"]


async def test_an_upload_is_kept_for_the_run_and_removed_at_shutdown(tmp_path: Path) -> None:
    sources: list[str] = []

    async def runner(p: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        sources.append(p["source"])
        yield _complete()

    app = _app(runner)
    async with app.router.lifespan_context(app), _client(runner, app) as c:
        r = await c.post(
            "/runs/ingest",
            files={"file": ("act.pdf", b"%PDF-", "application/pdf")},
            data={"jurisdiction": "xa"},
        )
        assert r.status_code == 202
        await c.get(f"/runs/{r.json()['id']}/stream")
        assert Path(sources[0]).read_bytes() == b"%PDF-"
        assert Path(sources[0]).suffix == ".pdf"

    assert not Path(sources[0]).exists()


@pytest.mark.integration
async def test_a_succeeded_http_ingest_is_stored_with_its_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real runner: the pipeline's Complete is saved so the reads see it."""
    import codify.pipeline
    from codify import cli_store
    from codify.storage.models import Law

    async def fake_ingest(*_: Any, **__: Any) -> AsyncIterator[IngestionEvent]:
        yield _complete()

    monkeypatch.setattr(codify.pipeline, "ingest_document", fake_ingest)
    monkeypatch.setattr(cli_store, "_llm_client", lambda model: None)
    title = f"Served {uuid.uuid4().hex[:8]}"

    app = create_app()
    async with _client(None, app) as c:
        rid = await _enqueue(c, title=title)
        events = _sse((await c.get(f"/runs/{rid}/stream")).text)
        assert events[-2][0] == "complete", events
        laws = (await c.get("/laws", params={"jurisdiction": "xa", "q": title})).json()["items"]
        law = (await c.get(f"/laws/{laws[0]['id']}")).json()
        version = (await c.get(f"/versions/{law['versions'][0]['id']}")).json()

    try:
        assert (law["title"], law["doctype"], law["year"], law["number"]) == (
            title,
            "act",
            1992,
            "7",
        )
        assert version["akn_xml"].startswith("<akomaNtoso")
    finally:
        async with app.state.sessions() as s:
            await s.execute(delete(Law).where(Law.id == uuid.UUID(law["id"])))
            await s.commit()
        await app.state.sessions.kw["bind"].dispose()


async def test_ingest_url_refuses_anything_but_a_url() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield _complete()

    async with _client(runner) as c:
        for source in (
            "/etc/passwd",
            "etc/passwd",
            "file:///etc/passwd",
            "https://example.test/a.pdf",
        ):
            r = await c.post("/runs/ingest-url", json={"url": source, "jurisdiction": "xa"})
            assert r.status_code == 422, source
        assert (await c.get("/runs")).json() == []


async def test_finished_runs_beyond_keep_are_forgotten_oldest_first() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield _complete()

    table = RunTable(runner, keep=2, concurrency=1)
    first = table.enqueue("ingest", {})
    await asyncio.sleep(0.02)
    second, third = table.enqueue("ingest", {}), table.enqueue("ingest", {})
    await asyncio.sleep(0.05)

    assert {r.id for r in table.list()} == {second.id, third.id}
    assert table.get(first.id) is None
    assert table.retry(first.id) is None


@pytest.mark.integration
async def test_xml_ingests_without_a_model_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The native-AKN lane is deterministic; an unset gateway must not stop it."""
    from codify.serve.app import _ingest_runner
    from codify.storage.models import Law

    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    app = _app(None)
    events = [
        e
        async for e in _ingest_runner(app.state.sessions)(
            {"source": str(FIXTURE), "jurisdiction": "xa"}
        )
    ]

    try:
        assert events[-1].kind == "complete", events[-1]
    finally:
        async with app.state.sessions() as s:
            await s.execute(delete(Law).where(Law.frbr_work_uri == "/akn/xa/act/1992/7"))
            await s.commit()
        await app.state.sessions.kw["bind"].dispose()


async def test_a_subscriber_outlives_its_forgotten_run() -> None:
    gate = asyncio.Event()

    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        await gate.wait()
        yield _complete()

    table = RunTable(runner, keep=0)
    run = table.enqueue("ingest", {})

    async def watch() -> list[str]:
        seen = []
        async for e in table.stream(run.id):
            seen.append(e["kind"])
        return seen

    task = asyncio.create_task(watch())
    await asyncio.sleep(0.02)
    gate.set()

    assert await asyncio.wait_for(task, 5) == ["complete"]
    assert table.get(run.id) is None


async def test_a_full_queue_is_429_and_keeps_no_upload() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        await asyncio.sleep(30)
        yield _complete()

    app = _app(runner)
    app.state.runs._max_queued = 1
    async with _client(runner, app) as c:
        for _ in range(3):  # two run, one queues
            await _enqueue(c)
            await asyncio.sleep(0.01)
        accepted = len(list(app.state.uploads.iterdir()))
        r = await c.post(
            "/runs/ingest",
            files={"file": ("act.pdf", b"%PDF-", "application/pdf")},
            data={"jurisdiction": "xa"},
        )
        assert r.status_code == 429
        assert len((await c.get("/runs")).json()) == 3
        assert len(list(app.state.uploads.iterdir())) == accepted  # the refused one is gone
        queued = app.state.runs.list()[0]
        assert queued.status == "queued"
        app.state.runs.cancel(queued.id)
        await c.get(f"/runs/{queued.id}/stream")
        await _enqueue(c)  # the freed slot refills
        assert (await c.post(f"/runs/{queued.id}/retry")).status_code == 429
        for run in app.state.runs.list():
            app.state.runs.cancel(run.id)

    assert len(list(app.state.uploads.iterdir())) == accepted + 1  # one refill, none refused


async def test_out_of_range_paging_and_k_are_422_not_500() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield _complete()

    async with _client(runner) as c:
        for path in (
            "/laws?limit=-1",
            "/laws?limit=0",
            "/laws?offset=-1",
            "/laws?limit=501",
            "/jurisdictions/xa/laws?limit=-1",
        ):
            assert (await c.get(path)).status_code == 422, path
        for path in (
            "/search?q=x&jurisdiction=xa&k=-1",
            "/search?q=x&jurisdiction=xa&k=0",
            "/search?q=x&jurisdiction=xa&k=101",
        ):
            assert (await c.get(path)).status_code == 422, path


async def test_search_without_an_embeddings_endpoint_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("EMBEDDING_BASE_URL", "LITELLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield _complete()

    async with _client(runner) as c:
        r = await c.get("/search?q=x&jurisdiction=xa")

    assert r.status_code == 503
    assert "EMBEDDING_BASE_URL" in r.json()["detail"]


async def test_a_failed_pass_before_complete_does_not_end_the_run() -> None:
    """A lane may report a failed enrichment pass and still complete."""
    gate = asyncio.Event()

    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield Failed(stage="enrich", error="pass crashed")
        await gate.wait()
        yield _complete()

    table = RunTable(runner)
    run = table.enqueue("ingest", {})
    await asyncio.sleep(0.02)
    assert run.status == "running"
    assert table.retry(run.id) is None

    async def watch() -> list[str]:
        kinds = []
        async for e in table.stream(run.id):
            kinds.append(e["kind"])
            gate.set()
        return kinds

    assert await asyncio.wait_for(watch(), 5) == ["failed", "complete"]
    assert run.status == "succeeded" and run.error is None


async def test_shutdown_cancels_running_runs_before_cleanup() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        await asyncio.sleep(30)
        yield _complete()

    app = _app(runner)
    async with app.router.lifespan_context(app), _client(runner, app) as c:
        rid = await _enqueue(c)
        await asyncio.sleep(0.02)

    run = app.state.runs.get(uuid.UUID(rid))
    assert run is not None and run.status == "cancelled"
    assert not app.state.uploads.exists()


async def test_an_unknown_or_uncanonical_jurisdiction_is_422_or_folded() -> None:
    seen: list[str] = []

    async def runner(p: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        seen.append(p["jurisdiction"])
        yield _complete()

    async with _client(runner) as c:
        r = await c.post(
            "/runs/ingest",
            files={"file": ("act.pdf", b"%PDF-", "application/pdf")},
            data={"jurisdiction": "zz"},
        )
        assert r.status_code == 422
        rid = await _enqueue(c, jurisdiction=" XA ")
        await c.get(f"/runs/{rid}/stream")

    assert seen == ["xa"]


async def test_ingest_url_is_the_eu_lane_only() -> None:
    """Only the EU lane fetches; core ships no eu config, so here the pair fails at
    the config, and under CODIFY_DATA_ROOT with one it runs."""

    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield _complete()

    url = "https://eur-lex.europa.eu/a.xml"
    async with _client(runner) as c:
        r = await c.post("/runs/ingest-url", json={"url": url, "jurisdiction": "xa"})
        assert r.status_code == 422 and "jurisdiction eu" in r.json()["detail"]
        r = await c.post("/runs/ingest-url", json={"url": url, "jurisdiction": "EU"})
        assert r.status_code == 422 and "no jurisdiction config for 'EU'" in r.json()["detail"]
        assert (await c.get("/runs")).json() == []


async def test_a_failed_upload_copy_leaves_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield _complete()

    def broken(*_: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copyfileobj", broken)
    app = _app(runner)
    async with _client(runner, app) as c:
        with pytest.raises(OSError, match="disk full"):
            await c.post(
                "/runs/ingest",
                files={"file": ("act.pdf", b"%PDF-", "application/pdf")},
                data={"jurisdiction": "xa"},
            )

    assert list(app.state.uploads.iterdir()) == []


async def test_two_runs_ending_on_one_tick_both_reach_their_subscribers() -> None:
    """The first run's eviction pass must not forget a run whose task has returned
    but whose done callback has not yet published the end sentinel."""
    gate = asyncio.Event()

    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        await gate.wait()
        yield _complete()

    table = RunTable(runner, keep=0)
    first, second = table.enqueue("ingest", {}), table.enqueue("ingest", {})
    await asyncio.sleep(0.02)
    watchers = [asyncio.create_task(_collect(table, r.id)) for r in (first, second)]
    await asyncio.sleep(0.02)
    gate.set()

    events = await asyncio.wait_for(asyncio.gather(*watchers), 5)

    assert [[e["kind"] for e in run] for run in events] == [["complete"], ["complete"]]
    assert first.completed_at and second.completed_at


async def test_a_malformed_jurisdiction_code_is_404_not_500() -> None:
    async def runner(_: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        yield _complete()

    async with _client(runner) as c:
        for code in ("..", "%2E%2E", "a/b", "zz"):
            assert (await c.get(f"/jurisdictions/{code}")).status_code == 404, code


@pytest.mark.integration
async def test_law_detail_lists_every_version_past_the_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from codify.storage import save_document
    from codify.storage import versions as versions_module
    from codify.storage.models import Law, Version

    # Pages of two, so three stored versions need the cursor followed.
    original = versions_module.list_versions
    calls: list[dict[str, Any]] = []

    async def two_per_page(session: Any, law_id: Any, **kw: Any) -> Any:
        calls.append(kw)
        return await original(
            session, law_id, limit=2, cursor=kw.get("cursor"), with_akn=kw.get("with_akn", True)
        )

    monkeypatch.setattr("codify.storage.list_versions", two_per_page)
    xml = FIXTURE.read_text(encoding="utf-8")
    title = f"Paged {uuid.uuid4().hex[:8]}"
    app = _app(None)
    async with app.state.sessions() as s:
        for lang in ("eng", "fra", "deu"):
            doc = parse_akn(
                xml.replace("/eng", f"/{lang}").replace('language="eng"', f'language="{lang}"')
            )
            vid = await save_document(s, doc, jurisdiction_code="xa", law_title=title, akn_xml=xml)
        law_id = (await s.execute(select(Version.law_id).where(Version.id == vid))).scalar_one()
        await s.commit()

    try:
        async with _client(None, app) as c:
            law = (await c.get(f"/laws/{law_id}")).json()
        assert sorted(v["language"] for v in law["versions"]) == ["deu", "eng", "fra"]
        assert calls and all(
            c.get("with_akn") is False for c in calls
        )  # the route asks for no bodies
        async with app.state.sessions() as s:
            rows, _ = await original(s, law_id, with_akn=False)
        assert all("akn_xml" in inspect(row).unloaded for row in rows)
    finally:
        async with app.state.sessions() as s:
            await s.execute(delete(Law).where(Law.id == law_id))
            await s.commit()
        await app.state.sessions.kw["bind"].dispose()


@pytest.mark.integration
async def test_laws_under_a_jurisdiction_validate_and_fold_the_code() -> None:
    """`zz` is a 404, not an empty page; `XA` lists what `xa` lists."""
    from codify.storage import save_document
    from codify.storage.models import Law

    title = f"Folded {uuid.uuid4().hex[:8]}"
    app = _app(None)
    async with app.state.sessions() as s:
        await save_document(
            s, _complete().document, jurisdiction_code="xa", law_title=title, akn_xml="<a/>"
        )
        await s.commit()

    try:
        async with _client(None, app) as c:
            assert (await c.get("/jurisdictions/zz/laws")).status_code == 404
            upper = (await c.get("/jurisdictions/XA/laws", params={"limit": 500})).json()["items"]
        assert title in {law["title"] for law in upper}
    finally:
        async with app.state.sessions() as s:
            await s.execute(delete(Law).where(Law.title == title))
            await s.commit()
        await app.state.sessions.kw["bind"].dispose()


def test_serve_is_registered() -> None:
    with pytest.raises(SystemExit):
        main(["serve", "--help"])
