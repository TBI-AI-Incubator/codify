"""Recall gate against EC screening-note anchor verdicts."""

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
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures" / "eu" / "screening-notes"


def _fixtures() -> list[Path]:
    if not FIXTURE_DIR.exists():
        return []
    return sorted(p for p in FIXTURE_DIR.glob("*.json"))


@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.parametrize("fixture_path", _fixtures(), ids=lambda p: p.stem)
async def test_ec_screening_precision_recall(fixture_path: Path) -> None:
    spec = json.loads(fixture_path.read_text(encoding="utf-8"))
    anchors = spec.get("anchors", [])
    if not anchors:
        pytest.skip(f"{fixture_path.name} has no anchors")

    directive_path = REPO_ROOT / spec["directive_fixture"]
    domestic_path = REPO_ROOT / spec["domestic_fixture"]

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

    by_number = {r.directive_eid: r for r in report.results}
    from codify.compare.scaffold import iter_assessable

    number_to_eid = {p.number: p.akn_eid for p in iter_assessable(directive) if p.number}

    matches = 0
    total = len(anchors)
    mismatches: list[str] = []
    for anchor in anchors:
        number = anchor["directive_number"]
        eid = number_to_eid.get(number)
        if eid is None or eid not in by_number:
            pytest.fail(f"anchor article {number!r} did not resolve in {fixture_path.name}")
        actual = by_number[eid].verdict
        expected = anchor["expected_verdict"]
        if actual == expected:
            matches += 1
        else:
            mismatches.append(f"art {number}: actual={actual} expected={expected}")

    threshold = total - max(1, total // 5)
    assert matches >= threshold, (
        f"recall: {matches}/{total} below threshold {threshold}/{total}; "
        f"mismatches:\n  " + "\n  ".join(mismatches)
    )
