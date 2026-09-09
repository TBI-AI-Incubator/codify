"""End-to-end tests for the Phase 2 notes-hardening fixes. What each pins:

- Schema cliff: one blank entry no longer evicts the whole slab; the rest
  survive with a ``partial`` status and a non-zero ``entries_dropped``.
- ``_notes_call`` catches a ``ValueError`` out of the notes call and returns
  ``failed``. The stub raises it directly, so nothing here shows that the real
  client converts a schema failure into one.
- Deterministic-check downgrade propagates: an Arabic definitions header with
  empty ``defined_terms`` yields ``audit["notes_status"] == "partial"``.
- ``_fold_status`` over mixed slabs.

Not covered here, despite the docstring having claimed it: a source without
``<preface>`` cannot host the anchor block, and nothing asserts the flag that
should land. ``_akn`` takes ``with_preface`` and no test passes it ``False``.
"""

from __future__ import annotations

from typing import Any

import pytest
from lxml import etree

from codify.akn import AKN_NS
from codify.translate import translate_document
from codify.translate.notes import (
    NotesOutcome,
    _filter_blank_entries,
    _fold_status,
)


def _akn(body_text: str, *, with_preface: bool = True) -> str:
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
    preface = "<preface><longTitle><p>title</p></longTitle></preface>" if with_preface else ""
    return (
        f"<akomaNtoso xmlns='{ns}'>"
        f"<act>{meta}{preface}"
        "<body>"
        "<article eId='art_1'>"
        "<num>1</num>"
        "<content>"
        f"<p>{body_text}</p>"
        "</content>"
        "</article>"
        "</body>"
        "</act>"
        "</akomaNtoso>"
    )


class _MixedBlankNotesLLM:
    """First notes call: three defined_terms, one with blank ``target``.
    Body call: echoes source. Simulates the LLM emitting a mostly-good
    glossary with a single unusable entry."""

    def __init__(self) -> None:
        self.notes_calls = 0

    async def chat_schema_stream(
        self,
        prompt: str,
        schema: Any,
        *,
        on_partial: Any = None,
        system: str | None = None,
        model: str | None = None,
    ) -> Any:
        self.notes_calls += 1
        return schema.model_validate(
            {
                "target_language": "English",
                "defined_terms": [
                    {"source": "Ministry", "target": "the Ministry", "note": ""},
                    {"source": "Fund", "target": "", "note": "blank on purpose"},
                    {"source": "Court", "target": "the Court", "note": ""},
                ],
                "terms_of_art": [],
                "named_entities": [],
                "deontic_conventions": "",
                "structural_conventions": "",
                "ambiguities": [],
            }
        )

    async def chat_schema(
        self, prompt: str, schema: Any, *, system: str | None = None, model: str | None = None
    ) -> Any:
        import json

        batch_start = prompt.rfind("Batch:\n")
        payload = json.loads(prompt[batch_start + len("Batch:\n") :])
        return schema.model_validate(
            {
                "blocks": [
                    {
                        "eid": e["eid"],
                        "heading": e["heading"],
                        "lines": e["body"].split("\n") if e["body"] else [],
                    }
                    for e in payload
                ]
            }
        )

    async def chat(
        self, prompt: str, system: str | None = None, model: str | None = None, **kw: Any
    ) -> str:
        return prompt.split("Title to translate:\n", 1)[-1].strip()


class _FailingNotesLLM(_MixedBlankNotesLLM):
    """Notes call raises ValueError (what the LLM client re-raises on
    schema/parse failures). Body call succeeds."""

    async def chat_schema_stream(
        self,
        prompt: str,
        schema: Any,
        *,
        on_partial: Any = None,
        system: str | None = None,
        model: str | None = None,
    ) -> Any:
        raise ValueError("Model returned invalid structured output: {truncated}")


class TestSchemaCliff:
    @pytest.mark.asyncio
    async def test_blank_entries_do_not_evict_the_slab(self) -> None:
        llm = _MixedBlankNotesLLM()
        result = await translate_document(
            _akn("A minor provision."), target_language="English", llm=llm
        )
        # Two of the three defined_terms survive; the blank-target one drops.
        surviving = result.notes["defined_terms"]
        assert len(surviving) == 2
        sources = {t["source"] for t in surviving}
        assert sources == {"Ministry", "Court"}
        # Status downgrades to partial and the count is surfaced.
        assert result.audit["notes_status"] == "partial"
        assert result.notes["entries_dropped"] == 1


class TestNotesFailedPath:
    @pytest.mark.asyncio
    async def test_valueerror_from_llm_produces_failed_status(self) -> None:
        llm = _FailingNotesLLM()
        result = await translate_document(
            _akn("A minor provision."), target_language="English", llm=llm
        )
        assert result.audit["notes_status"] == "failed"
        assert "notes_error" in result.audit
        assert result.audit["notes_error"]
        assert any(f["code"] == "notes_failed" for f in result.flags)


class TestDeterministicCheckPropagation:
    @pytest.mark.asyncio
    async def test_ar_definitions_header_empty_terms_downgrades_audit(self) -> None:
        # Source contains an Arabic definitions header at start of line;
        # the LLM stub emits empty defined_terms so the check must fire
        # and downgrade the audit status to partial.
        class _EmptyNotesLLM(_MixedBlankNotesLLM):
            async def chat_schema_stream(self, prompt, schema, **kw):  # type: ignore[no-untyped-def]
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

        source = _akn("body")
        # Insert the Arabic definitions header as the article body so the
        # bluebell walker picks it up.
        source = source.replace("<p>body</p>", "<p>التعريفات</p>")
        result = await translate_document(
            source, target_language="English", llm=_EmptyNotesLLM(), source_language="ara"
        )
        assert result.audit["notes_status"] == "partial"
        assert any(f["code"] == "notes_definitions_empty" for f in result.flags)


class TestNotesRideOutOfBand:
    """Notes must not land inside the translated AKN body or preface.
    They ride on ``TranslationResult.notes`` so downstream callers can
    render them out-of-band. Emitting them into the AKN leaked the
    ``TRANSLATION NOTES (binding)...`` prose into every delivered
    document."""

    @pytest.mark.asyncio
    async def test_translated_akn_does_not_carry_notes_block(self) -> None:
        result = await translate_document(
            _akn("body"), target_language="English", llm=_MixedBlankNotesLLM()
        )
        root = etree.fromstring(result.akn_xml.encode())
        # No `<block name="translation-notes">` anywhere in the tree.
        for block in root.iter(f"{{{AKN_NS}}}block"):
            assert block.get("name") != "translation-notes"
        # No stray "TRANSLATION NOTES" prose in any <p>.
        for p in root.iter(f"{{{AKN_NS}}}p"):
            assert "TRANSLATION NOTES" not in (p.text or "")
        # But the caller still receives the notes dict on the result.
        assert result.notes is not None


class TestFoldStatus:
    def test_all_complete_folds_to_complete(self) -> None:
        assert _fold_status(["complete", "complete"]) == "complete"

    def test_all_failed_folds_to_failed(self) -> None:
        assert _fold_status(["failed", "failed"]) == "failed"

    def test_mixed_complete_and_failed_folds_to_partial(self) -> None:
        assert _fold_status(["complete", "failed"]) == "partial"

    def test_mixed_complete_and_partial_folds_to_partial(self) -> None:
        assert _fold_status(["complete", "partial"]) == "partial"

    def test_empty_folds_to_failed(self) -> None:
        assert _fold_status([]) == "failed"


class TestFilterBlankEntries:
    def test_drops_blank_target(self) -> None:
        raw = {
            "target_language": "English",
            "defined_terms": [
                {"source": "a", "target": "A", "note": ""},
                {"source": "b", "target": "", "note": ""},
            ],
            "terms_of_art": [],
            "named_entities": [],
            "deontic_conventions": "",
            "structural_conventions": "",
            "ambiguities": [],
        }
        filtered, dropped = _filter_blank_entries(raw)
        assert dropped == 1
        assert len(filtered["defined_terms"]) == 1
        assert filtered["defined_terms"][0]["source"] == "a"

    def test_drops_whitespace_only_source(self) -> None:
        raw = {
            "target_language": "English",
            "defined_terms": [{"source": "   ", "target": "X", "note": ""}],
            "terms_of_art": [],
            "named_entities": [],
            "deontic_conventions": "",
            "structural_conventions": "",
            "ambiguities": [],
        }
        filtered, dropped = _filter_blank_entries(raw)
        assert dropped == 1
        assert filtered["defined_terms"] == []


class TestNotesOutcomeShape:
    def test_notes_outcome_carries_status_and_error(self) -> None:
        outcome = NotesOutcome(notes={"target_language": "X"}, status="failed", error="boom")
        assert outcome.status == "failed"
        assert outcome.error == "boom"
