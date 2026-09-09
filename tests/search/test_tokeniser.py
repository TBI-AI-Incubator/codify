"""Tokeniser invariants.

The load-bearing one is symmetry: index time and query time must produce the
same tokens. Applied on one side only this makes recall worse, silently, so it
is asserted rather than assumed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

import pytest

from codify.search import TOKENISER_VERSION, tokenise, tokenise_to_text

# Snippets spanning every path the ladder takes. Any change reachable from
# `tokenise`, including its imports, moves this digest.
_FROZEN_CORPUS: Final = (
    ("The rights of the accused shall include appealing convictions", "eng"),
    ("Appeals against a conviction lie to the Court of Cassation", "eng"),
    ("المحكمة تنظر في الطعن المقدم من المتهم وفقا لاحكام القانون", "ara"),
    ("يعاقب بالحبس مدة لا تزيد على ثلاث سنوات وبالغرامة", "ara"),
    ("المادة ٣٥ من قانون العقوبات رقم ١٦ لسنة ١٩٦٠", "ara"),
    ("הזכויות של הנאשם כוללות זכות ערעור לבית המשפט", "heb"),
    ("prawa oskarżonego obejmują odwołanie do sądu", "pol"),
    ("të drejtat e të akuzuarit dhe e drejta e ankesës", "sqi"),
    ("права обвинуваченого на оскарження вироку", "ukr"),
    ("Les droits de l'accusé comprennent le droit d'appel", "fra"),
    # Indic scripts carry their vowels as combining marks, so they are the
    # scripts a change to mark handling moves. Without them the digest passed
    # through a change that had split कानून into three fragments.
    ("कानून का प्रावधान न्यायालय के समक्ष", "hin"),
    ("நீதிமன்றம் மேல்முறையீடு உரிமை", "tam"),
    # Turkish, because default casefolding is not locale-aware and İ folds to
    # `i` plus a combining dot; without a Turkish row the digest passed through
    # a state where İHALE and ihale never matched.
    ("İHALE KANUNU ve idari yargı kararları", "tur"),
    # A non-transcription marker tokenises to nothing, so the digest pins that
    # too: a filename must never become a searchable term.
    ("[TIFF not transcribed: annex-page.tif]", "eng"),
)

# sha256 over the joined output of _FROZEN_CORPUS at this TOKENISER_VERSION.
_FROZEN_DIGEST: Final = {
    2: "a590371f128b39a2d405fdda1253f91282f0549e8ba575e86325c381dd10eaea",
}


def test_output_is_frozen_for_the_declared_version() -> None:
    """A tokeniser change without a `TOKENISER_VERSION` bump is the silent
    failure this whole design turns on: existing rows keep the old version, the
    backfill resolver finds no work, and the corpus splits permanently across
    two tokenisations with nothing to detect it."""
    digest = hashlib.sha256(
        "\n".join(tokenise_to_text(t, lang) for t, lang in _FROZEN_CORPUS).encode()
    ).hexdigest()
    assert digest == _FROZEN_DIGEST[TOKENISER_VERSION], (
        "tokeniser output changed. Bump TOKENISER_VERSION, add its digest to "
        "_FROZEN_DIGEST, and run the backfill-search-tokens admin operation, or "
        "the corpus keeps tokens no query will match."
    )


def test_arabic_clitic_round_trip_holds_across_the_corpus() -> None:
    """Six hand-picked pairs cannot tell a correct affix rule from a broken one.
    This is a sample of pairs actually attested in the corpus: both the bare and
    the prefixed form occur, so a query for one should reach the other."""
    fixture = Path(__file__).parent / "fixtures" / "ara_clitic_pairs.tsv"
    pairs = [
        line.split("\t")
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(pairs) > 2000, "fixture truncated"
    agree = sum(tokenise(bare, "ara") == tokenise(prefixed, "ara") for bare, prefixed in pairs)
    rate = agree / len(pairs)
    # A floor, not the measured figure: an improvement should not fail the test.
    assert rate >= 0.96, f"clitic round-trip fell to {rate:.1%} over {len(pairs)} attested pairs"


@pytest.mark.parametrize(
    ("body", "language"),
    [
        ("The rights of the accused shall include appealing convictions", "eng"),
        ("المحكمة تنظر في الطعن المقدم من المتهم", "ara"),
        ("הזכויות של הנאשם כוללות ערעור", "heb"),
        ("prawa oskarżonego obejmują odwołanie", "pol"),
        ("të drejtat e të akuzuarit", "sqi"),
        ("права обвинуваченого", "ukr"),
    ],
)
def test_a_quoted_phrase_reaches_the_text_it_was_indexed_under(body: str, language: str) -> None:
    """The contract the lexical arm rests on: a user quoting part of a provision
    must produce a contiguous run of the tokens that provision was stored under.
    Asserting `tokenise(x) == tokenise(x)` would only restate that the function
    is pure, which no implementation can fail."""
    stored = tokenise_to_text(body, language)
    quoted = " ".join(body.split()[1:4])
    assert tokenise_to_text(quoted, language) in stored


# Real words per language: ASCII placeholders reach no stemmer on most of these
# paths, so parametrising them would run the same test six times.
@pytest.mark.parametrize(
    ("language", "words"),
    [
        ("eng", ["rights", "appeals", "rights", "convictions", "appeals"]),
        ("ara", ["الحقوق", "المحكمة", "الحقوق", "العقوبة", "المحكمة"]),
        ("heb", ["הזכויות", "הנאשם", "הזכויות", "ערעור", "הנאשם"]),
        ("pol", ["prawa", "odwołanie", "prawa", "oskarżony", "odwołanie"]),
        ("sqi", ["drejtat", "ankesa", "drejtat", "akuzuarit", "ankesa"]),
        ("ukr", ["права", "оскарження", "права", "обвинуваченого", "оскарження"]),
    ],
)
def test_one_token_out_per_token_in(language: str, words: list[str]) -> None:
    """Order preserved, nothing dropped, nothing deduped. Positional queries
    stay possible only while this holds."""
    tokens = tokenise(" ".join(words), language)
    assert len(tokens) == len(words)
    assert tokens[0] == tokens[2]
    assert tokens[1] == tokens[4]


def test_stopwords_are_kept() -> None:
    """BM25 weights common terms down via IDF; dropping them here would break
    the one-token-per-token invariant for no gain."""
    assert "the" in tokenise("the court", "eng")


# Arabic clitic round-trip: a query for the bare form must reach the prefixed
# form. Postgres's Snowball config fails these three; measured over the corpus,
# this tokeniser round-trips 97.4% of 14,107 attested pairs against its 0%.
@pytest.mark.parametrize(
    ("bare", "prefixed"),
    [
        ("قانون", "القانون"),  # law: Snowball splits these
        ("حق", "الحق"),  # right
        ("محكمة", "المحكمة"),  # court
        ("ضريبة", "الضريبة"),  # tax
        ("نقيب", "وللنقيب"),  # captain: conjunction stacked on article
        ("شركة", "بالشركة"),  # company
    ],
)
def test_arabic_clitic_round_trip(bare: str, prefixed: str) -> None:
    assert tokenise(bare, "ara") == tokenise(prefixed, "ara")


def test_arabic_stem_initial_waw_is_a_known_loss() -> None:
    """وزير ("minister") begins with a stem و that no affix rule can tell from
    the conjunction. Protecting it costs 2.4pp of corpus round-trip, so it is
    accepted. Pinned so the trade stays visible if the rules change."""
    assert tokenise("وزير", "ara") != tokenise("الوزير", "ara")


def test_arabic_orthographic_variants_fold() -> None:
    """Alef and ta-marbuta variants and harakat must not split a term."""
    assert tokenise("إجراء", "ara") == tokenise("اجراء", "ara")
    assert tokenise("المُحَكَّمَة", "ara") == tokenise("المحكمة", "ara")


def test_english_is_stemmed() -> None:
    assert tokenise("rights", "eng") == tokenise("right", "eng")
    assert tokenise("appealing", "eng") == tokenise("appeal", "eng")


def test_polish_is_stemmed_where_postgres_has_no_config() -> None:
    assert tokenise("prawa", "pol") == tokenise("prawo", "pol")


def test_hebrew_niqqud_does_not_split_a_term() -> None:
    assert tokenise("הַנֶּאֱשָׁם", "heb") == tokenise("הנאשם", "heb")


def test_non_latin_digits_fold_to_ascii() -> None:
    """Article numbers are queried in either digit set."""
    assert tokenise("المادة ٣٥", "ara") == tokenise("المادة 35", "ara")


def test_unknown_language_falls_back_to_segmentation() -> None:
    """An unmapped ISO 639-3 code still tokenises rather than raising."""
    assert tokenise("some text here", "zzz") == ["some", "text", "here"]


@pytest.mark.parametrize("text", ["", "   ", "...", "،؛"])
def test_empty_and_punctuation_only_yield_no_tokens(text: str) -> None:
    assert tokenise(text, "ara") == []
    assert tokenise(text, "eng") == []


@pytest.mark.parametrize(
    ("stray", "canonical"),
    [("en", "eng"), ("lt", "lit"), ("en-US", "eng"), ("EN", "eng")],
)
def test_language_labels_fold_to_iso639_3(stray: str, canonical: str) -> None:
    """`versions.language` carries 639-1 strays: 1,214 versions are labelled
    `en`. Unfolded they would tokenise differently from their `eng` siblings,
    so a query would reach one and not the other."""
    text = "the rights of the accused"
    assert tokenise(text, stray) == tokenise(text, canonical)


def test_an_unresolvable_language_still_tokenises() -> None:
    """A bad label costs stemming, never the whole ingest."""
    assert tokenise("some text here", "not-a-language") == ["some", "text", "here"]


@pytest.mark.parametrize(
    ("text", "language"),
    [("कानून का प्रावधान", "hin"), ("நீதிமன்றம் மேல்முறையீடு", "tam")],
)
def test_indic_words_survive_segmentation_whole(text: str, language: str) -> None:
    """Indic vowels are combining marks. Excluded from the word class they act
    as separators and split कानून into three fragments; stripped outright they
    change the word to कानन. Asserted on segmentation rather than on the final
    tokens, because a stemmer may legitimately shorten one afterwards."""
    from codify.search.tokeniser import _WORD, _normalise

    segmented = _WORD.findall(_normalise(text, language))
    assert segmented == text.split()
    assert len(tokenise(text, language)) == len(text.split())


def test_turkish_dotted_capital_i_matches_its_lowercase_form() -> None:
    """Default casefolding is not locale-aware: İ becomes `i` plus a combining
    dot, so an indexed İHALE never met a query for ihale."""
    assert tokenise("İHALE", "tur") == tokenise("ihale", "tur")
    assert tokenise("İSTANBUL", "tur") == tokenise("istanbul", "tur")
