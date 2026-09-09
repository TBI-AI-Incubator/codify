"""language_name_for / with_response_language, prefix-match + no-op on en/unknown."""

from __future__ import annotations

import pytest

from codify.core.i18n import language_name_for, with_response_language


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("uk-UA", "Ukrainian"),
        ("uk", "Ukrainian"),
        ("UK-UA", "Ukrainian"),
        ("ar", "Modern Standard Arabic"),
        ("ar-EG", "Modern Standard Arabic"),
        ("sr-Latn-RS", "Serbian in Latin script (never Cyrillic)"),
        ("en-GB", None),
        ("en", None),
        ("xx-YY", None),
        ("", None),
        (None, None),
    ],
)
def test_language_name_for(locale: str | None, expected: str | None) -> None:
    assert language_name_for(locale) == expected


def test_with_response_language_appends_for_non_english() -> None:
    out = with_response_language("Base prompt.", "uk-UA")
    assert out.startswith("Base prompt.")
    assert "Respond in Ukrainian" in out


@pytest.mark.parametrize("locale", ["en-GB", "en", None, "", "xx-YY"])
def test_with_response_language_noop(locale: str | None) -> None:
    assert with_response_language("Base prompt.", locale) == "Base prompt."
