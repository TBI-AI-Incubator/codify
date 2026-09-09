"""Recall gate, comparator alignment % within 10pp of captured baselines."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from codify.akn.io import parse_akn
from codify.compare import compare
from codify.core.llm import create_llm_client
from codify.embed.client import EmbeddingClient
from codify.pipeline.formats.eu_directive import formex_to_akn4eu, parse_akn4eu

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures" / "eval"


def _fixtures() -> list[Path]:
    if not FIXTURE_DIR.exists():
        return []
    return sorted(p for p in FIXTURE_DIR.glob("*-baseline.json"))


@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.parametrize("fixture_path", _fixtures(), ids=lambda p: p.stem)
async def test_reproduces_prior_alignment_within_10pct(fixture_path: Path) -> None:
    spec = json.loads(fixture_path.read_text(encoding="utf-8"))
    directive_path = REPO_ROOT / spec["directive_fixture"]
    domestic_path = REPO_ROOT / spec["domestic_fixture"]
    expected_pct: dict[str, float] = spec["expected_alignment_pct"]

    directive_xml = formex_to_akn4eu(directive_path.read_text(encoding="utf-8"))
    directive = parse_akn4eu(directive_xml)
    domestic = parse_akn(domestic_path.read_text(encoding="utf-8"))

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

    report = await compare(directive, domestic, llm=llm, embedding_client=embedding, seed=42)

    for verdict, expected in expected_pct.items():
        actual = getattr(report.summary, f"{verdict}_pct")
        assert abs(actual - expected) <= 10.0, (
            f"{verdict}: actual {actual:.1f}% diverges from baseline {expected:.1f}% "
            f"by more than 10pp"
        )
