"""Fixture-driven tests for the deterministic notes-phase checks."""

from __future__ import annotations

from codify.translate.notes_audit import run_checks


def _empty_notes(target_language: str) -> dict[str, object]:
    return {
        "target_language": target_language,
        "defined_terms": [],
        "terms_of_art": [],
        "named_entities": [],
        "deontic_conventions": "",
        "structural_conventions": "",
        "ambiguities": [],
    }


class TestDefinitionsClauseHeuristic:
    def test_ar_definitions_header_with_empty_terms_downgrades(self) -> None:
        bluebell = "ARTICLE 1\nمعنى المصطلحات في تطبيق أحكام هذا القانون:"
        findings, sev = run_checks(
            bluebell, _empty_notes("English"), target_language="English", source_language="ara"
        )
        assert sev == "downgrade"
        assert any(f["code"] == "notes_definitions_empty" for f in findings)

    def test_ar_definitions_header_with_terms_passes(self) -> None:
        bluebell = "ARTICLE 1\nمعنى المصطلحات في تطبيق أحكام هذا القانون:"
        notes = _empty_notes("English")
        notes["defined_terms"] = [{"source": "الوزارة", "target": "the Ministry", "note": ""}]
        findings, sev = run_checks(
            bluebell, notes, target_language="English", source_language="ara"
        )
        assert not any(f["code"] == "notes_definitions_empty" for f in findings)
        assert sev is None

    def test_en_definitions_heading_downgrades_on_empty(self) -> None:
        bluebell = "PART I\n\nDefinitions\nIn this Act..."
        findings, sev = run_checks(
            bluebell, _empty_notes("French"), target_language="French", source_language="eng"
        )
        assert sev == "downgrade"
        assert any(f["code"] == "notes_definitions_empty" for f in findings)

    def test_en_substantive_use_of_definitions_word_does_not_fire(self) -> None:
        # "definitions" appearing inside a sentence, not as a heading.
        bluebell = "Any dispute concerning the definitions used elsewhere shall be resolved."
        findings, sev = run_checks(
            bluebell, _empty_notes("French"), target_language="French", source_language="eng"
        )
        assert not any(f["code"] == "notes_definitions_empty" for f in findings)
        assert sev is None

    def test_he_definitions_heading_downgrades_on_empty(self) -> None:
        bluebell = "פרק א\n\nהגדרות"
        findings, sev = run_checks(
            bluebell, _empty_notes("English"), target_language="English", source_language="heb"
        )
        assert sev == "downgrade"
        assert any(f["code"] == "notes_definitions_empty" for f in findings)


class TestTermsOfArtFloor:
    def test_long_source_empty_terms_of_art_warns(self) -> None:
        bluebell = "A" * 6_000
        findings, sev = run_checks(
            bluebell, _empty_notes("English"), target_language="English", source_language="eng"
        )
        # No definitions header here so this is the only trigger; warn, not downgrade.
        assert sev == "warn"
        assert any(f["code"] == "notes_terms_of_art_empty" for f in findings)

    def test_short_source_empty_terms_of_art_does_not_fire(self) -> None:
        bluebell = "A" * 500
        findings, sev = run_checks(
            bluebell, _empty_notes("English"), target_language="English", source_language="eng"
        )
        assert not any(f["code"] == "notes_terms_of_art_empty" for f in findings)
        assert sev is None

    def test_long_source_with_terms_of_art_passes(self) -> None:
        bluebell = "A" * 6_000
        notes = _empty_notes("English")
        notes["terms_of_art"] = [
            {"source": "x", "target": "y", "rationale": "", "false_friend_warning": ""}
        ]
        findings, sev = run_checks(
            bluebell, notes, target_language="English", source_language="eng"
        )
        assert not any(f["code"] == "notes_terms_of_art_empty" for f in findings)


class TestTargetLanguageMismatch:
    def test_notes_target_disagrees_downgrades(self) -> None:
        notes = _empty_notes("Arabic")
        notes["defined_terms"] = [{"source": "x", "target": "y", "note": ""}]
        findings, sev = run_checks(
            "short body", notes, target_language="English", source_language="eng"
        )
        assert sev == "downgrade"
        assert any(f["code"] == "notes_language_mismatch" for f in findings)

    def test_case_and_whitespace_are_folded(self) -> None:
        notes = _empty_notes("  english  ")
        notes["defined_terms"] = [{"source": "x", "target": "y", "note": ""}]
        findings, sev = run_checks(
            "short body", notes, target_language="English", source_language="eng"
        )
        assert not any(f["code"] == "notes_language_mismatch" for f in findings)

    def test_empty_notes_target_language_does_not_double_flag(self) -> None:
        # A degrade path leaves target_language empty; the caller already
        # holds the notes_status flag, so this check must not fire.
        notes = _empty_notes("")
        findings, sev = run_checks(
            "short body", notes, target_language="English", source_language="eng"
        )
        assert not any(f["code"] == "notes_language_mismatch" for f in findings)
