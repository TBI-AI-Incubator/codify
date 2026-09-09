"""PDF ingestion, integration test against a synthetic scan, skipped without LLM."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from codify.pipeline import ingest_document
from codify.pipeline.events import Complete

# A synthetic Atlantis (xa) gazette scan from the factory; the open tree ships no
# real legislation. Ingesting it needs an LLM, so this test skips offline.
SYNTHETIC_SCAN = Path(__file__).resolve().parents[1] / "fixtures" / "pdf" / "synthetic-xa-scan.pdf"


def _llm_or_skip():
    """Build a LiteLLM-backed client from env and probe it; skip if unreachable."""
    try:
        from codify.core.llm import create_llm_client
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LLM client factory unavailable: {exc}")
    base_url = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
    api_key = os.environ.get("LITELLM_API_KEY", "sk-codify-dev")
    model = os.environ.get("LITELLM_MODEL", "gemini-3.6-flash")
    try:
        client = create_llm_client(base_url=base_url, api_key=api_key, model=model)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LLM client unreachable: {exc}")

    async def _probe() -> None:
        await client.chat("ping")

    try:
        asyncio.run(asyncio.wait_for(_probe(), timeout=5.0))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LLM probe failed ({base_url}): {exc}")
    return client


# Also live_llm: this is the only test that runs body fill against a real model, and
# without the marker the live workflow deselects it while the offline run skips it on
# the probe, so nothing exercised the path at all.
@pytest.mark.integration
@pytest.mark.live_llm
def test_synthetic_scan_ingests_end_to_end() -> None:
    """Smoke: a synthetic Atlantis gazette scan PDF → Document with non-empty body."""
    if not SYNTHETIC_SCAN.exists():
        pytest.skip(f"fixture missing: {SYNTHETIC_SCAN}")
    llm = _llm_or_skip()

    async def go() -> list:
        return [e async for e in ingest_document(SYNTHETIC_SCAN, "xa", llm=llm)]

    events = asyncio.run(go())
    assert events, "no events yielded"
    last = events[-1]
    assert isinstance(last, Complete), f"expected Complete, got {last!r}: {events}"
    assert last.document.body, "Document body is empty"
    assert last.document.frbr_work_uri.startswith("/akn/xa/"), (
        f"unexpected FRBR work URI: {last.document.frbr_work_uri}"
    )
