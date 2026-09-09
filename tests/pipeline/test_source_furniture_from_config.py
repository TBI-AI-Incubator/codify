"""Jurisdiction-specific strings come from config, not from the code. Each test
asserts both directions: it works when the config supplies the string, and the
behaviour is absent when it does not."""

from __future__ import annotations

from codify.pipeline.enrich.ocr import (
    _strip_furniture_lines,
    clean_page_text,
    furniture_inline_patterns,
    furniture_line_patterns,
)

_MASTHEAD = "Atlantis Gazette Office - archival copy"
_BODY = "Article 1. This Article states a rule."


class TestWholeLineFurniture:
    def test_a_declared_pattern_takes_the_line(self) -> None:
        patterns = furniture_line_patterns("xa")
        assert patterns, "the synthetic jurisdiction declares one"
        out = _strip_furniture_lines(f"{_MASTHEAD}\n{_BODY}", patterns)
        assert _MASTHEAD not in out
        assert _BODY in out

    def test_without_the_config_the_line_survives(self) -> None:
        out = _strip_furniture_lines(f"{_MASTHEAD}\n{_BODY}", ())
        assert _MASTHEAD in out


class TestInlineFurniture:
    def test_a_declared_pattern_is_scrubbed_from_a_shared_line(self) -> None:
        line = f"{_BODY} {_MASTHEAD}"
        out = clean_page_text(line, furniture_inline_patterns("xa"))
        assert "archival copy" not in out
        assert "Article 1." in out

    def test_without_the_config_the_text_is_untouched(self) -> None:
        line = f"{_BODY} {_MASTHEAD}"
        assert "archival copy" in clean_page_text(line)


class TestResolution:
    def test_a_jurisdiction_declaring_none_gets_none(self) -> None:
        assert furniture_line_patterns("xe") == ()

    def test_no_country_gets_none(self) -> None:
        assert furniture_line_patterns("") == ()


class TestJurisdictionLexicon:
    """A term naming one jurisdiction lives in its config, not the language list."""

    def test_a_declared_term_joins_the_language_list(self) -> None:
        from codify.jurisdictions import load_config
        from codify.quality.lexicons import extend, words_for

        config = load_config("xa")
        base = words_for(config.authoritative_language)
        assert base is not None
        out = extend(base, config.lexicon_words)
        assert out is not None
        assert "Atlantean" in out.lexicon_words
        assert "Atlantean" not in base.lexicon_words

    def test_without_the_config_the_language_list_is_unchanged(self) -> None:
        from codify.quality.lexicons import extend, words_for

        base = words_for("eng")
        assert extend(base, []) is base


class TestCalendarHint:
    """The calendar cue for one jurisdiction comes from its config, not from the
    prompt every jurisdiction shares."""

    def test_the_hint_carries_the_jurisdiction_and_its_eras(self) -> None:
        from codify.jurisdictions import load_config
        from codify.pipeline.enrich.metadata import calendar_hint

        hint = calendar_hint(load_config("xa"))
        assert "gregorian" in hint
        assert "hijri" in hint
        assert "Thalassocracy" in hint

    def test_no_config_gives_no_hint(self) -> None:
        from codify.pipeline.enrich.metadata import calendar_hint

        assert calendar_hint(None) == ""

    def test_the_shared_prompt_keeps_only_signals_that_hold_anywhere(self) -> None:
        """The calendar guidance survives; the clause tying a calendar to named
        jurisdictions does not, because that belongs to those jurisdictions."""
        from codify.pipeline.enrich.metadata import METADATA_SYSTEM_PROMPT

        assert "calendar" in METADATA_SYSTEM_PROMPT
        assert "instruments are typically Hijri" not in METADATA_SYSTEM_PROMPT


class TestThePageCarriesItsPatterns:
    """A helper that takes a page must not need the patterns handed to it again:
    that is how a scrub gets silently skipped on one path out of eight."""

    def test_a_page_built_by_the_extractor_scrubs_without_further_argument(
        self,
    ) -> None:
        from codify.pipeline.enrich.ocr import PageResult, _page_body_for_combine

        page = PageResult(
            page_number=1,
            text=f"{_BODY} {_MASTHEAD}",
            method="vision_ocr",
            furniture=furniture_inline_patterns("xa"),
        )
        body = _page_body_for_combine(page)
        assert body is not None
        assert "archival copy" not in body
        assert "Article 1." in body

    def test_a_page_with_no_patterns_keeps_the_text(self) -> None:
        from codify.pipeline.enrich.ocr import PageResult, _page_body_for_combine

        page = PageResult(page_number=1, text=f"{_BODY} {_MASTHEAD}", method="vision_ocr")
        body = _page_body_for_combine(page)
        assert body is not None and "archival copy" in body


class TestCalendarVocabulary:
    """The config and the prompt were written separately and mostly disagree, so
    a hint built from a config verbatim would name a calendar the schema lacks."""

    def test_config_values_are_translated_to_the_prompt_s_names(self) -> None:
        from codify.pipeline.enrich.metadata import _as_prompt_calendar

        assert _as_prompt_calendar("lunar_hijri") == "hijri"
        assert _as_prompt_calendar("solar_hijri") == "hijri_solar"
        assert _as_prompt_calendar("buddhist_era") == "buddhist"

    def test_an_ambiguous_config_value_names_no_calendar(self) -> None:
        """`dual` is what the model must judge, so hinting at it would prejudge."""
        from codify.pipeline.enrich.metadata import _as_prompt_calendar

        assert _as_prompt_calendar("dual") == ""

    def test_each_imperial_system_names_its_own(self) -> None:
        """One value for both named one system and was wrong for the other."""
        from codify.pipeline.enrich.metadata import _as_prompt_calendar

        assert _as_prompt_calendar("japanese_era") == "japanese_era"
        assert _as_prompt_calendar("minguo") == "minguo"
        assert _as_prompt_calendar("imperial_era") == ""

    def test_every_hint_names_only_values_the_prompt_offers(self) -> None:
        import json

        from codify.jurisdictions import JURISDICTIONS_DIR
        from codify.pipeline.enrich.metadata import (
            METADATA_SYSTEM_PROMPT,
            _as_prompt_calendar,
        )

        root = JURISDICTIONS_DIR
        seen = set()
        for config in root.glob("*/config.json"):
            data = json.loads(config.read_text(encoding="utf-8"))
            for value in [data.get("calendar", "")] + [
                e.get("calendar", "") for e in (data.get("legal_eras") or [])
            ]:
                named = _as_prompt_calendar(value or "")
                if named:
                    seen.add(named)
        assert seen, "the corpus declares calendars"
        for named in seen:
            assert f'"{named}"' in METADATA_SYSTEM_PROMPT


class TestEraCalendarIsClosed:
    def test_an_era_takes_the_same_closed_set_as_the_jurisdiction(self) -> None:
        """Two calendar fields with different vocabularies would drift apart."""
        import pydantic
        import pytest

        from codify.jurisdictions import LegalEra

        assert LegalEra(id="x", calendar="lunar_hijri").calendar == "lunar_hijri"
        assert LegalEra(id="x").calendar is None
        with pytest.raises(pydantic.ValidationError):
            LegalEra(id="x", calendar="hijri")
