"""The thin HTTP server: every route is one library call, ingest is a run, and the
only state is the in-memory run table."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.pipeline.events import Complete, IngestionEvent
from codify.serve.runs import QueueFull, Run, RunTable
from codify.settings import database_url

SessionFactory = Callable[[], AsyncSession]
_LIMIT = Query(50, ge=1, le=500)
_OFFSET = Query(0, ge=0)


class IngestUrl(BaseModel):
    url: HttpUrl  # a URL only: a local path is the upload endpoint's job
    jurisdiction: str
    title: str | None = None


def _ingest_runner(
    sessions: async_sessionmaker[AsyncSession],
) -> Callable[[dict[str, Any]], AsyncIterator[IngestionEvent]]:
    """Run the pipeline, then store what it produced so the reads can see it."""

    async def run(params: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        from codify.cli_store import _descriptors, _env, _llm_client
        from codify.pipeline import ingest_document
        from codify.pipeline.events import MetadataExtracted
        from codify.storage import save_document

        # The XML lanes need no model; without one a PDF fails at dispatch, and says so.
        llm = _llm_client(params.get("model")) if _env("LITELLM_BASE_URL") else None
        read_title = ""
        async for event in ingest_document(params["source"], params["jurisdiction"], llm=llm):
            if isinstance(event, MetadataExtracted):
                read_title = str(event.metadata.get("title") or "")
            if isinstance(event, Complete):
                doctype, year, number = _descriptors(event.document)
                async with sessions() as session:
                    await save_document(
                        session,
                        event.document,
                        jurisdiction_code=params["jurisdiction"],
                        law_title=params.get("title") or read_title or event.document.frbr_work_uri,
                        doctype=doctype,
                        year=year,
                        number=number,
                        akn_xml=event.akn_xml,
                    )
                    await session.commit()
            yield event

    return run


def create_app(
    *, runner: Callable[[dict[str, Any]], AsyncIterator[IngestionEvent]] | None = None
) -> FastAPI:
    engine = create_async_engine(database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    # Uploads live as long as the runs that read them, and go when the run is forgotten.
    uploads = tempfile.TemporaryDirectory(prefix="codify-uploads-")

    def drop_upload(run: Run) -> None:
        source = Path(str(run.params.get("source", "")))
        # A retry shares its source with the run it retries; the file goes with the last of them.
        if source.parent == Path(uploads.name) and not any(
            str(source) == r.params.get("source") for r in runs.list()
        ):
            source.unlink(missing_ok=True)

    runs: RunTable = RunTable(runner or _ingest_runner(sessions), on_forget=drop_upload)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await runs.shutdown()
        uploads.cleanup()
        await engine.dispose()

    app = FastAPI(title="codify", docs_url="/docs", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.runs = runs
    app.state.uploads = Path(uploads.name)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/jurisdictions")
    async def jurisdictions() -> list[dict[str, Any]]:
        from codify.jurisdictions import JURISDICTIONS_DIR, load_config

        out = []
        for entry in sorted(JURISDICTIONS_DIR.iterdir()):
            if (entry / "config.json").exists():
                config = load_config(entry.name)
                out.append(
                    {"code": config.code, "name": config.name, "languages": list(config.languages)}
                )
        return out

    @app.get("/jurisdictions/{code}")
    async def jurisdiction(code: str) -> dict[str, Any]:
        from codify.jurisdictions import JurisdictionConfigError, load_config

        try:
            config = load_config(code.strip().lower())
        except JurisdictionConfigError as exc:  # missing or malformed: neither is ours
            raise HTTPException(404, str(exc)) from exc
        return config.model_dump(mode="json")

    @app.get("/jurisdictions/{code}/laws")
    async def laws_for(code: str, limit: int = _LIMIT, offset: int = _OFFSET) -> dict[str, Any]:
        return await laws(jurisdiction=_jurisdiction(code, 404), limit=limit, offset=offset)

    @app.get("/laws")
    async def laws(
        jurisdiction: str | None = None,
        doctype: str | None = None,
        year: int | None = None,
        q: str | None = None,
        limit: int = _LIMIT,
        offset: int = _OFFSET,
    ) -> dict[str, Any]:
        from codify.storage import list_laws

        async with sessions() as session:
            rows, _ = await list_laws(
                session,
                jurisdictions=[_jurisdiction(jurisdiction)] if jurisdiction else None,
                doctype=doctype,
                year=year,
                q=q,
                limit=limit,
                offset=offset,
            )
        return {
            "items": [r.model_dump(mode="json") for r in rows],
            "limit": limit,
            "offset": offset,
        }

    @app.get("/laws/{law_id}")
    async def law(law_id: uuid.UUID) -> dict[str, Any]:
        from codify.storage import list_versions
        from codify.storage.laws import get_law_with_jurisdiction

        async with sessions() as session:
            found = await get_law_with_jurisdiction(session, law_id)
            if found is None:
                raise HTTPException(404, "no such law")
            row, code = found
            versions, cursor = await list_versions(session, law_id, with_akn=False)
            while cursor is not None:  # every version, not the first page
                page, cursor = await list_versions(session, law_id, cursor=cursor, with_akn=False)
                versions.extend(page)
        return {
            **_law_json(row),
            "jurisdiction": code,
            "versions": [_version_json(v, with_akn=False) for v in versions],
        }

    @app.get("/versions/{version_id}")
    async def version(version_id: uuid.UUID) -> dict[str, Any]:
        from codify.storage import get_version

        async with sessions() as session:
            row = await get_version(session, version_id)
        if row is None:
            raise HTTPException(404, "no such version")
        return _version_json(row, with_akn=True)

    @app.get("/search")
    async def search(
        q: str, jurisdiction: str, k: int = Query(10, ge=1, le=100), language: str | None = None
    ) -> dict[str, Any]:
        from codify.cli_store import _embedding_client
        from codify.retrieve.hybrid import retrieve

        code = _jurisdiction(jurisdiction)
        try:
            embedding_client = _embedding_client()
        except SystemExit as exc:  # the CLI helper's way of saying "not configured"
            raise HTTPException(503, str(exc)) from exc
        async with sessions() as session:
            matches = await retrieve(
                session,
                q,
                embedding_client=embedding_client,
                jurisdiction_code=code,
                language=language,
                k=k,
            )
        return {
            "query": q,
            "matches": [
                {
                    "provision_id": str(m.provision_id),
                    "eid": m.akn_eid,
                    "score": m.rrf_score,
                    "text": m.text,
                }
                for m in matches
            ],
        }

    @app.post("/runs/ingest", status_code=202)
    async def ingest(
        file: UploadFile = File(...), jurisdiction: str = Form(...), title: str | None = Form(None)
    ) -> dict[str, Any]:
        code = _jurisdiction(jurisdiction)
        suffix = Path(file.filename or "upload").suffix or ".pdf"
        source = Path(uploads.name) / f"{uuid.uuid4()}{suffix}"
        try:
            with source.open("wb") as out:
                await asyncio.to_thread(shutil.copyfileobj, file.file, out)
            run = runs.enqueue(
                "ingest", {"source": str(source), "jurisdiction": code, "title": title}
            )
        except QueueFull as exc:
            source.unlink(missing_ok=True)
            raise HTTPException(429, str(exc)) from exc
        except BaseException:  # a file no run will read is not kept
            source.unlink(missing_ok=True)
            raise
        return run.snapshot()

    @app.post("/runs/ingest-url", status_code=202)
    async def ingest_url(body: IngestUrl) -> dict[str, Any]:
        from codify.pipeline.formats import looks_like_eu

        # The EU lane is the only one that fetches; every other lane reads a path.
        if not looks_like_eu(str(body.url)) or body.jurisdiction.strip().lower() != "eu":
            raise HTTPException(
                422, "only an EU publications URL (eur-lex or publications.europa.eu) under eu"
            )
        code = _jurisdiction(body.jurisdiction)
        try:
            run = runs.enqueue(
                "ingest", {"source": str(body.url), "jurisdiction": code, "title": body.title}
            )
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc
        return run.snapshot()

    @app.get("/runs")
    async def list_runs() -> list[dict[str, Any]]:
        return [r.snapshot() for r in runs.list()]

    @app.get("/runs/{run_id}")
    async def run_state(run_id: uuid.UUID) -> dict[str, Any]:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "no such run")
        return run.snapshot()

    @app.get("/runs/{run_id}/stream")
    async def run_stream(run_id: uuid.UUID, request: Request) -> StreamingResponse:
        if runs.get(run_id) is None:
            raise HTTPException(404, "no such run")

        async def body() -> AsyncIterator[bytes]:
            async for event in runs.stream(run_id):
                if await request.is_disconnected():
                    return
                yield f"event: {event.get('kind', 'event')}\ndata: {json.dumps(event)}\n\n".encode()
            yield b"event: end\ndata: {}\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    @app.post("/runs/{run_id}/cancel")
    async def cancel(run_id: uuid.UUID) -> dict[str, Any]:
        if not runs.cancel(run_id):
            raise HTTPException(409, "not running")
        return {"cancelled": str(run_id)}

    @app.post("/runs/{run_id}/retry", status_code=202)
    async def retry(run_id: uuid.UUID) -> dict[str, Any]:
        try:
            run = runs.retry(run_id)
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc
        if run is None:
            raise HTTPException(409, "only a finished run can be retried")
        return run.snapshot()

    return app


def _jurisdiction(code: str, status: int = 422) -> str:
    """The canonical code of a shipped jurisdiction, or `status`."""
    from codify.jurisdictions import resolve_config

    resolved = resolve_config(code)
    if not resolved.found:
        raise HTTPException(status, f"no jurisdiction config for {code!r}")
    return resolved.code


def _law_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "short_title": row.short_title,
        "doctype": row.doctype,
        "status": row.status,
        "year": row.year,
        "number": row.number,
        "work_uri": row.frbr_work_uri,
    }


def _version_json(row: Any, *, with_akn: bool) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        "law_id": str(row.law_id),
        "expression_uri": row.expression_uri,
        "language": row.language,
        "expression_date": row.expression_date.isoformat() if row.expression_date else None,
        "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
        "structural_quality_grade": row.structural_quality_grade,
    }
    if with_akn:
        out["akn_xml"] = row.akn_xml
    return out
