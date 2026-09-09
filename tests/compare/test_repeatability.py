"""Three-run repeatability gate, same seed, ±5% drift on summary counts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codify.akn.io import parse_akn
from codify.compare import compare
from codify.core.llm import create_llm_client
from codify.embed.client import EmbeddingClient
from codify.pipeline.formats.eu_directive import formex_to_akn4eu, parse_akn4eu

REPO_ROOT = Path(__file__).resolve().parents[4]
DIRECTIVE = REPO_ROOT / "data" / "fixtures" / "eu" / "2016-943" / "eng.xml"
DOMESTIC_AKN = REPO_ROOT / "data" / "fixtures" / "al" / "162-2020.akn.xml"


@pytest.mark.integration
@pytest.mark.live_llm
async def test_three_run_repeatability_within_5_pct() -> None:
    if not DIRECTIVE.exists() or not DOMESTIC_AKN.exists():
        pytest.skip(f"missing fixture: {DIRECTIVE.name} or {DOMESTIC_AKN.name}")

    directive_xml = formex_to_akn4eu(DIRECTIVE.read_text(encoding="utf-8"))
    directive = parse_akn4eu(directive_xml)
    domestic = parse_akn(DOMESTIC_AKN.read_text(encoding="utf-8"))

    llm = create_llm_client(
        base_url=os.getenv("LITELLM_BASE_URL", "http://localhost:4000/v1"),
        api_key=os.getenv("LITELLM_API_KEY", "sk-codify-dev"),
        model=os.getenv("LITELLM_MODEL", "gemini-3.6-flash"),
    )
    embedding = EmbeddingClient(
        base_url=os.getenv("LITELLM_BASE_URL", "http://localhost:4000/v1"),
        api_key=os.getenv("LITELLM_API_KEY", "sk-codify-dev"),
        model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-2"),
    )

    reports = []
    for run_idx in range(3):
        try:
            reports.append(
                await compare(directive, domestic, llm=llm, embedding_client=embedding, seed=42)
            )
        except Exception as exc:
            pytest.fail(f"run {run_idx} raised {type(exc).__name__}: {exc}")

    total = reports[0].summary.total
    tolerance = max(1, total * 5 // 100)
    for verdict in ("aligned", "partial", "gap"):
        counts = [getattr(r.summary, verdict) for r in reports]
        spread = max(counts) - min(counts)
        assert spread <= tolerance, (
            f"{verdict} count drift {spread} exceeds tolerance {tolerance} "
            f"(total={total}, runs={counts})"
        )
