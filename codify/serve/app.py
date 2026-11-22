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
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.core.tracing import error_fields
from codify.jurisdictions import JurisdictionConfig
from codify.pipeline.events import Complete, Failed
from codify.serve.runs import QueueFull, Run, RunEvent, RunTable, Stored, describe_failure
from codify.serve.schemas import (
    Cancelled,
    Health,
    IngestUrl,
    JurisdictionSummary,
    LawDetail,
    LawPage,
    LawSummary,
    RunSnapshot,
    SearchMatch,
    SearchResult,
    VersionDetail,
    VersionPage,
    VersionSummary,
)
from codify.settings import database_url
from codify.storage.documents import VersionDocument

logger = structlog.get_logger()
SessionFactory = Callable[[], AsyncSession]
_LIMIT = Query(50, ge=1, le=500, description="Most laws to return.")
_OFFSET = Query(0, ge=0, description="Laws to skip before the page starts.")
_CODE = PathParam(description="Jurisdiction code, such as `xa`.")
_LAW_ID = PathParam(description="Law id, as returned by the law listing.")
_VERSION_ID = PathParam(description="Version id, as listed under its law.")
_RUN_ID = PathParam(description="Run id, as returned when the run was started.")


class _Clients:
    """One chat and one embeddings client per process, built on first use, closed at shutdown."""

    def __init__(self) -> None:
        self._built: dict[str, Any] = {}

    def llm(self) -> Any:
        from codify.cli_store import _env, _llm_client

        # The XML lanes need no model; without one a PDF fails at dispatch, and says so.
        if "llm" not in self._built:
            self._built["llm"] = _llm_client(None) if _env("LLM_BASE_URL") else None
        return self._built["llm"]

    def embeddings(self) -> Any:
        from codify.cli_store import _embedding_client

        if "embeddings" not in self._built:
            self._built["embeddings"] = _embedding_client()
        return self._built["embeddings"]

    async def close(self) -> None:
        for built in self._built.values():
            if built is not None:
                await built.client.close()
        self._built.clear()


def _ingest_runner(
    sessions: async_sessionmaker[AsyncSession], clients: _Clients
) -> Callable[[dict[str, Any]], AsyncIterator[RunEvent]]:
    """Run the pipeline, then store what it produced so the reads can see it."""

    async def run(params: dict[str, Any]) -> AsyncIterator[RunEvent]:
        from codify.cli_store import _descriptors
        from codify.pipeline import ingest_document
        from codify.pipeline.events import MetadataExtracted
        from codify.storage import get_version, save_document

        llm = clients.llm()
        read_title = ""
        async for event in ingest_document(params["source"], params["jurisdiction"], llm=llm):
            if isinstance(event, MetadataExtracted):
                read_title = str(event.metadata.get("title") or "")
            if isinstance(event, Complete):
                doctype, year, number = _descriptors(event.document)
                async with sessions() as session:
                    version_id = await save_document(
                        session,
                        event.document,
                        jurisdiction_code=params["jurisdiction"],
                        law_title=params.get("title") or read_title or event.document.frbr_work_uri,
                        doctype=doctype,
                        year=year,
                        number=number,
                        akn_xml=event.akn_xml,
                    )
                    law_id = (await get_version(session, version_id)).law_id  # type: ignore[union-attr]
                    try:
                        embedder = clients.embeddings()
                    except SystemExit as exc:
                        embedder = None
                        logger.warning(
                            "stored_unsearchable", version_id=str(version_id), fix=str(exc)
                        )
                    if embedder is not None:
                        from codify.storage.embeddings import embed_and_stamp

                        try:
                            await embed_and_stamp(
                                session, version_id, client=embedder, path_context=False
                            )
                        except Exception as exc:  # noqa: BLE001
                            await session.rollback()
                            logger.warning(
                                "embed_failed", version_id=str(version_id), **error_fields(exc)
                            )
                            yield Failed(stage="persist", error=describe_failure(exc))
                            return
                    await session.commit()
                yield Stored(version_id=version_id, law_id=law_id)
            yield event

    return run


class EventStream(StreamingResponse):
    media_type = "text/event-stream"


def create_app(
    *,
    runner: Callable[[dict[str, Any]], AsyncIterator[RunEvent]] | None = None,
    database: str | None = None,
) -> FastAPI:
    """`database` overrides `POSTGRES_URL`; nothing connects until a route needs to."""
    engine = create_async_engine(database_url(database))
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

    clients = _Clients()
    runs: RunTable = RunTable(runner or _ingest_runner(sessions, clients), on_forget=drop_upload)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await runs.shutdown()
        uploads.cleanup()
        await clients.close()
        await engine.dispose()

    app = FastAPI(
        title="codify", version=package_version("codify-core"), docs_url="/docs", lifespan=lifespan
    )
    app.state.sessions = sessions
    app.state.runs = runs
    app.state.uploads = Path(uploads.name)
    app.state.clients = clients

    @app.get(
        "/health",
        summary="Check the server is up",
        description="Answers without touching the database.",
    )
    async def health() -> Health:
        return Health(status="ok")

    @app.get(
        "/jurisdictions",
        summary="List jurisdictions",
        description=(
            "Every jurisdiction with an installed configuration: its code, name and languages."
        ),
    )
    async def jurisdictions() -> list[JurisdictionSummary]:
        from codify.jurisdictions import JURISDICTIONS_DIR, load_config

        out = []
        for entry in sorted(JURISDICTIONS_DIR.iterdir()):
            if (entry / "config.json").exists():
                config = load_config(entry.name)
                out.append(
                    JurisdictionSummary(
                        code=config.code, name=config.name, languages=list(config.languages)
                    )
                )
        return out

    @app.get(
        "/jurisdictions/{code}",
        summary="Get a jurisdiction's configuration",
        description=(
            "The full configuration the pipeline reads for this jurisdiction. 404 when none is "
            "installed or it does not load."
        ),
    )
    async def jurisdiction(code: str = _CODE) -> JurisdictionConfig:
        from codify.jurisdictions import JurisdictionConfigError, load_config

        try:
            config = load_config(code.strip().lower())
        except JurisdictionConfigError as exc:  # missing or malformed: neither is ours
            raise HTTPException(404, str(exc)) from exc
        return config

    @app.get(
        "/jurisdictions/{code}/laws",
        summary="List a jurisdiction's laws",
        description="Stored laws of one jurisdiction, newest year first. 404 for an unknown code.",
    )
    async def laws_for(code: str = _CODE, limit: int = _LIMIT, offset: int = _OFFSET) -> LawPage:
        return await laws(jurisdiction=_jurisdiction(code, 404), limit=limit, offset=offset)

    @app.get(
        "/laws",
        summary="List laws",
        description=(
            "Stored laws, newest year first, filtered by jurisdiction, doctype, year or a title "
            "query. A title query orders results by how well the title matches."
        ),
    )
    async def laws(
        # Annotated, so `laws_for` can call this with the plain defaults.
        jurisdiction: Annotated[
            str | None, Query(description="Jurisdiction code. Unset lists every jurisdiction.")
        ] = None,
        doctype: Annotated[str | None, Query(description="Document class, such as `act`.")] = None,
        year: Annotated[int | None, Query(description="Year of the law.")] = None,
        q: Annotated[
            str | None,
            Query(description="Title query. When set, results are ordered by title match."),
        ] = None,
        limit: int = _LIMIT,
        offset: int = _OFFSET,
    ) -> LawPage:
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
        return LawPage(
            items=[LawSummary.model_validate(r, from_attributes=True) for r in rows],
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/laws/{law_id}",
        summary="Get a law",
        description="One law with every stored version, without the XML.",
    )
    async def law(law_id: uuid.UUID = _LAW_ID) -> LawDetail:
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
        return LawDetail(
            id=row.id,
            jurisdiction=code,
            title=row.title,
            short_title=row.short_title,
            doctype=row.doctype,
            status=row.status,
            year=row.year,
            number=row.number,
            work_uri=row.frbr_work_uri,
            versions=[VersionSummary.model_validate(v, from_attributes=True) for v in versions],
        )

    @app.get(
        "/versions/{version_id}",
        summary="Get a version",
        description="One version with its Akoma Ntoso XML.",
    )
    async def version(version_id: uuid.UUID = _VERSION_ID) -> VersionDetail:
        from codify.storage import get_version

        async with sessions() as session:
            row = await get_version(session, version_id)
        if row is None:
            raise HTTPException(404, "no such version")
        return VersionDetail.model_validate(row, from_attributes=True)

    @app.get(
        "/versions/{version_id}/document",
        summary="Get a version as a document tree",
        description="The version as the reader's section tree, not as AKN.",
    )
    async def version_document(version_id: uuid.UUID = _VERSION_ID) -> VersionDocument:
        from codify.storage.documents import get_version_document

        async with sessions() as session:
            document = await get_version_document(session, version_id)
        if document is None:
            raise HTTPException(404, "no such version")
        return document

    @app.get(
        "/laws/{law_id}/versions",
        summary="List a law's versions",
        description=(
            "One page of a law's versions, without the XML. Pass `next_cursor` back as `cursor` "
            "for the next page."
        ),
    )
    async def law_versions(
        law_id: uuid.UUID = _LAW_ID,
        limit: int = Query(100, ge=1, le=500, description="Most versions to return."),
        cursor: uuid.UUID | None = Query(
            None, description="The `next_cursor` of the previous page. Unset starts at the first."
        ),
    ) -> VersionPage:
        from codify.storage import list_versions

        async with sessions() as session:
            rows, next_cursor = await list_versions(
                session, law_id, limit=limit, cursor=cursor, with_akn=False
            )
        return VersionPage(
            items=[VersionSummary.model_validate(v, from_attributes=True) for v in rows],
            next_cursor=next_cursor,
        )

    @app.get(
        "/search",
        summary="Search provisions",
        description=(
            "Hybrid (lexical and vector) search over one jurisdiction's provisions, best match "
            "first. 503 when no embeddings endpoint is configured."
        ),
    )
    async def search(
        q: str = Query(description="The search text."),
        jurisdiction: str = Query(description="Jurisdiction code, such as `xa`."),
        k: int = Query(10, ge=1, le=100, description="Most matches to return."),
        language: str | None = Query(
            None,
            description="ISO 639-3 code of the expression to search. Unset searches each law's "
            "original language; a law without that expression falls back to it.",
        ),
    ) -> SearchResult:
        from codify.retrieve.hybrid import retrieve
        from codify.storage.provisions import provision_contexts

        code = _jurisdiction(jurisdiction)
        try:
            embedding_client = clients.embeddings()
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
            where = await provision_contexts(session, [m.provision_id for m in matches])
        return SearchResult(
            query=q,
            matches=[
                SearchMatch(
                    provision_id=m.provision_id,
                    eid=m.akn_eid,
                    score=m.rrf_score,
                    text=m.text,
                    version_id=where[m.provision_id].version_id,
                    law_id=where[m.provision_id].law_id,
                    law_title=where[m.provision_id].law_title,
                    work_uri=where[m.provision_id].frbr_work_uri,
                    jurisdiction=where[m.provision_id].jurisdiction_code,
                )
                for m in matches
                if m.provision_id in where
            ],
        )

    @app.post(
        "/runs/ingest",
        status_code=202,
        summary="Ingest an uploaded document",
        description=(
            "Starts a run that converts the uploaded file to Akoma Ntoso and stores it. 429 when "
            "the run queue is full."
        ),
    )
    async def ingest(
        file: UploadFile = File(description="The source document, such as a PDF."),
        jurisdiction: str = Form(description="Jurisdiction code, such as `xa`."),
        title: str | None = Form(
            None, description="Title to store the law under. Unset uses the title read from it."
        ),
    ) -> RunSnapshot:
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
        return _snapshot(run)

    @app.post(
        "/runs/ingest-url",
        status_code=202,
        summary="Ingest a document by URL",
        description=(
            "Starts a run that fetches and ingests an EUR-Lex or Publications Office URL under "
            "the `eu` jurisdiction, the only lane that fetches. 422 for any other URL or "
            "jurisdiction."
        ),
    )
    async def ingest_url(body: IngestUrl) -> RunSnapshot:
        from codify.jurisdictions import resolve_config
        from codify.pipeline.formats import looks_like_eu

        if not resolve_config("eu").found:
            raise HTTPException(
                422,
                "URL ingest needs the eu jurisdiction config, which is not installed "
                "(GET /jurisdictions lists the installed configs)",
            )
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
        return _snapshot(run)

    @app.get(
        "/runs",
        summary="List runs",
        description=(
            "Runs this server process holds, newest first. Runs are kept in memory and lost on "
            "restart."
        ),
    )
    async def list_runs() -> list[RunSnapshot]:
        return [_snapshot(r) for r in runs.list()]

    @app.get(
        "/runs/{run_id}", summary="Get a run", description="One run's status and events so far."
    )
    async def run_state(run_id: uuid.UUID = _RUN_ID) -> RunSnapshot:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "no such run")
        return _snapshot(run)

    @app.get(
        "/runs/{run_id}/stream",
        response_class=EventStream,
        summary="Stream a run's events",
        description=(
            "Server-sent events: every event so far, then each new one, then an `end` event when "
            "the run finishes."
        ),
    )
    async def run_stream(request: Request, run_id: uuid.UUID = _RUN_ID) -> EventStream:
        if runs.get(run_id) is None:
            raise HTTPException(404, "no such run")

        async def body() -> AsyncIterator[bytes]:
            async for event in runs.stream(run_id):
                if await request.is_disconnected():
                    return
                yield f"event: {event.get('kind', 'event')}\ndata: {json.dumps(event)}\n\n".encode()
            yield b"event: end\ndata: {}\n\n"

        return EventStream(body())

    @app.post(
        "/runs/{run_id}/cancel",
        summary="Cancel a run",
        description=(
            "Cancels a queued or running run. 409 when the run is unknown or has already finished."
        ),
    )
    async def cancel(run_id: uuid.UUID = _RUN_ID) -> Cancelled:
        if not runs.cancel(run_id):
            raise HTTPException(409, "not running")
        return Cancelled(cancelled=run_id)

    @app.post(
        "/runs/{run_id}/retry",
        status_code=202,
        summary="Retry a run",
        description=(
            "Starts a new run with the finished run's inputs; the old run keeps its outcome. "
            "409 for an unknown or unfinished run, 429 when the queue is full."
        ),
    )
    async def retry(run_id: uuid.UUID = _RUN_ID) -> RunSnapshot:
        try:
            run = runs.retry(run_id)
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc
        if run is None:
            raise HTTPException(409, "only a finished run can be retried")
        return _snapshot(run)

    return app


def _jurisdiction(code: str, status: int = 422) -> str:
    """The canonical code of a shipped jurisdiction, or `status`."""
    from codify.jurisdictions import resolve_config

    resolved = resolve_config(code)
    if not resolved.found:
        raise HTTPException(status, f"no jurisdiction config for {code!r}")
    return resolved.code


def _snapshot(run: Run) -> RunSnapshot:
    return RunSnapshot(**run.snapshot())
