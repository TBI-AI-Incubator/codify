"""End-to-end Phase 4 tests: judge phase runs when enabled, verdicts land
on audit, fail-level findings emit judge_<dimension> flags."""

from __future__ import annotations

from typing import Any

import pytest

from codify.akn import AKN_NS
from codify.translate import translate_document


def _akn(articles: list[tuple[str, str]]) -> str:
    """Build a minimal AKN with N articles under one chapter."""
    ns = AKN_NS
    meta = (
        "<meta>"
        "<identification source='#test'>"
        "<FRBRWork>"
        "<FRBRthis value='/akn/xx/act/2020/1/main'/>"
        "<FRBRuri value='/akn/xx/act/2020/1'/>"
        "<FRBRdate date='2020-01-01' name='Generation'/>"
        "<FRBRauthor href='#test'/>"
        "<FRBRcountry value='xx'/>"
        "</FRBRWork>"
        "<FRBRExpression>"
        "<FRBRthis value='/akn/xx/act/2020/1/eng@2020-01-01/main'/>"
        "<FRBRuri value='/akn/xx/act/2020/1/eng@2020-01-01'/>"
        "<FRBRdate date='2020-01-01' name='Generation'/>"
        "<FRBRauthor href='#test'/>"
        "<FRBRlanguage language='eng'/>"
        "</FRBRExpression>"
        "<FRBRManifestation>"
        "<FRBRthis value='/akn/xx/act/2020/1/eng@2020-01-01/main.xml'/>"
        "<FRBRuri value='/akn/xx/act/2020/1/eng@2020-01-01.akn'/>"
        "<FRBRdate date='2020-01-01' name='Generation'/>"
        "<FRBRauthor href='#test'/>"
        "</FRBRManifestation>"
        "</identification>"
        "</meta>"
    )
    body = ""
    for eid, text in articles:
        body += f"<article eId='{eid}'><num>{eid}</num><content><p>{text}</p></content></article>"
    return (
        f"<akomaNtoso xmlns='{ns}'>"
        f"<act>{meta}"
        "<preface><longTitle><p>title</p></longTitle></preface>"
        f"<body>{body}</body>"
        "</act>"
        "</akomaNtoso>"
    )


class _EchoLLM:
    """Body-fill echoes source; notes empty. No judge behaviour here."""

    async def chat_schema_stream(self, prompt: str, schema: Any, **kw: Any) -> Any:
        return schema.model_validate(
            {
                "target_language": "English",
                "defined_terms": [],
                "terms_of_art": [],
                "named_entities": [],
                "deontic_conventions": "",
                "structural_conventions": "",
                "ambiguities": [],
            }
        )

    async def chat_schema(self, prompt: str, schema: Any, **kw: Any) -> Any:
        import json

        batch_start = prompt.rfind("Batch:\n")
        payload = json.loads(prompt[batch_start + len("Batch:\n") :])
        return schema.model_validate(
            {
                "blocks": [
                    {
                        "eid": e["eid"],
                        "heading": e["heading"],
                        "lines": (e["body"] or "").split("\n") if e["body"] else [],
                    }
                    for e in payload
                ]
            }
        )

    async def chat(self, prompt: str, **kw: Any) -> str:
        return prompt.split("Title to translate:\n", 1)[-1].strip()


class _JudgeFailLLM(_EchoLLM):
    """When judge_enabled fires, this second LLM emits a coverage fail
    on every judged provision."""

    async def chat_schema(self, prompt: str, schema: Any, **kw: Any) -> Any:
        # Detect the judge prompt by its distinct opening line.
        if "Score this translation" in prompt:
            eid = prompt.split("Provision eid: ", 1)[1].split("\n", 1)[0].strip()
            return schema.model_validate(
                {
                    "eid": eid,
                    "coverage": 4,
                    "fidelity": 1,
                    "terminology": 0,
                    "register": 1,
                    "overall": "fail",
                    "issues": [
                        {
                            "dimension": "coverage",
                            "detail": "penalty subclause dropped in translation",
                            "source_span": "fine of 100 dinars",
                            "target_span": "fine",
                        }
                    ],
                }
            )
        return await super().chat_schema(prompt, schema, **kw)


class TestJudgePhaseWireIn:
    @pytest.mark.asyncio
    async def test_judge_disabled_default_no_verdicts(self) -> None:
        source = _akn([("art_1", "Every person shall pay a fine of 100 dinars.")])
        result = await translate_document(source, target_language="English", llm=_EchoLLM())
        assert result.audit["judge_enabled"] is False
        assert result.audit["judge_verdicts"] == {}
        assert result.audit["judge_sample_size"] == 0

    @pytest.mark.asyncio
    async def test_judge_enabled_populates_verdicts(self) -> None:
        source = _akn(
            [
                ("art_1", "Every person shall pay a fine of 100 dinars."),
                ("art_2", "The Minister shall publish the report annually."),
                ("art_3", "The court shall record the fine as final."),
            ]
        )
        llm = _JudgeFailLLM()
        result = await translate_document(
            source,
            target_language="English",
            llm=llm,
            judge_enabled=True,
        )
        assert result.audit["judge_enabled"] is True
        assert result.audit["judge_sample_size"] >= 1
        # Every judged verdict has the four sub-scores + overall.
        for verdict in result.audit["judge_verdicts"].values():
            assert set(verdict.keys()) >= {
                "eid",
                "coverage",
                "fidelity",
                "terminology",
                "register",
                "overall",
                "issues",
            }

    @pytest.mark.asyncio
    async def test_fail_verdicts_emit_flags(self) -> None:
        source = _akn(
            [
                ("art_1", "Every person shall pay a fine of 100 dinars."),
                ("art_2", "The Minister shall publish the report annually."),
            ]
        )
        result = await translate_document(
            source,
            target_language="English",
            llm=_JudgeFailLLM(),
            judge_enabled=True,
        )
        judge_flags = [f for f in result.flags if f["code"].startswith("judge_")]
        assert len(judge_flags) >= 1
        assert any(f["code"] == "judge_coverage" for f in judge_flags)


class TestTheCreationDateReachesTheWritePath:
    """`translate_document` is the only caller the workflow has, so the
    parameter existing on `apply_translation_to_akn` proves nothing on its own."""

    @pytest.mark.asyncio
    async def test_the_expression_carries_the_date_it_was_given(self) -> None:
        source = _akn([("art_1", "Every person shall pay a fine of 100 dinars.")])
        result = await translate_document(
            source,
            target_language="French",
            llm=_EchoLLM(),
            expression_date="2026-08-09",
        )
        assert "/akn/xx/act/2020/1/fra@2026-08-09" in result.akn_xml
        assert 'date="2026-08-09" name="translation"' in result.akn_xml

    @pytest.mark.asyncio
    async def test_without_one_the_source_date_stands(self) -> None:
        source = _akn([("art_1", "Every person shall pay a fine of 100 dinars.")])
        result = await translate_document(source, target_language="French", llm=_EchoLLM())
        assert "/akn/xx/act/2020/1/fra@2020-01-01" in result.akn_xml
