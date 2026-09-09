"""Phase 3 review-pass coverage: exemplar wiring, register_drift flag,
fallback-safe pool feed, `is required to` label, applicable flag."""

from __future__ import annotations

from typing import Any

import pytest

from codify.akn import AKN_NS
from codify.translate import translate_document
from codify.translate.audit import audit_translation
from codify.translate.exemplars import Exemplar, ExemplarPool


def _akn(units: list[tuple[str, str, str, str]]) -> str:
    """Build a minimal AKN with a preface and articles.
    ``units`` is [(chapter_eid, chapter_heading, art_eid, art_body), ...]."""
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
    body: list[str] = []
    seen_chapters: set[str] = set()
    for chapter_eid, chapter_heading, art_eid, art_body in units:
        if chapter_eid and chapter_eid not in seen_chapters:
            body.append(f"<chapter eId='{chapter_eid}'><heading>{chapter_heading}</heading>")
            seen_chapters.add(chapter_eid)
        body.append(
            f"<article eId='{art_eid}'>"
            f"<num>{art_eid}</num>"
            f"<content><p>{art_body}</p></content>"
            "</article>"
        )
    # Close chapters. Simple: single-chapter test cases only.
    for _ in seen_chapters:
        body.append("</chapter>")
    return (
        f"<akomaNtoso xmlns='{ns}'>"
        f"<act>{meta}"
        "<preface><longTitle><p>title</p></longTitle></preface>"
        f"<body>{''.join(body)}</body>"
        "</act>"
        "</akomaNtoso>"
    )


class _PromptCapturingLLM:
    """Echoes source body as translation (identity) so the exemplar-pool
    invariant is preserved by returning slightly-differentiated text. Records
    every user prompt so a test can assert exemplar-block presence."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

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

        self.prompts.append(prompt)
        batch_start = prompt.rfind("Batch:\n")
        payload = json.loads(prompt[batch_start + len("Batch:\n") :])
        return schema.model_validate(
            {
                "blocks": [
                    {
                        "eid": e["eid"],
                        "heading": e["heading"],
                        # Prefix with "the" so target != source (so the pool
                        # invariant that rejects identity translations does
                        # not evict every candidate).
                        "lines": [f"the {ln}" if ln else "" for ln in (e["body"] or "").split("\n")]
                        if e["body"]
                        else [],
                    }
                    for e in payload
                ]
            }
        )

    async def chat(self, prompt: str, **kw: Any) -> str:
        return prompt.split("Title to translate:\n", 1)[-1].strip()


class TestExemplarWiring:
    @pytest.mark.asyncio
    async def test_exemplar_block_appears_in_later_batch_prompts(self) -> None:
        # Two deontic-rich articles in one chapter, batch_size=1 forces
        # two batches; batch 2 must see the "Already-agreed translations"
        # block seeded by batch 1's output.
        source = _akn(
            [
                (
                    "chp_1",
                    "General",
                    "art_1",
                    "Every person shall submit a written notice to the authority.",
                ),
                (
                    "chp_1",
                    "General",
                    "art_2",
                    "Every operator shall pay the annual fee before the deadline.",
                ),
            ]
        )
        llm = _PromptCapturingLLM()
        result = await translate_document(
            source,
            target_language="English",
            llm=llm,
            batch_size=1,
            source_language="eng",
        )
        # Batches from a single-chapter act include the chapter unit as its
        # own batch when batch_size=1. Filter to article-body prompts.
        article_prompts = [p for p in llm.prompts if '"kind": "article"' in p]
        assert len(article_prompts) == 2
        assert "Already-agreed translations" not in article_prompts[0]
        assert "Already-agreed translations" in article_prompts[1]
        # And the pool activity surfaces on the audit for observability.
        assert result.audit["exemplar_pool_active"] is True
        assert result.audit["exemplar_picks"] >= 1

    @pytest.mark.asyncio
    async def test_unregistered_source_language_pool_inactive(self) -> None:
        source = _akn([("", "", "art_1", "Une disposition ordinaire.")])
        result = await translate_document(
            source,
            target_language="French",
            llm=_PromptCapturingLLM(),
            source_language="fra",
        )
        assert result.audit["exemplar_pool_active"] is False


class TestRegisterDriftFlag:
    def test_flag_surfaces_on_deontic_split(self) -> None:
        # Direct audit_translation call proves the flag path.
        shall_text = " ".join(["Every person shall pay a fine."] * 87)
        must_text = " ".join(["The Minister must publish it."] * 12)
        result = audit_translation("source", f"{shall_text} {must_text}", {}, target_language="eng")
        assert result["register_drifted"] is True
        assert result["register_drift_applicable"] is True
        assert result["deontic_counts"]["shall"] == 87
        assert result["deontic_counts"]["must"] == 12

    def test_unregistered_target_marked_not_applicable(self) -> None:
        result = audit_translation("source", "arbitrary target text", {}, target_language="fra")
        assert result["register_drift_applicable"] is False
        assert result["deontic_counts"] == {}
        assert result["register_drifted"] is False

    def test_is_required_to_key_is_human_readable(self) -> None:
        # 25 "is required to", 3 "shall", the compound label must appear
        # in the counts dict as the plain phrase, not a regex fragment.
        req_text = " ".join(["Every operator is required to submit a report."] * 25)
        shall_text = "The Minister shall verify. " * 3
        result = audit_translation("source", f"{req_text} {shall_text}", {}, target_language="eng")
        assert "is required to" in result["deontic_counts"]
        # And no leaked regex fragment either.
        assert "is\\s+required\\s+to" not in result["deontic_counts"]
        # 25 "is required to" (~89%) vs 3 "shall" (~11%) crosses threshold.
        assert result["register_drifted"] is True


class TestExemplarPoolFallbackSafe:
    def test_pool_rejects_source_equals_target(self) -> None:
        # Identity exemplar (LLM failure signature) must be refused.
        pool = ExemplarPool(target_language="French", source_language="eng")
        source = [
            {
                "eid": "art_1",
                "body": "Every person shall pay a fine to the authority annually.",
            }
        ]
        translated = [
            {
                "eid": "art_1",
                "lines": ["Every person shall pay a fine to the authority annually."],
            }
        ]
        assert pool.pick_from(source, translated) is None
        assert pool.entries == []
        assert pool.scans == 1
        assert pool.picks == 0

    def test_capacity_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            ExemplarPool(target_language="English", source_language="eng", capacity=0)

    def test_capacity_uses_deque_maxlen(self) -> None:
        pool = ExemplarPool(target_language="English", source_language="eng", capacity=2)
        for i in range(5):
            pool.add(
                Exemplar(
                    source=f"A person shall pay {i} dinars annually to the fund.",
                    target=f"target {i}",
                )
            )
        assert len(pool.entries) == 2
        assert "3" in pool.entries[0].target
        assert "4" in pool.entries[1].target


class TestChapterStripeOrdering:
    @pytest.mark.asyncio
    async def test_serial_within_chapter_visible_in_prompt_order(self) -> None:
        # Chapter with three articles at batch_size=1 → three batches
        # serial within the chapter; batch N's prompt sees exemplars
        # from batch N-1.
        source = _akn(
            [
                (
                    "chp_1",
                    "One",
                    "art_1",
                    "Every person shall pay a fine promptly to the authority.",
                ),
                (
                    "chp_1",
                    "One",
                    "art_2",
                    "Every operator shall report to the Ministry annually.",
                ),
                (
                    "chp_1",
                    "One",
                    "art_3",
                    "Every citizen shall obey the rules published herein.",
                ),
            ]
        )
        llm = _PromptCapturingLLM()
        await translate_document(
            source,
            target_language="English",
            llm=llm,
            batch_size=1,
            source_language="eng",
        )
        # Serial-within: article batches see accumulating exemplars. Filter
        # to the article batches (chapter unit gets its own batch slot).
        article_prompts = [p for p in llm.prompts if '"kind": "article"' in p]
        assert len(article_prompts) == 3
        pools = [p.count("Source:") for p in article_prompts]
        assert pools[0] == 0
        assert pools[1] >= 1
        assert pools[2] >= pools[1]
