import pytest

from codify.lang import to_iso639_3


def test_two_letter_codes_normalise_to_iso639_3():
    assert to_iso639_3("en") == "eng"
    assert to_iso639_3("pl") == "pol"
    assert to_iso639_3("uk") == "ukr"


def test_already_three_letter_passes_through():
    assert to_iso639_3("ukr") == "ukr"
    assert to_iso639_3("eng") == "eng"
    # Valid 639-3 outside the 639-1 map still passes.
    assert to_iso639_3("mlt") == "mlt"


def test_case_and_whitespace_insensitive():
    assert to_iso639_3("  EN ") == "eng"
    assert to_iso639_3("ENG") == "eng"


def test_english_language_names_fold():
    assert to_iso639_3("English") == "eng"
    assert to_iso639_3("  ARABIC ") == "ara"
    assert to_iso639_3("hebrew") == "heb"


def test_empty():
    assert to_iso639_3("") == ""
    assert to_iso639_3(None) == ""


def test_eu_official_languages_fold():
    assert to_iso639_3("et") == "est"
    assert to_iso639_3("mt") == "mlt"
    assert to_iso639_3("cs") == "ces"
    assert to_iso639_3("Estonian") == "est"


def test_locale_tags_fold_on_primary_subtag():
    assert to_iso639_3("en-US") == "eng"
    assert to_iso639_3("uk_UA") == "ukr"
    assert to_iso639_3("ar-PS") == "ara"


def test_off_table_three_letter_passes_with_warning():
    # Legitimate rare 639-3 (Latin) is not rejected.
    assert to_iso639_3("lat") == "lat"


def test_unknown_raises():
    with pytest.raises(ValueError):
        to_iso639_3("klingon")
    with pytest.raises(ValueError):
        to_iso639_3("e n")
