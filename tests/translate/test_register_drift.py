"""Fixture-driven tests for the register-drift deontic-verb check."""

from __future__ import annotations

from codify.translate.audit import audit_translation


def _audit(translated: str, target_language: str = "eng") -> dict:
    return audit_translation("source", translated, {}, target_language=target_language)


class TestRegisterDrift:
    def test_dominant_shall_no_drift(self) -> None:
        text = " ".join(["Every person shall pay a fine."] * 30)
        result = _audit(text)
        assert result["register_drifted"] is False
        assert result["deontic_counts"].get("shall", 0) >= 30

    def test_split_shall_and_must_above_threshold_drifts(self) -> None:
        # 87 shall, 12 must, 12/99 ≈ 12% above the 10% threshold.
        shall_text = " ".join(["Every person shall pay."] * 87)
        must_text = " ".join(["The Minister must publish it."] * 12)
        result = _audit(f"{shall_text} {must_text}")
        assert result["register_drifted"] is True
        assert result["deontic_counts"]["shall"] == 87
        assert result["deontic_counts"]["must"] == 12

    def test_tiny_minor_below_threshold_no_drift(self) -> None:
        # 99 shall, 1 must, 1/100 = 1%, below 10%.
        shall_text = " ".join(["Every person shall pay."] * 99)
        must_text = "The Minister must publish it."
        result = _audit(f"{shall_text} {must_text}")
        assert result["register_drifted"] is False

    def test_small_sample_does_not_drift_even_when_mixed(self) -> None:
        # Total < 20 hits: sample too small to be meaningful.
        result = _audit("Alice shall pay. Bob must publish.")
        assert result["register_drifted"] is False

    def test_unknown_target_language_yields_empty_counts(self) -> None:
        result = _audit("Alice paye. Bob doit publier.", target_language="fra")
        assert result["deontic_counts"] == {}
        assert result["register_drifted"] is False

    def test_arabic_target_deontic_split(self) -> None:
        # 25 يجب, 6 يتعين, 6/31 ≈ 19% above threshold.
        must_text = " ".join(["يجب على كل مواطن أن يدفع الضريبة."] * 25)
        shall_text = " ".join(["يتعين على الوزير نشر القرار."] * 6)
        result = _audit(f"{must_text} {shall_text}", target_language="ara")
        assert result["register_drifted"] is True
        assert result["deontic_counts"]["يجب"] == 25
        assert result["deontic_counts"]["يتعين"] == 6
