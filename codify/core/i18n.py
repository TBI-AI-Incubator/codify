"""Locale → LLM-facing language name."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

_LANGUAGE_NAMES: dict[str, str] = {
    "en-GB": "English",
    "uk-UA": "Ukrainian",
    "sq-AL": "Albanian",
    "sr-Latn-RS": "Serbian in Latin script (never Cyrillic)",
    "el-GR": "Greek",
    "it-IT": "Italian",
    "ar": "Modern Standard Arabic",
    "et-EE": "Estonian",
}

_PREFIX_MAP: dict[str, str] = {full.split("-", 1)[0].lower(): full for full in _LANGUAGE_NAMES}


def language_name_for(locale: str | None) -> str | None:
    if not locale:
        return None
    tag = locale.strip()
    resolved = tag if tag in _LANGUAGE_NAMES else _PREFIX_MAP.get(tag.split("-", 1)[0].lower())
    if resolved is None:
        logger.warning("response_language_unknown_locale", locale=locale[:32])
        return None
    name = _LANGUAGE_NAMES[resolved]
    return name if name != "English" else None


def with_response_language(system_prompt: str, locale: str | None) -> str:
    name = language_name_for(locale)
    if name is None:
        return system_prompt
    return f"{system_prompt}\n\nRespond in {name}. Keep technical AKN terms (eId, FRBR) verbatim."


__all__ = ["language_name_for", "with_response_language"]
