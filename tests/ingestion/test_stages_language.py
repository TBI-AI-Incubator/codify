"""resolve_language must not collapse a multilingual jurisdiction to its
alphabetically-first config language (the "bul" leak)."""

from codify.jurisdictions import load_config, try_load_config
from codify.pipeline.stages import resolve_language


def test_multilingual_no_authoritative_defaults_english_not_first_listed() -> None:
    try_load_config.cache_clear()
    eu = load_config("eu")  # 24 languages, authoritative_language None, languages[0] == "bul"
    assert eu is not None
    # No detected language: must NOT return the alphabetical-first "bul".
    assert resolve_language({}, eu) == "eng"


def test_detected_language_wins() -> None:
    eu = load_config("eu")
    assert resolve_language({"language": "fra"}, eu) == "fra"


def test_authoritative_language_used_when_undetected() -> None:
    try_load_config.cache_clear()
    al = load_config("al")  # authoritative_language sqi
    assert al is not None
    assert resolve_language({}, al) == "sqi"


def test_junk_detected_language_falls_through_to_config() -> None:
    # An unrecognised LLM detection must not crash ingest; the config
    # authoritative language wins.
    al = load_config("al")
    assert resolve_language({"language": "not-a-language"}, al) == "sqi"


def test_detected_language_name_folds() -> None:
    eu = load_config("eu")
    assert resolve_language({"language": "French"}, eu) == "fra"


def test_sentinel_authoritative_language_falls_through() -> None:
    # gb-wls sets authoritative_language="both_equal"; kg sets "both".
    # Sentinels must not crash resolution.
    wls = load_config("gb-wls")
    assert resolve_language({}, wls) in {"eng", "cym"}
