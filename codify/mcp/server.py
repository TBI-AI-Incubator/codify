"""Read tools for an MCP client: each read is the library call the HTTP server makes
for it, and compare_versions is the CLI's compare; one implementation behind every surface."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.settings import database_url

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
ClientFactory = Callable[[], Any]

# One AKN body per call; a statute book's largest acts run to a few megabytes.
XML_CAP_CHARS = 1_000_000
# One model call per reference provision, so a compare is bounded before it starts.
COMPARE_CAP_PROVISIONS = 200

TOOL_NAMES = frozenset(
    {
        "search_provisions",
        "list_laws",
        "get_law",
        "get_version",
        "list_jurisdictions",
        "get_jurisdiction",
        "compare_versions",
    }
)

# What a client needs to name and read a jurisdiction; the parsing patterns stay out.
_JURISDICTION_FIELDS = {
    "code",
    "name",
    "name_local",
    "name_en",
    "type",
    "tradition",
    "calendar",
    "languages",
    "authoritative_language",
    "default_document_class",
    "tier",
    "coverage_status",
}
_DOCUMENT_CLASS_FIELDS = {"label", "short_label", "akn_element", "basic_unit", "frbr_subtype"}

_Limit = Annotated[int, Field(ge=1, le=500)]
_Offset = Annotated[int, Field(ge=0)]
_K = Annotated[int, Field(ge=1, le=100)]


def _embedding_client() -> Any:
    from codify.cli_store import _embedding_client

    return _embedding_client()


def _llm_client() -> Any:
    from codify.cli_store import _llm_client

    return _llm_client(None)


class _Clients:
    """One client per factory for the process, built on first use, closed at shutdown."""

    def __init__(self) -> None:
        self._built: dict[ClientFactory, Any] = {}

    def get(self, factory: ClientFactory) -> Any:
        """The client, or the CLI helper's "not configured" message as a tool error."""
        if factory not in self._built:
            try:
                self._built[factory] = factory()
            except SystemExit as exc:
                raise ToolError(str(exc)) from None
        return self._built[factory]

    async def close(self) -> None:
        for built in self._built.values():
            transport = getattr(built, "client", None)  # the library clients own one
            if transport is not None:
                await transport.close()
        self._built.clear()


def _id(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ToolError(f"{value!r} is not a {what} id") from None


def _no_data() -> ToolError:
    return ToolError("this installation carries no jurisdiction data")


def _jurisdiction(code: str) -> str:
    """The canonical code of a shipped jurisdiction, as the HTTP routes resolve it."""
    from codify.jurisdictions import resolve_config

    resolved = resolve_config(code)
    if not resolved.found:
        raise ToolError(f"no jurisdiction config for {code!r}")
    return resolved.code


def create_server(
    *,
    sessions: SessionFactory | None = None,
    embedding_client: ClientFactory = _embedding_client,
    llm_client: ClientFactory = _llm_client,
    xml_cap: int = XML_CAP_CHARS,
    compare_cap: int = COMPARE_CAP_PROVISIONS,
) -> MCPServer[None]:
    """The server. `sessions` defaults to one engine on `POSTGRES_URL`, disposed
    when the server stops; the client factories default to the CLI's."""
    engine = None
    if sessions is None:
        engine = create_async_engine(database_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)

    clients = _Clients()

    @asynccontextmanager
    async def lifespan(_: MCPServer[None]) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await clients.close()
            if engine is not None:
                await engine.dispose()

    server: MCPServer[None] = MCPServer(
        name="codify",
        instructions="Read-only access to a store of legislation in Akoma Ntoso.",
        lifespan=lifespan,
    )

    @server.tool(
        description="Hybrid (lexical and vector) search over the provisions of one "
        "jurisdiction. Returns matches best first, each with its provision id, eId, "
        "score and text."
    )
    async def search_provisions(
        query: str, jurisdiction: str, language: str | None = None, k: _K = 10
    ) -> dict[str, Any]:
        from codify.retrieve.hybrid import retrieve

        code = _jurisdiction(jurisdiction)
        client = clients.get(embedding_client)
        async with sessions() as session:
            matches = await retrieve(
                session,
                query,
                embedding_client=client,
                jurisdiction_code=code,
                language=language,
                k=k,
            )
        return {
            "query": query,
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

    @server.tool(
        description="Stored laws, newest year first, filtered by jurisdiction, doctype, "
        "year or a title query. Returns a page of law summaries with their ids."
    )
    async def list_laws(
        jurisdiction: str | None = None,
        doctype: str | None = None,
        year: int | None = None,
        q: str | None = None,
        limit: _Limit = 50,
        offset: _Offset = 0,
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

    @server.tool(
        description="One law by id. Returns its title, doctype, year, number, work URI, "
        "jurisdiction and every stored version (without the XML)."
    )
    async def get_law(law_id: str) -> dict[str, Any]:
        from codify.storage import list_versions
        from codify.storage.laws import get_law_with_jurisdiction

        key = _id(law_id, "law")
        async with sessions() as session:
            found = await get_law_with_jurisdiction(session, key)
            if found is None:
                raise ToolError(f"no law {law_id}")
            row, code = found
            versions, cursor = await list_versions(session, key, with_akn=False)
            while cursor is not None:  # every page: the contract is every version
                page, cursor = await list_versions(session, key, cursor=cursor, with_akn=False)
                versions.extend(page)
        return {
            "id": str(row.id),
            "title": row.title,
            "short_title": row.short_title,
            "doctype": row.doctype,
            "status": row.status,
            "year": row.year,
            "number": row.number,
            "work_uri": row.frbr_work_uri,
            "jurisdiction": code,
            "versions": [_version_json(v) for v in versions],
        }

    @server.tool(
        description="One version by id. Returns its expression URI, language, dates and "
        "quality grade; with include_xml, the Akoma Ntoso XML too, cut at the size cap "
        "and flagged when cut."
    )
    async def get_version(version_id: str, include_xml: bool = False) -> dict[str, Any]:
        from codify.storage import get_version, get_version_akn_length

        key = _id(version_id, "version")
        async with sessions() as session:
            # The body is loaded only when asked for; its length is measured in the database.
            row = await get_version(session, key, with_akn=include_xml)
            if row is None:
                raise ToolError(f"no version {version_id}")
            length = len(row.akn_xml) if include_xml else await get_version_akn_length(session, key)
        out = _version_json(row)
        out["akn_xml_length"] = length
        if include_xml:
            out["akn_xml"] = row.akn_xml[:xml_cap]
            out["akn_xml_truncated"] = len(row.akn_xml) > xml_cap
        return out

    @server.tool(
        description="Every jurisdiction this installation carries a configuration for. "
        "Returns code, name and languages for each."
    )
    async def list_jurisdictions() -> dict[str, Any]:
        from codify.jurisdictions import JURISDICTIONS_DIR, load_config

        if not JURISDICTIONS_DIR.exists():
            raise _no_data()
        items = []
        for entry in sorted(JURISDICTIONS_DIR.iterdir()):
            if (entry / "config.json").exists():
                config = load_config(entry.name)
                items.append(
                    {"code": config.code, "name": config.name, "languages": list(config.languages)}
                )
        return {"items": items}

    @server.tool(
        description="One jurisdiction's configuration by code. Returns its names, legal "
        "tradition, calendar, languages and document classes."
    )
    async def get_jurisdiction(code: str) -> dict[str, Any]:
        from codify.jurisdictions import JURISDICTIONS_DIR, JurisdictionConfigError, load_config

        if not JURISDICTIONS_DIR.exists():
            raise _no_data()
        try:
            config = load_config(code.strip().lower())
        except JurisdictionConfigError:
            raise ToolError(f"no jurisdiction {code!r}") from None
        out = config.model_dump(mode="json", include=_JURISDICTION_FIELDS)
        out["document_classes"] = {
            key: cls.model_dump(mode="json", include=_DOCUMENT_CLASS_FIELDS)
            for key, cls in config.document_classes.items()
        }
        return out

    @server.tool(
        description="Compare two stored versions: each provision of the reference is "
        "assessed against the domestic version through the chat model, one call per "
        f"reference provision, refused above {compare_cap} of them. Returns the report: "
        "a summary (aligned, partial, gap) and every alignment, keyed by the reference "
        "provision's eId."
    )
    async def compare_versions(
        reference_version_id: str, domestic_version_id: str
    ) -> dict[str, Any]:
        from codify.akn.io import parse_akn
        from codify.compare.comparator import compare
        from codify.compare.scaffold import is_excluded, is_structural, iter_assessable
        from codify.storage import get_version

        docs = []
        async with sessions() as session:
            for ref in (reference_version_id, domestic_version_id):
                row = await get_version(session, _id(ref, "version"))
                if row is None:
                    raise ToolError(f"no version {ref}")
                docs.append(parse_akn(row.akn_xml))
        # The comparator's own set: a structural heading is graded without a model call.
        count = sum(
            1
            for p in iter_assessable(docs[0])
            if not is_structural(p) and not is_excluded(p, docs[0])
        )
        if count > compare_cap:
            raise ToolError(
                f"the reference has {count} provisions to assess; this tool compares "
                f"at most {compare_cap}"
            )
        llm = clients.get(llm_client)
        embeddings = clients.get(embedding_client)
        report = await compare(docs[0], docs[1], llm=llm, embedding_client=embeddings)
        return report.model_dump(mode="json")

    return server


def _version_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "law_id": str(row.law_id),
        "expression_uri": row.expression_uri,
        "language": row.language,
        "expression_date": row.expression_date.isoformat() if row.expression_date else None,
        "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
        "structural_quality_grade": row.structural_quality_grade,
    }
