from typing import Any

import pytest

from codify.translate.notes import PinnedTerm, apply_pinned_terms, select_pinned_terms
from codify.translate.translate import translate_document
from tests.translate.test_translate import _source_akn_xml, _StubLLM


def _pin(source: str, target: str = "T", origin: str = "Ruritanian Glossary") -> PinnedTerm:
    return PinnedTerm(source=source, target=target, origin=origin)


def _sources(text: str, *sources: str) -> list[str]:
    return [t.source for t in select_pinned_terms(text, [_pin(s) for s in sources])]


def test_latin_needs_word_boundaries_and_ignores_case() -> None:
    assert _sources("The Licence holder", "licence holder") == ["licence holder"]
    assert _sources("a fact", "act") == []
    assert _sources("an act.", "act") == ["act"]


def test_unspaced_script_matches_as_substring() -> None:
    text = "事業者は許可申請を提出しなければならない。各人々は届出る。"
    assert _sources(text, "許可申請", "人々", "裁判所") == ["許可申請", "人々"]


def test_unspaced_term_ending_in_a_mark_matches_mid_sentence() -> None:
    assert _sources("ဤဥပဒေကို", "ဥပဒေ") == ["ဥပဒေ"]


def test_turkish_dotted_capital_i_folds_to_plain_i() -> None:
    assert _sources("İzin belgesi", "izin") == ["izin"]


def test_arabic_script_ignores_diacritics_and_respects_boundaries() -> None:
    text = "يجب على الْمُرَخَّص له تقديم الطلب"
    assert _sources(text, "المرخص", "طلب") == ["المرخص"]


def test_blank_entries_dropped() -> None:
    assert select_pinned_terms("abc", [PinnedTerm("", "x"), PinnedTerm("abc", " ")]) == []


def test_pinned_wins_over_model_and_origins_recorded() -> None:
    notes: dict[str, Any] = {
        "defined_terms": [{"source": "Permit", "target": "model-permit", "note": ""}],
        "terms_of_art": [{"source": "permit", "target": "model-permit-2"}],
        "named_entities": [{"source": "Board", "target": "model-board"}],
    }
    out = apply_pinned_terms(notes, [PinnedTerm("permit", "pinned-permit", "n", "Glossary A")])
    assert out["terms_of_art"] == []
    assert out["defined_terms"] == [
        {
            "source": "permit",
            "target": "pinned-permit",
            "note": "n",
            "origin": "pinned",
            "origin_label": "Glossary A",
        }
    ]
    assert out["named_entities"][0]["origin"] == "model"


@pytest.mark.asyncio
async def test_translate_document_reports_pinned_origin() -> None:
    seen: list[dict[str, Any]] = []
    await translate_document(
        _source_akn_xml(),
        target_language="French",
        llm=_StubLLM(),
        on_notes=seen.append,
        pinned_terms=[_pin("person", "personne"), _pin("absent-term", "x")],
    )
    terms = seen[0]["defined_terms"]
    assert [(t["target"], t["origin"]) for t in terms] == [("personne", "pinned")]


def test_combining_marks_are_part_of_a_word() -> None:
    assert _sources("क़ानून कि", "क") == []


@pytest.mark.asyncio
async def test_model_origin_is_recorded_without_a_glossary() -> None:
    seen: list[dict[str, Any]] = []
    await translate_document(
        _source_akn_xml(), target_language="French", llm=_StubLLM(), on_notes=seen.append
    )
    assert [t["origin"] for t in seen[0]["defined_terms"]] == ["model"]


@pytest.mark.asyncio
async def test_selection_ignores_markup_keywords() -> None:
    seen: list[dict[str, Any]] = []
    await translate_document(
        _source_akn_xml(),
        target_language="French",
        llm=_StubLLM(),
        on_notes=seen.append,
        pinned_terms=[_pin("ARTICLE", "x"), _pin("person", "personne")],
    )
    assert [t["source"] for t in seen[0]["defined_terms"] if t["origin"] == "pinned"] == ["person"]
