"""The thin HTTP server: the library's reads behind routes, and ingest as a run.

Nothing here is a service in its own right. Every route is one library call;
the only state is the in-memory run table, which a restart empties.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.pipeline.events import Complete, IngestionEvent
from codify.serve.runs import RunTable
from codify.settings import database_url

SessionFactory = Callable[[], AsyncSession]


class IngestUrl(BaseModel):
    url: str
    jurisdiction: str
    title: str | None = None


def _ingest_runner(
    sessions: async_sessionmaker[AsyncSession],
) -> Callable[[dict[str, Any]], AsyncIterator[IngestionEvent]]:
    """Run the pipeline, then store what it produced so the reads can see it."""

    async def run(params: dict[str, Any]) -> AsyncIterator[IngestionEvent]:
        from codify.cli_store import _descriptors, _llm_client
        from codify.pipeline import ingest_document
        from codify.storage import save_document

        async for event in ingest_document(
            params["source"], params["jurisdiction"], llm=_llm_client(params.get("model"))
        ):
            if isinstance(event, Complete):
                doctype, year, number = _descriptors(event.document)
                async with sessions() as session:
                    await save_document(
                        session,
                        event.document,
                        jurisdiction_code=params["jurisdiction"],
                        law_title=params.get("title") or event.document.frbr_work_uri,
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
    runs = RunTable(runner or _ingest_runner(sessions))
    # Uploads live as long as the runs that read them: the process.
    uploads = tempfile.TemporaryDirectory(prefix="codify-uploads-")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        uploads.cleanup()
        await engine.dispose()

    app = FastAPI(title="codify", docs_url="/docs", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.runs = runs

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
        from codify.jurisdictions import JurisdictionDataMissing, load_config

        try:
            config = load_config(code)
        except JurisdictionDataMissing as exc:
            raise HTTPException(404, str(exc)) from exc
        return config.model_dump(mode="json")

    @app.get("/jurisdictions/{code}/laws")
    async def laws_for(code: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return await laws(jurisdiction=code, limit=limit, offset=offset)

    @app.get("/laws")
    async def laws(
        jurisdiction: str | None = None,
        doctype: str | None = None,
        year: int | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        from codify.storage import list_laws

        async with sessions() as session:
            rows, _ = await list_laws(
                session,
                jurisdictions=[jurisdiction] if jurisdiction else None,
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
            versions, _ = await list_versions(session, law_id)
        return {
            **_law_json(row),
            "jurisdiction": code,
            "versions": [_version_json(v, with_akn=False) for v in versions],
        }

    @app.get("/laws/versions/{version_id}")
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
        q: str, jurisdiction: str, k: int = 10, language: str | None = None
    ) -> dict[str, Any]:
        from codify.cli_store import _embedding_client
        from codify.retrieve.hybrid import retrieve

        async with sessions() as session:
            matches = await retrieve(
                session,
                q,
                embedding_client=_embedding_client(),
                jurisdiction_code=jurisdiction,
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
        suffix = Path(file.filename or "upload").suffix or ".pdf"
        source = Path(uploads.name) / f"{uuid.uuid4()}{suffix}"
        source.write_bytes(await file.read())
        run = runs.enqueue(
            "ingest", {"source": str(source), "jurisdiction": jurisdiction, "title": title}
        )
        return run.snapshot()

    @app.post("/runs/ingest-url", status_code=202)
    async def ingest_url(body: IngestUrl) -> dict[str, Any]:
        run = runs.enqueue(
            "ingest", {"source": body.url, "jurisdiction": body.jurisdiction, "title": body.title}
        )
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
        run = runs.retry(run_id)
        if run is None:
            raise HTTPException(409, "only a finished run can be retried")
        return run.snapshot()

    return app


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
