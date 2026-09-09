"""The legibility score. Each test names the wrong reading it prevents."""

from __future__ import annotations

from codify.quality.legibility import MIN_TOKENS, band_for, function_word_rate
from codify.quality.lexicons import WordList, words_for

# The word list, not the script pack: legibility keys on language now.
ARABIC = words_for("ara")
assert ARABIC is not None

# Legal prose at ordinary density.
_READABLE = "تسري أحكام هذا القانون على كل من يعمل في القطاع العام أو في القطاع الخاص. "
# The same shapes with the letters garbled: token lengths survive, words do not.
_GARBLED = "نبري الظ ول المتع قن با ه ين ولا لمخ لفه بمد هذ لقر ر لمشمو ه بحكم. "


def _rate(unit: str, repeats: int) -> float | None:
    return function_word_rate(unit * repeats, ARABIC)


class TestCalibration:
    """The bands are thresholds on this word list, so both move together or
    neither means anything."""

    def test_the_word_list_the_bands_were_derived_from_has_not_changed(self) -> None:
        """Adding or dropping words shifts every score, silently rebanding the
        corpus. Changing this number means re-deriving the boundaries."""
        assert len(ARABIC.function_words) == 28

    def test_the_token_floor_is_the_calibrated_one(self) -> None:
        """Pinned literally: a test reading MIN_TOKENS tracks the constant
        instead of holding it."""
        assert MIN_TOKENS == 200

    def test_an_orthographic_variant_is_not_a_function_word(self) -> None:
        """Folding is right for anchor recovery and wrong here: it would let a
        garbled source match and score as legible."""
        assert function_word_rate("فى هذة " * 200, ARABIC) == 0.0


class TestSeparation:
    def test_readable_prose_scores_above_garbled_prose(self) -> None:
        """The whole point: a character-level proxy scores these the same."""
        readable = _rate(_READABLE, 40)
        garbled = _rate(_GARBLED, 40)
        assert readable is not None and garbled is not None
        assert readable > garbled

    def test_readable_prose_lands_in_a_legible_band(self) -> None:
        assert band_for(_rate(_READABLE, 40)) in {"good", "fair"}

    def test_garbled_prose_lands_below_the_working_band(self) -> None:
        assert band_for(_rate(_GARBLED, 40)) in {"poor", "illegible"}

    def test_prose_near_the_boundary_bands_the_way_the_corpus_did(self) -> None:
        """The fixtures above sit far from every boundary, so they would survive
        a scorer that had drifted. This one sits where documents actually land."""
        # One function word in eight, the density the `fair` band was drawn for.
        text = "في الجهة المختصة تقرير سنوي مفصل شامل واضح " * 60
        rate = function_word_rate(text, ARABIC)
        assert rate is not None
        assert 80 <= rate < 200


class TestAbstention:
    def test_too_little_text_scores_None_not_zero(self) -> None:
        """A one-line decree can miss every function word; 0.0 would read as
        unreadable rather than as unjudgeable."""
        assert function_word_rate("مادة (1)", ARABIC) is None

    def test_the_floor_is_counted_in_tokens_not_characters(self) -> None:
        """A long single-word run is not 200 tokens of evidence."""
        below = _rate("نص ", MIN_TOKENS - 1)
        at = _rate("نص ", MIN_TOKENS)
        assert below is None
        assert at is not None

    def test_a_language_with_no_word_list_scores_None(self) -> None:
        """Silence for an unsupported language, never a bad score."""
        bare = WordList(language="zz")
        assert function_word_rate("the quick brown fox " * 100, bare) is None

    def test_no_pack_scores_None(self) -> None:
        """A jurisdiction whose script cannot be resolved abstains."""
        assert function_word_rate(_READABLE * 40, None) is None

    def test_an_unscored_document_has_no_band(self) -> None:
        """None must not fall through to the worst band."""
        assert band_for(None) is None


class TestTokenising:
    def test_another_script_does_not_dilute_the_rate(self) -> None:
        """A bilingual page would otherwise score as degraded Arabic because its
        Latin words all count as non-function tokens."""
        arabic_only = _rate(_READABLE, 40)
        bilingual = function_word_rate(_READABLE * 40 + "English text " * 200, ARABIC)
        assert arabic_only == bilingual

    def test_digits_and_punctuation_separate_tokens(self) -> None:
        """`في(5)من` is two function words, not one unrecognised token."""
        assert function_word_rate("في(5)من " * 100, ARABIC) == 1000.0


class TestTokenisingArabic:
    def test_vocalised_prose_scores_like_its_plain_form(self) -> None:
        """Harakat are separate codepoints and not letters, so a naive tokeniser
        splits `فِي` into two tokens and matches neither."""
        plain = "تسري أحكام هذا القانون على كل من يعمل في القطاع العام. " * 40
        vocalised = "تَسري أحكام هذا القانون عَلى كل مِن يعمل فِي القطاع العام. " * 40
        assert function_word_rate(plain, ARABIC) == function_word_rate(vocalised, ARABIC)


class TestBands:
    def test_the_bands_run_from_legible_down(self) -> None:
        """Ordering, so a rewrite cannot silently invert the scale."""
        assert band_for(150.0) == "good"
        assert band_for(100.0) == "fair"
        assert band_for(60.0) == "poor"
        assert band_for(10.0) == "illegible"

    def test_a_boundary_belongs_to_the_better_band(self) -> None:
        """Inclusive lower bounds: a document scoring exactly 120 is good, not
        fair, and the band counts shift by hundreds if that flips."""
        assert band_for(120.0) == "good"
        assert band_for(80.0) == "fair"
        assert band_for(40.0) == "poor"

    def test_zero_is_illegible_not_unscored(self) -> None:
        """Distinct from None: this document was measured and found unreadable."""
        assert band_for(0.0) == "illegible"
