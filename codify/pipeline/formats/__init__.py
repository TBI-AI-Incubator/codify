"""Source dispatch, pick PDF vs EU directive based on the input."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from codify.pipeline.events import Failed, IngestionEvent

if TYPE_CHECKING:
    from codify.core.llm import LLMClient


_EU_HOSTS = {"eur-lex.europa.eu", "publications.europa.eu"}
_XML_SUFFIXES = {".xml", ".akn", ".fmx"}


def looks_like_eu(source: Path | str) -> bool:
    """Directive/AKN-XML source: local XML by suffix (case-insensitive), remote XML
    only from EU hosts (no SSRF). The single routing predicate, shared with the
    ingest workflow so the two lanes cannot disagree on a host or a suffix case."""
    if isinstance(source, Path):
        return source.suffix.lower() in _XML_SUFFIXES
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        # hostname, not netloc: strips a port and any userinfo, so a spoof like
        # `eur-lex.europa.eu.evil.com` or `user@evil` never matches, and a legit
        # `eur-lex.europa.eu:443` still does.
        return parsed.hostname in _EU_HOSTS
    # Bare local path string.
    return Path(source).suffix.lower() in _XML_SUFFIXES


def _is_html_path(source: Path | str) -> bool:
    """A local EU act stored as HTML: Cellar's rendering of a pre-FORMEX act. A
    URL is not one; remote EU sources pass only through the host check."""
    if isinstance(source, str) and urlparse(source).scheme:
        return False
    return Path(source).suffix.lower() in {".html", ".htm"}


async def dispatch(
    source: Path | str,
    jurisdiction_code: str,
    *,
    llm: "LLMClient | None" = None,
    model: str | None = None,
    frbr_work_uri: str | None = None,
    language: str = "eng",
) -> AsyncIterator[IngestionEvent]:
    if looks_like_eu(source) or (jurisdiction_code == "eu" and _is_html_path(source)):
        # XML from a non-EU jurisdiction is a publisher's native AKN
        # (legislation.gov.uk, Laws.Africa), deterministic normalise-only
        # ingest. The EU path keeps FORMEX/AKN4EU handling.
        if jurisdiction_code != "eu":
            from codify.pipeline.formats import akn_native

            async for event in akn_native.ingest(source, jurisdiction_code):
                yield event
            return
        from codify.pipeline.formats import eu_directive

        async for event in eu_directive.ingest(
            source, jurisdiction_code, frbr_work_uri=frbr_work_uri, language=language
        ):
            yield event
        return

    if llm is None:
        yield Failed(stage="dispatch", error="PDF ingestion requires an LLMClient")
        return

    from codify.pipeline.formats import pdf

    async for event in pdf.ingest(source, jurisdiction_code, llm=llm, model=model):
        yield event


__all__ = ["dispatch", "looks_like_eu"]
