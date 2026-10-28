"""Scheduling of body-translation batches across concurrent lanes."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import openai
import pytest

from codify.translate.anchors import SourceUnit
from codify.translate.exemplars import ExemplarPool
from codify.translate.translate_bodies import LANE_BATCHES, translate_bodies

ACTIVE_SOURCE = "eng"
INERT_SOURCE = "zzz"


def _unit(i: int, kind: str = "article") -> SourceUnit:
    body = f"The authority shall publish register entry number {i} within the stated period."
    return SourceUnit(
        akn_eid=f"{kind}_{i}",
        kind=kind,
        akn_type=kind,
        number=str(i),
        depth=1,
        heading=None,
        body_text=body,
    )


class _RecordingLLM:
    """Echoes each body with a prefix; records prompts and peak in-flight calls."""

    def __init__(self, fail_first_eids: frozenset[str] = frozenset()) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.prompts: dict[str, str] = {}
        self.fail_first_eids = fail_first_eids
        self.outage_first_eids: frozenset[str] = frozenset()

    async def chat_schema(self, prompt: str, schema: Any, **kw: Any) -> Any:
        payload = json.loads(prompt[prompt.rfind("Batch:\n") + len("Batch:\n") :])
        self.prompts[payload[0]["eid"]] = prompt
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
        finally:
            self.in_flight -= 1
        if payload[0]["eid"] in self.outage_first_eids:
            raise openai.APIConnectionError(request=httpx.Request("POST", "http://gateway"))
        if payload[0]["eid"] in self.fail_first_eids:
            raise ValueError("model refused")
        return schema.model_validate(
            {"blocks": [{"eid": e["eid"], "lines": [f"T: {e['body']}"]} for e in payload]}
        )


def _pool(source: str) -> ExemplarPool:
    return ExemplarPool(target_language="Target", source_language=source)


async def _run(
    units: list[SourceUnit], llm: _RecordingLLM, source: str, concurrency: int = 25
) -> Any:
    return await translate_bodies(
        units,
        target_language="Target",
        notes_block="",
        llm=llm,  # type: ignore[arg-type]
        concurrency=concurrency,
        batch_size=8,
        exemplar_pool=_pool(source),
    )


def _first_body(units: list[SourceUnit], batch: int) -> str:
    return units[batch * 8].body_text


async def test_inert_pool_runs_every_batch_in_parallel_in_source_order() -> None:
    units = [_unit(i) for i in range(80)]
    llm = _RecordingLLM()
    progress: list[int] = []

    outcome = await translate_bodies(
        units,
        target_language="Target",
        notes_block="",
        llm=llm,  # type: ignore[arg-type]
        concurrency=4,
        batch_size=8,
        on_progress=lambda done, total, _label: progress.append(done),
        exemplar_pool=_pool(INERT_SOURCE),
    )

    assert llm.max_in_flight == 4
    assert [b.eid for b in outcome.response.bodies] == [u.akn_eid for u in units]
    assert [b.lines for b in outcome.response.bodies] == [[f"T: {u.body_text}"] for u in units]
    assert progress == list(range(1, 11))


async def test_active_pool_splits_chapterless_act_into_serial_lanes() -> None:
    lanes = 5
    units = [_unit(i) for i in range(lanes * LANE_BATCHES * 8)]
    llm = _RecordingLLM()

    outcome = await _run(units, llm, ACTIVE_SOURCE)

    assert llm.max_in_flight == lanes
    assert [b.eid for b in outcome.response.bodies] == [u.akn_eid for u in units]
    for batch in range(lanes * LANE_BATCHES):
        prompt = llm.prompts[units[batch * 8].akn_eid]
        assert prompt.count("  Source: ") == min(3, batch % LANE_BATCHES)
        if batch % LANE_BATCHES:
            assert f"Target: T: {_first_body(units, batch - 1)}" in prompt


async def test_chapters_keep_one_serial_lane_and_their_own_pool() -> None:
    units: list[SourceUnit] = []
    starts: list[int] = []
    for c in range(3):
        starts.append(len(units))
        units.append(_unit(c, kind="chapter"))
        units.extend(_unit(100 * (c + 1) + i) for i in range(23))
    llm = _RecordingLLM()

    outcome = await _run(units, llm, ACTIVE_SOURCE)

    assert llm.max_in_flight == 3
    assert [b.eid for b in outcome.response.bodies] == [u.akn_eid for u in units]
    for start in starts:
        assert "Already-agreed" not in llm.prompts[units[start].akn_eid]
        for b in (1, 2):
            prompt = llm.prompts[units[start + b * 8].akn_eid]
            assert f"Target: T: {units[start + (b - 1) * 8].body_text}" in prompt


async def test_failed_batch_falls_back_and_does_not_feed_the_pool() -> None:
    units = [_unit(i) for i in range(24)]
    llm = _RecordingLLM(fail_first_eids=frozenset({units[0].akn_eid}))

    outcome = await _run(units, llm, ACTIVE_SOURCE)

    assert [b.eid for b in outcome.response.bodies] == [u.akn_eid for u in units]
    assert all(b.lines == ["[UNTRANSLATED]"] for b in outcome.response.bodies[:8])
    assert outcome.bodies_fallback_to_source == 8
    assert {f["code"] for f in outcome.flags} == {"translate_batch_fallback"}
    assert "Already-agreed" not in llm.prompts[units[8].akn_eid]
    assert f"Target: T: {units[8].body_text}" in llm.prompts[units[16].akn_eid]


async def test_connection_error_cancels_the_other_lanes() -> None:
    units = [_unit(i) for i in range(80)]
    llm = _RecordingLLM()
    llm.outage_first_eids = frozenset({units[0].akn_eid})

    with pytest.raises(openai.APIConnectionError):
        await _run(units, llm, INERT_SOURCE, concurrency=2)
    assert llm.in_flight == 0
    started = len(llm.prompts)
    await asyncio.sleep(0.05)

    assert started < 10
    assert len(llm.prompts) == started
