"""`codify load`, `codify search` and `codify compare`: the library behind a prompt.

`ingest-one` writes a bundle and stops. These three take it the rest of the way:
into Postgres, out through hybrid search, and side by side with another version.
Nothing here is new logic; each command is one library call and its plumbing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from codify.akn.document import Document
from codify.settings import database_url

_DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2"


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    """One engine per command, disposed on the way out."""
    engine = create_async_engine(database_url())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


def _env(*names: str) -> str | None:
    """The first set environment variable among `names`."""
    return next((v for n in names if (v := os.environ.get(n))), None)


def _embedding_client() -> Any:
    """An OpenAI-compatible embeddings client from the environment.

    `EMBEDDING_*` win; the `LITELLM_*` chat settings are the fallback so one
    gateway serves both.
    """
    from codify.embed.client import EmbeddingClient

    base_url = _env("EMBEDDING_BASE_URL", "LITELLM_BASE_URL")
    if not base_url:
        raise SystemExit("set EMBEDDING_BASE_URL (or LITELLM_BASE_URL) to an embeddings endpoint")
    return EmbeddingClient(
        base_url=base_url,
        api_key=_env("EMBEDDING_API_KEY", "LITELLM_API_KEY", "LITELLM_MASTER_KEY")
        or "sk-codify-dev",
        model=_env("EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODEL,
    )


def _llm_client(model: str | None) -> Any:
    from codify.core.llm import LiteLLMClient

    base_url = _env("LITELLM_BASE_URL")
    if not base_url:
        raise SystemExit("set LITELLM_BASE_URL to an OpenAI-compatible chat endpoint")
    return LiteLLMClient(
        base_url=base_url,
        api_key=_env("LITELLM_API_KEY", "LITELLM_MASTER_KEY") or "sk-codify-dev",
        model=model or _env("LITELLM_MODEL") or "gemini-3.7-flash",
        telemetry_mode="direct",
    )


def _bundle_akn(path: Path) -> tuple[str, dict[str, Any]]:
    """The AKN and manifest of a bundle directory, or of a bare AKN file."""
    if path.is_dir():
        akn = path / "final.akn.xml"
        if not akn.exists():
            raise SystemExit(f"{path} holds no final.akn.xml; is it an ingest-one bundle?")
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        return akn.read_text(encoding="utf-8"), manifest
    return path.read_text(encoding="utf-8"), {}


def _descriptors(doc: Document) -> tuple[str, int | None, str]:
    """Doctype, year and number as the pipeline resolved them into the work URI."""
    from cobalt import FrbrUri

    from codify.frbr import UNKNOWN_YEAR

    uri = FrbrUri.parse(doc.frbr_work_uri)
    year = str(doc.work_date.year) if doc.work_date else uri.date[:4]
    # An undated work carries the sentinel year in both the URI and the work date.
    resolved = int(year) if year.isdigit() and int(year) != int(UNKNOWN_YEAR) else None
    return uri.subtype or uri.doctype, resolved, uri.number


async def _load(args: argparse.Namespace) -> int:
    from codify.akn.io import parse_akn
    from codify.storage import save_document

    akn_xml, manifest = _bundle_akn(Path(args.path))
    meta = manifest.get("metadata") or {}
    jurisdiction = args.jurisdiction or manifest.get("jurisdiction")
    if not jurisdiction:
        raise SystemExit("no jurisdiction in the bundle's manifest; pass --jurisdiction")
    title = args.title or meta.get("title")
    if not title:
        raise SystemExit("no title in the bundle's manifest; pass --title")
    doc = parse_akn(akn_xml)
    # The manifest holds only what the model read (a blank year, no doctype).
    doctype, year, number = _descriptors(doc)
    async with _session() as session:
        version_id = await save_document(
            session,
            doc,
            jurisdiction_code=jurisdiction,
            law_title=title,
            doctype=args.doctype or doctype,
            year=args.year or year,
            number=args.number or number,
            akn_xml=akn_xml,
        )
        embedded = 0
        if args.embed:
            from codify.storage.embeddings import embed_version_provisions

            embedded = await embed_version_provisions(
                session, version_id, client=_embedding_client()
            )
        await session.commit()
    print(
        json.dumps(
            {"version_id": str(version_id), "work_uri": doc.frbr_work_uri, "embedded": embedded}
        )
    )
    return 0


async def _search(args: argparse.Namespace) -> int:
    from codify.retrieve.hybrid import retrieve

    async with _session() as session:
        matches = await retrieve(
            session,
            args.query,
            embedding_client=_embedding_client(),
            jurisdiction_code=args.jurisdiction,
            language=args.language,
            k=args.k,
        )
    for m in matches:
        print(json.dumps({"eid": m.akn_eid, "score": round(m.rrf_score, 4), "text": m.text}))
    return 0 if matches else 1


async def _compare(args: argparse.Namespace) -> int:
    from codify.akn.io import parse_akn
    from codify.compare.comparator import compare
    from codify.storage import get_version

    async def load_side(ref: str) -> Any:
        path = Path(ref)
        if path.exists():
            return parse_akn(_bundle_akn(path)[0])
        async with _session() as session:
            version = await get_version(session, uuid.UUID(ref))
            if version is None or not version.akn_xml:
                raise SystemExit(f"no stored version {ref}")
            return parse_akn(version.akn_xml)

    left, right = await load_side(args.left), await load_side(args.right)
    report = await compare(
        left, right, llm=_llm_client(args.model), embedding_client=_embedding_client()
    )
    out = report.model_dump_json(indent=2)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out + "\n")
    return 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    load = sub.add_parser("load", help="write an ingest-one bundle (or an AKN file) into Postgres")
    load.add_argument("path", help="bundle directory or .akn.xml file")
    load.add_argument("--jurisdiction", help="defaults to the bundle's manifest")
    load.add_argument(
        "--title", help="defaults to the title the model read, in the bundle's manifest"
    )
    load.add_argument("--doctype")
    load.add_argument("--year", type=int)
    load.add_argument("--number")
    load.add_argument("--embed", action="store_true", help="also embed its provisions for search")
    load.set_defaults(func=lambda a: asyncio.run(_load(a)))

    search = sub.add_parser(
        "search", help="hybrid search over stored provisions, one JSON match per line"
    )
    search.add_argument("query")
    search.add_argument("--jurisdiction", required=True)
    search.add_argument("--language")
    search.add_argument("-k", type=int, default=10, help="matches to return")
    search.set_defaults(func=lambda a: asyncio.run(_search(a)))

    cmp = sub.add_parser(
        "compare",
        help="compare two versions, by stored id or AKN path, as a JSON report",
    )
    cmp.add_argument("left", help="the reference (directive) side")
    cmp.add_argument("right", help="the domestic side")
    cmp.add_argument("--model", help="chat model for the assessment")
    cmp.add_argument("--out", help="write the report here instead of stdout")
    cmp.set_defaults(func=lambda a: asyncio.run(_compare(a)))
