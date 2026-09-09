"""Unit tests for the translation pipeline (post-CDFY-T1).

Covers the audit + notes-merge surface and the end-to-end
`translate_document` flow with a stubbed LLM. The structural invariant
(source eId set == translation eId set) lives in test_anchors.py.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from codify.akn.io import parse_akn
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.translate import (
    audit_translation,
    translate_document,
    walk_source_units,
)
from codify.translate.notes import _merge_notes, notes_to_prompt

_ACT_BLUEBELL = (
    "PREFACE\n  An Act\nBODY\n"
    "  CHAPTER 1 - General\n"
    "    ARTICLE 1 - Scope\n      This Act applies to all persons.\n"
    "    ARTICLE 2 - Definitions\n      In this Act, 'person' means an individual.\n"
    "  CHAPTER 2 - Duties\n"
    "    ARTICLE 3 - Duty of care\n      Every person shall act with care.\n"
)


def _source_akn_xml() -> str:
    return parse_to_akn(_ACT_BLUEBELL, country="xa", doctype="act", number="0001")


# ── audit_translation ───────────────────────────────────────────────────────


def test_audit_flags_missing_defined_term() -> None:
    notes = {"defined_terms": [{"source": "person", "target": "personne"}]}
    report = audit_translation(_ACT_BLUEBELL, _ACT_BLUEBELL, notes)
    assert report["terms_missing"] == ["personne"]
    assert report["terms_checked"] == 1


def test_audit_content_coverage_identical_is_full() -> None:
    report = audit_translation(_ACT_BLUEBELL, _ACT_BLUEBELL, {"defined_terms": []})
    assert report["content_coverage"] == 1.0


def test_audit_content_coverage_flags_dropped_bodies() -> None:
    """Headers kept, bodies gone, coverage collapses."""
    headers_only = "\n".join(
        ln
        for ln in _ACT_BLUEBELL.split("\n")
        if ln.lstrip().split(" ")[0] in {"PREFACE", "BODY", "CHAPTER", "ARTICLE"}
    )
    report = audit_translation(_ACT_BLUEBELL, headers_only, {"defined_terms": []})
    assert report["content_coverage"] < 0.5


def test_audit_drops_structural_check() -> None:
    """structural_ok is no longer a key, it's an invariant of the pipeline."""
    report = audit_translation(_ACT_BLUEBELL, "anything", {"defined_terms": []})
    assert "structural_ok" not in report
    assert "unit_sequence_diff" not in report


# ── notes merge + render ────────────────────────────────────────────────────


def test_merge_notes_unions_terms_first_wins() -> None:
    a = {"defined_terms": [{"source": "x", "target": "X1"}], "terms_of_art": []}
    b = {"defined_terms": [{"source": "x", "target": "X2"}, {"source": "y", "target": "Y"}]}
    merged = _merge_notes([a, b], target_language="French")
    sources = [t["source"] for t in merged["defined_terms"]]
    assert sources == ["x", "y"]
    assert merged["defined_terms"][0]["target"] == "X1"


def test_notes_to_prompt_renders_terms_and_warnings() -> None:
    notes = {
        "target_language": "French",
        "defined_terms": [{"source": "person", "target": "personne", "note": "an individual"}],
        "terms_of_art": [
            {
                "source": "consideration",
                "target": "contrepartie",
                "false_friend_warning": "considération",
            }
        ],
        "named_entities": [],
        "deontic_conventions": "shall → obligation",
        "structural_conventions": "",
    }
    rendered = notes_to_prompt(notes)
    assert "personne" in rendered
    assert "AVOID: considération" in rendered
    assert "shall → obligation" in rendered


# ── translate_document end-to-end (stubbed LLM) ─────────────────────────────


class _StubLLM:
    """Echoes body text back verbatim (mock translation = identity).

    `chat_schema_stream` returns the notes; `chat_schema` returns one
    TranslatedBlock per source unit with the source body as the translation.
    """

    async def chat_schema_stream(
        self,
        prompt: str,
        schema: Any,
        *,
        on_partial: Any = None,
        system: str | None = None,
        model: str | None = None,
    ) -> Any:
        return schema.model_validate(
            {
                "target_language": "Identity",
                "defined_terms": [{"source": "person", "target": "person", "note": ""}],
                "terms_of_art": [],
                "named_entities": [],
                "deontic_conventions": "",
                "structural_conventions": "",
                "ambiguities": [],
            }
        )

    async def chat_schema(
        self,
        prompt: str,
        schema: Any,
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> Any:
        import json

        # Pull the batch JSON the prompt embeds and echo each entry's body.
        batch_start = prompt.rfind("Batch:\n")
        payload = json.loads(prompt[batch_start + len("Batch:\n") :])
        return schema.model_validate(
            {
                "blocks": [
                    {
                        "eid": entry["eid"],
                        "heading": entry["heading"],
                        "lines": entry["body"].split("\n") if entry["body"] else [],
                    }
                    for entry in payload
                ]
            }
        )

    async def chat(
        self, prompt: str, system: str | None = None, model: str | None = None, **kw: Any
    ) -> str:
        return prompt.split("Title to translate:\n", 1)[-1].strip()


class _DroppingLLM(_StubLLM):
    """Empty body lines, headings preserved, simulates a model dropping all
    body content. The audit's coverage floor should fire."""

    async def chat_schema(
        self,
        prompt: str,
        schema: Any,
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> Any:
        import json

        batch_start = prompt.rfind("Batch:\n")
        payload = json.loads(prompt[batch_start + len("Batch:\n") :])
        return schema.model_validate(
            {
                "blocks": [
                    {
                        "eid": entry["eid"],
                        "heading": entry["heading"],
                        "lines": [],
                    }
                    for entry in payload
                ]
            }
        )


@pytest.mark.asyncio
async def test_translate_document_preserves_eid_set_invariant() -> None:
    """Source eId set equals translation eId set. Guaranteed by the source-clone
    write path; no positional zip alignment required."""
    source_xml = _source_akn_xml()
    result = await translate_document(source_xml, target_language="Identity", llm=_StubLLM())
    src_doc = parse_akn(source_xml)
    tgt_doc = parse_akn(result.akn_xml)
    src_eids = {u.akn_eid for u in walk_source_units(src_doc)}
    tgt_eids = {u.akn_eid for u in walk_source_units(tgt_doc)}
    assert src_eids == tgt_eids


@pytest.mark.asyncio
async def test_translate_document_runs_notes_phase() -> None:
    """Phase 1 fires once via `on_notes`."""
    seen: list[dict[str, Any]] = []
    await translate_document(
        _source_akn_xml(),
        target_language="French",
        llm=_StubLLM(),
        on_notes=seen.append,
    )
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_translate_document_flags_dropped_bodies() -> None:
    """An LLM that returns empty lines for every unit falls back to source
    text, but emits a structured flag per unit + a counter, a bulk run can
    filter on it instead of relying on coverage."""
    result = await translate_document(
        _source_akn_xml(), target_language="French", llm=_DroppingLLM()
    )
    fallback_flags = [f for f in result.flags if f.get("code") == "translate_body_empty"]
    assert len(fallback_flags) > 0
    assert result.audit["bodies_fallback_to_source"] == len(fallback_flags)


class _RaisingBatchLLM(_StubLLM):
    """Every batch call raises, exercises the batch-fail fallback path."""

    async def chat_schema(self, *a: Any, **kw: Any) -> Any:
        raise RuntimeError("simulated outage")


@pytest.mark.asyncio
async def test_batch_failure_flags_every_unit_and_falls_back() -> None:
    result = await translate_document(
        _source_akn_xml(), target_language="French", llm=_RaisingBatchLLM()
    )
    batch_flags = [f for f in result.flags if f.get("code") == "translate_batch_fallback"]
    assert len(batch_flags) > 0
    assert result.audit["bodies_fallback_to_source"] >= len(batch_flags)
    # A genuine model refusal still records the total unit count, so the
    # fallback-ratio gate has a live denominator.
    assert result.audit["units_total"] > 0


class _OutageBatchLLM(_StubLLM):
    """Every batch call raises a connection-class error, a gateway outage."""

    async def chat_schema(self, *a: Any, **kw: Any) -> Any:
        import httpx
        import openai

        raise openai.APIConnectionError(request=httpx.Request("POST", "http://gateway"))


@pytest.mark.asyncio
async def test_gateway_outage_fails_the_run() -> None:
    """A connection-class error during body-fill must fail the run, not degrade
    every unit to an [UNTRANSLATED] source-text block and report success."""
    import openai

    with pytest.raises(openai.APIConnectionError):
        await translate_document(_source_akn_xml(), target_language="French", llm=_OutageBatchLLM())


class _HeadingDroppingLLM(_StubLLM):
    """Returns null heading even when source had one. Body still translated."""

    async def chat_schema(self, prompt: str, schema: Any, **kw: Any) -> Any:
        import json

        batch_start = prompt.rfind("Batch:\n")
        payload = json.loads(prompt[batch_start + len("Batch:\n") :])
        return schema.model_validate(
            {
                "blocks": [
                    {
                        "eid": entry["eid"],
                        "heading": None,
                        "lines": entry["body"].split("\n") if entry["body"] else [],
                    }
                    for entry in payload
                ]
            }
        )


@pytest.mark.asyncio
async def test_null_heading_falls_back_to_source_with_flag() -> None:
    result = await translate_document(
        _source_akn_xml(), target_language="French", llm=_HeadingDroppingLLM()
    )
    heading_flags = [f for f in result.flags if f.get("code") == "translate_heading_fallback"]
    assert len(heading_flags) > 0
    assert result.audit["headings_fallback_to_source"] == len(heading_flags)
    # Source headings ride through into the assembled AKN.
    assert "Scope" in result.akn_xml
    assert "Definitions" in result.akn_xml


def test_translated_block_accepts_structural_keyword_lines() -> None:
    """Body-fill has no Bluebell reparse under the deterministic write path, so
    lines that spell a Bluebell keyword (source schedules sometimes contain
    'POINT ب' as literal prose) land as `<p>` text without structural drift."""
    from codify.translate.translate_bodies import TranslatedBlock

    TranslatedBlock(eid="x", lines=["Article 3 provides for review."])
    TranslatedBlock(eid="x", lines=["PART 1"])
    TranslatedBlock(eid="x", lines=["POINT ب"])


class _PrefaceSeekingLLM(_StubLLM):
    """Records the preface translation prompt so we can assert it was called."""

    def __init__(self) -> None:
        self.preface_prompts: list[str] = []

    async def chat(self, prompt: str, system: str | None = None, **kw: Any) -> str:
        if "preface paragraph below" in prompt:
            self.preface_prompts.append(prompt)
            # Echo every requested slot key, which is what the contract asks of
            # a well-behaved model.
            keys = re.findall(r"\[\[(\d+)\]\]", prompt)
            return "\n\n".join(f"[[{k}]] TRANSLATED_PREFACE" for k in keys)
        return prompt.split("Title to translate:\n", 1)[-1].strip()


@pytest.mark.asyncio
async def test_preface_is_translated_not_silently_dropped() -> None:
    llm = _PrefaceSeekingLLM()
    result = await translate_document(_source_akn_xml(), target_language="French", llm=llm)
    assert llm.preface_prompts, "preface translation was never attempted"
    assert "TRANSLATED_PREFACE" in result.akn_xml


def test_walk_source_flags_skipped_eidless_elements() -> None:
    """Elements without an eId can't survive the scaffold, surface a flag
    so a silent structural drop is visible to the bulk run."""
    from codify.akn.document import Document
    from codify.akn.elements import Chapter
    from codify.translate.anchors import walk_source

    # An hcontainer-shaped child without an eId.
    chapter = Chapter(
        akn_eid="chp_1",
        akn_type="chapter",
        position=0,
        children=[
            Chapter(akn_eid="", akn_type="hcontainer", position=0, children=[]),
        ],
    )
    doc = Document(
        frbr_work_uri="/akn/zz/act/2026/1",
        frbr_expression_uri="/akn/zz/act/2026/1/eng@2026-01-01",
        language="eng",
        expression_date=__import__("datetime").date(2026, 1, 1),
        body=[chapter],
    )
    units, summary = walk_source(doc)
    assert summary.skipped_without_eid == 1
    assert "hcontainer" in summary.skipped_kinds
    assert len(units) == 1  # only the chapter, not the eidless child
