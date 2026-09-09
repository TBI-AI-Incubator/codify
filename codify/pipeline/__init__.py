"""Source-document ingestion. PDF (LLM) or EU directive XML (deterministic)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from codify.pipeline.events import (
    Complete,
    Enriched,
    Failed,
    IngestionEvent,
    MetadataExtracted,
    PageExtracted,
    Parsed,
    Structured,
    ValidationIssued,
)
from codify.pipeline.formats import dispatch

if TYPE_CHECKING:
    from codify.core.llm import LLMClient


async def ingest_document(
    source: Path | str,
    jurisdiction_code: str,
    *,
    llm: "LLMClient | None" = None,
    model: str | None = None,
    frbr_work_uri: str | None = None,
    language: str = "eng",
) -> AsyncIterator[IngestionEvent]:
    """Yield progress events for `source` until Complete or Failed.

    `source` is a filesystem path or URL. Routing:
    - `eur-lex.europa.eu` URL or `.xml`/`.akn`/`.fmx` suffix → EU directive
    - everything else → PDF (LLM-driven body-fill against an anchor scaffold)

    `model` overrides the LLMClient's default for the body-fill call;
    None routes to the client's configured model. EU directive ingest is
    fully deterministic and ignores it.
    """
    async for event in dispatch(
        source,
        jurisdiction_code,
        llm=llm,
        model=model,
        frbr_work_uri=frbr_work_uri,
        language=language,
    ):
        yield event


__all__ = [
    "Complete",
    "Enriched",
    "Failed",
    "IngestionEvent",
    "MetadataExtracted",
    "PageExtracted",
    "Parsed",
    "Structured",
    "ValidationIssued",
    "ingest_document",
]
