"""Tests for the LLM-as-judge sampling + verdict handling."""

from __future__ import annotations

from typing import Any

import pytest

from codify.translate.anchors import SourceUnit
from codify.translate.judge import (
    JudgeIssue,
    JudgeVerdict,
    issues_to_repair_details,
    judge_provision,
    select_sample,
)


def _u(eid: str, kind: str = "article", body: str = "") -> SourceUnit:
    return SourceUnit(
        akn_eid=eid,
        kind=kind,
        akn_type=kind,
        number=None,
        depth=1,
        heading=None,
        body_text=body or f"body of {eid}",
    )


class _CleanJudgeLLM:
    async def chat_schema(self, prompt: str, schema: Any, **kw: Any) -> Any:
        eid = prompt.split("Provision eid: ", 1)[1].split("\n", 1)[0].strip()
        return schema.model_validate(
            {
                "eid": eid,
                "coverage": 0,
                "fidelity": 0,
                "terminology": 0,
                "register": 0,
                "overall": "pass",
                "issues": [],
            }
        )


class _CoverageFailJudgeLLM:
    async def chat_schema(self, prompt: str, schema: Any, **kw: Any) -> Any:
        eid = prompt.split("Provision eid: ", 1)[1].split("\n", 1)[0].strip()
        return schema.model_validate(
            {
                "eid": eid,
                "coverage": 4,
                "fidelity": 0,
                "terminology": 0,
                "register": 0,
                "overall": "fail",
                "issues": [
                    {
                        "dimension": "coverage",
                        "detail": "subclause on penalty amount missing",
                        "source_span": "shall pay 100 dinars",
                        "target_span": "shall pay",
                    }
                ],
            }
        )


class _RaisingJudgeLLM:
    async def chat_schema(self, prompt: str, schema: Any, **kw: Any) -> Any:
        raise RuntimeError("gateway timeout")


class TestJudgeProvision:
    @pytest.mark.asyncio
    async def test_clean_pass_returns_verdict(self) -> None:
        v = await judge_provision(
            eid="art_1",
            source_text="Every person shall pay a fine.",
            translated_text="Every person shall pay a fine.",
            notes_block="",
            target_language="English",
            llm=_CleanJudgeLLM(),
        )
        assert v is not None
        assert v.overall == "pass"
        assert v.issues == []

    @pytest.mark.asyncio
    async def test_fail_carries_issue(self) -> None:
        v = await judge_provision(
            eid="art_1",
            source_text="Every person shall pay 100 dinars.",
            translated_text="Every person shall pay.",
            notes_block="",
            target_language="English",
            llm=_CoverageFailJudgeLLM(),
        )
        assert v is not None
        assert v.overall == "fail"
        assert v.coverage == 4
        assert len(v.issues) == 1
        assert v.issues[0].dimension == "coverage"

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self) -> None:
        v = await judge_provision(
            eid="art_1",
            source_text="Every person shall pay.",
            translated_text="Every person shall pay.",
            notes_block="",
            target_language="English",
            llm=_RaisingJudgeLLM(),
        )
        assert v is None


class TestSelectSample:
    def test_mandatory_flagged_always_in_sample(self) -> None:
        units = [_u(f"art_{i}") for i in range(1, 11)]
        sample = select_sample(units, flagged_eids={"art_3", "art_7"}, per_chapter=3, cap=20)
        eids = {u.akn_eid for u in sample}
        assert "art_3" in eids
        assert "art_7" in eids

    def test_stratified_per_chapter_default_three(self) -> None:
        units = [
            _u("chp_1", kind="chapter"),
            _u("art_1"),
            _u("art_2"),
            _u("art_3"),
            _u("art_4"),
            _u("art_5"),
            _u("chp_2", kind="chapter"),
            _u("art_6"),
            _u("art_7"),
            _u("art_8"),
            _u("art_9"),
            _u("art_10"),
        ]
        sample = select_sample(units, flagged_eids=set(), per_chapter=3, cap=20)
        assert len(sample) == 6  # 3 per chapter × 2 chapters

    def test_cap_wins_when_mandatory_exceeds_it(self) -> None:
        units = [_u(f"art_{i}") for i in range(1, 30)]
        flagged = {f"art_{i}" for i in range(1, 26)}
        sample = select_sample(units, flagged_eids=flagged, per_chapter=3, cap=20)
        assert len(sample) == 20
        assert all(u.akn_eid in flagged for u in sample)

    def test_deterministic_under_seed(self) -> None:
        units = [_u("chp_1", kind="chapter")] + [_u(f"art_{i}") for i in range(1, 20)]
        s1 = select_sample(units, flagged_eids=set(), per_chapter=3, cap=20, seed="run-1")
        s2 = select_sample(units, flagged_eids=set(), per_chapter=3, cap=20, seed="run-1")
        assert [u.akn_eid for u in s1] == [u.akn_eid for u in s2]

    def test_different_seed_different_sample(self) -> None:
        units = [_u("chp_1", kind="chapter")] + [_u(f"art_{i}") for i in range(1, 20)]
        s1 = select_sample(units, flagged_eids=set(), per_chapter=3, cap=5, seed="run-1")
        s2 = select_sample(units, flagged_eids=set(), per_chapter=3, cap=5, seed="run-2")
        # Different seeds produce different picks (not guaranteed strictly
        # distinct but overwhelmingly likely for small samples).
        assert {u.akn_eid for u in s1} != {u.akn_eid for u in s2}


class TestIssuesToRepairDetails:
    def test_dimension_becomes_code(self) -> None:
        pairs = issues_to_repair_details(
            [JudgeIssue(dimension="coverage", detail="dropped clause")]
        )
        assert pairs == [("judge_coverage", "dropped clause")]

    def test_source_and_target_spans_appended(self) -> None:
        pairs = issues_to_repair_details(
            [
                JudgeIssue(
                    dimension="fidelity",
                    detail="fabricated authority",
                    source_span="Ministry",
                    target_span="The Ministry of Finance",
                )
            ]
        )
        assert pairs[0][0] == "judge_fidelity"
        assert "Ministry" in pairs[0][1]
        assert "Ministry of Finance" in pairs[0][1]


class TestJudgeVerdictSchema:
    def test_out_of_range_score_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            JudgeVerdict(
                eid="art_1",
                coverage=6,
                fidelity=0,
                terminology=0,
                register=0,
                overall="fail",
                issues=[],
            )
