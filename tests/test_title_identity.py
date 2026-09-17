"""Identity derived from a title, for instrument series that number nothing."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from codify.frbr import TITLE_DIGEST_PREFIX, identity_from_title, is_citable_work_uri
from codify.jurisdictions import SLUG_DIGEST_CHARS, TitleIdentity

# Every title here is fabricated. Thai vowel and tone marks are separate
# codepoints, which is the property the combining-mark case turns on.
RULE = TitleIdentity(
    strip_prefixes=["พระราชบัญญัติประกอบรัฐธรรมนูญ", "พระราชบัญญัติ"],
    year_particles=["พระพุทธศักราช", "พุทธศักราช", "พุทธสักราช", "พ.ศ."],
    edition_markers=["ฉบับที่", "ฉะบับที่"],
    consolidation_markers=["Update", "ครั้งที่"],
)

SEGMENT = re.compile(rf"{TITLE_DIGEST_PREFIX}[0-9a-f]{{{SLUG_DIGEST_CHARS}}}")


@pytest.mark.parametrize(
    ("title", "body", "edition", "year"),
    [
        # The principal instrument and its amendment take different identities.
        ("พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511", "เครื่องร่อนสุริยะ", "", "2511"),
        ("พระราชบัญญัติเครื่องร่อนสุริยะ (ฉบับที่ 3) พ.ศ. 2468", "เครื่องร่อนสุริยะ", "3", "2468"),
        # Native digits reach the same identity as their ASCII spelling.
        ("พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. ๒๕๑๑", "เครื่องร่อนสุริยะ", "", "2511"),
        # A longer kind word is stripped whole, not by its shorter prefix.
        ("พระราชบัญญัติประกอบรัฐธรรมนูญว่าด้วยสภาหอสมุดจันทรา พ.ศ. 2472", "ว่าด้วยสภาหอสมุดจันทรา", "", "2472"),
        # A re-publication marker is not part of the work's identity.
        (
            "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511 (ฉบับ Update ล่าสุด)",
            "เครื่องร่อนสุริยะ",
            "",
            "2511",
        ),
        # An edition number after a re-publication marker is the edition folded
        # in, so it neither renames the work nor forks it from its own identity.
        (
            "พระราชบัญญัติกองทุนเมฆฝน พ.ศ. 2481 (ฉบับ Update ล่าสุด) (ครั้งที่ 6) (ฉบับที่ 7)",
            "กองทุนเมฆฝน",
            "",
            "2481",
        ),
        # Two years: the last is this instrument's, the earlier one names the
        # instrument being amended and stays, telling two amendments apart.
        (
            "พระราชบัญญัติแก้ไขเพิ่มเติมพระราชกำหนดว่าด้วยหมอกเงิน พ.ศ. 2477 (ฉบับที่ 5) พ.ศ. 2486",
            "แก้ไขเพิ่มเติมพระราชกำหนดว่าด้วยหมอกเงิน-พระพุทธศักราช-2477",
            "5",
            "2486",
        ),
        # Every declared spelling of the year particle reaches the same identity,
        # the pre-reform orthography included.
        ("พระราชบัญญัติมโหรีหลวง พระพุทธศักราช 2466", "มโหรีหลวง", "", "2466"),
        ("พระราชบัญญัติมโหรีหลวง พุทธสักราช 2466", "มโหรีหลวง", "", "2466"),
        # A year counted in an era the grammar does not declare is no year of
        # its own: the work is still named, and nothing is read as Buddhist.
        ("พระราชบัญญัติรถรางไอน้ำ ร.ศ. 108", "รถรางไอน้ำ-ร-ศ-108", "", ""),
        # A title stating no year still names the work.
        ("พระราชบัญญัติวิทยาลัยดารา (ฉบับ Update ล่าสุด)", "วิทยาลัยดารา", "", ""),
    ],
)
def test_identity_from_title(title: str, body: str, edition: str, year: str) -> None:
    result = identity_from_title(title, RULE)
    assert (result.body, result.edition, result.year) == (body, edition, year)
    assert SEGMENT.fullmatch(result.segment), result.segment


def test_the_segment_is_a_digest_of_the_whole_identity() -> None:
    """Body, edition and year each decide it, and nothing else does: the same
    identity from another spelling is the same segment, a different one is not."""
    base = identity_from_title("พระราชบัญญัติมโหรีหลวง (ฉบับที่ 3) พ.ศ. 2511", RULE)
    assert identity_from_title("พระราชบัญญัติ มโหรีหลวง  (ฉะบับที่ 03)  พุทธศักราช ๒๕๑๑", RULE) == base
    for other in (
        "พระราชบัญญัติมโหรีหลวง (ฉบับที่ 4) พ.ศ. 2511",
        "พระราชบัญญัติมโหรีหลวง (ฉบับที่ 3) พ.ศ. 2512",
        "พระราชบัญญัติมโหรีหลวงใหม่ (ฉบับที่ 3) พ.ศ. 2511",
    ):
        assert identity_from_title(other, RULE).segment != base.segment, other


def test_the_segment_is_ascii_and_citable() -> None:
    """No script reaches the URI, and the prefix is not the content-address one."""
    found = identity_from_title("พระราชบัญญัติมโหรีหลวง (ฉบับที่ 3) พ.ศ. 2511", RULE)
    assert found.segment.isascii()
    assert is_citable_work_uri(f"/akn/xz/act/1968/{found.segment}")


def test_the_body_keeps_the_marks_a_word_is_written_with() -> None:
    """`\\w` drops combining marks, so an identity built on it is not the title."""
    result = identity_from_title("พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511", RULE)
    assert result.body == "เครื่องร่อนสุริยะ"
    assert "ื" in result.body  # SARA UEE, a mark `\w` discards


def test_identity_is_a_pure_function_of_the_title() -> None:
    title = "พระราชบัญญัติเครื่องร่อนสุริยะ (ฉบับที่ 3) พ.ศ. 2468"
    assert identity_from_title(title, RULE) == identity_from_title(title, RULE)
    # Typography a publisher varies must not fork the identity.
    assert identity_from_title(title, RULE) == identity_from_title(
        "พระราชบัญญัติ เครื่องร่อนสุริยะ  (ฉบับที่ 3)  พ.ศ.2468", RULE
    )


@pytest.mark.parametrize(
    "title",
    [
        # Both declared spellings of the edition word reach one identity, so a
        # reform of the orthography does not fork it.
        "พระราชบัญญัติมโหรีหลวง (ฉบับที่ 2) พุทธศักราช 2475",
        "พระราชบัญญัติมโหรีหลวง (ฉะบับที่ 2) พุทธศักราช 2475",
    ],
)
def test_an_edition_spelling_variant_reaches_the_canonical_identity(title: str) -> None:
    assert identity_from_title(title, RULE) == identity_from_title(
        "พระราชบัญญัติมโหรีหลวง (ฉบับที่ 2) พ.ศ. 2475", RULE
    )


def test_an_undeclared_grammar_derives_the_whole_title() -> None:
    assert identity_from_title("Act No. 7 of 1992", TitleIdentity()).body == "act-no-7-of-1992"


def test_a_title_naming_nothing_yields_no_segment() -> None:
    assert identity_from_title("", RULE).segment == ""
    assert identity_from_title("พระราชบัญญัติ", RULE).segment == ""


_ADVERSARIAL = """
import sys
sys.path.insert(0, {root!r})
from tests.test_title_identity import RULE
from codify.frbr import identity_from_title
identity_from_title("(" * 40000, RULE)
identity_from_title("พ.ศ. " * 40000, RULE)
identity_from_title("(ฉบับที่ " * 40000, RULE)
"""


def test_the_title_patterns_are_bounded_on_adversarial_input() -> None:
    """`re` holds the GIL and takes no timeout, so the bound is a child process."""
    script = _ADVERSARIAL.format(root=str(Path(__file__).resolve().parents[1]))
    try:
        subprocess.run([sys.executable, "-c", script], timeout=10, check=True)
    except subprocess.TimeoutExpired:
        pytest.fail("a title pattern did not return within 10s on 40k repeats")


@pytest.mark.parametrize("invisible", ["​", "‏", "‎", "­"])
def test_an_invisible_character_does_not_fork_the_identity(invisible: str) -> None:
    """A format character is endemic in scanned and model-produced text and
    cannot be seen in the title, so it must not change the stored URI."""
    plain = "พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511"
    marked = f"{invisible}พระราชบัญญัติเครื่อง{invisible}ร่อนสุริยะ พ.ศ. 2511"
    assert identity_from_title(marked, RULE) == identity_from_title(plain, RULE)


def test_two_long_titles_differing_late_keep_separate_identities() -> None:
    """A digest of the whole identity, not of a prefix: these differ only at
    the end of a long body."""
    made = {
        identity_from_title(f"พระราชบัญญัติ{'a' * 120}{tail} พ.ศ. 2511", RULE).segment
        for tail in ("889", "5838")
    }
    assert len(made) == 2


def test_a_longer_digit_run_is_not_the_titles_year() -> None:
    """A prefix of a longer run is not a year, as `sole_year_token` also holds."""
    assert identity_from_title("พระราชบัญญัติมโหรีหลวง พ.ศ. 25110", RULE).year == ""


def test_a_marker_word_in_a_substantive_title_is_not_a_republication() -> None:
    """The marker is a parenthetical, not a word: read anywhere it would strip
    the edition and collide the amendment with its principal."""
    principal = identity_from_title("พระราชบัญญัติUpdate Services พ.ศ. 2511", RULE)
    amendment = identity_from_title("พระราชบัญญัติUpdate Services (ฉบับที่ 2) พ.ศ. 2511", RULE)
    assert amendment.edition == "2"
    assert amendment.segment != principal.segment


@pytest.mark.parametrize("written", ["3", "03", "003"])
def test_an_edition_is_its_number_not_its_typography(written: str) -> None:
    """A padded ordinal and a bare one are one edition; two URIs would fork it."""
    result = identity_from_title(f"พระราชบัญญัติมโหรีหลวง (ฉบับที่ {written}) พ.ศ. 2475", RULE)
    assert result.edition == "3"
    assert result == identity_from_title("พระราชบัญญัติมโหรีหลวง (ฉบับที่ 3) พ.ศ. 2475", RULE)


def test_a_lone_zero_edition_survives_canonicalisation() -> None:
    assert identity_from_title("พระราชบัญญัติมโหรีหลวง (ฉบับที่ 0) พ.ศ. 2475", RULE).edition == "0"


def test_an_invisible_between_a_letter_and_its_mark_does_not_fork_the_identity() -> None:
    """Removed after composition it leaves a decomposed letter where the same
    word composed; removed before, both reach one identity."""
    rule = TitleIdentity(strip_prefixes=["Act "], year_particles=["of"])
    blocked = identity_from_title("Act Cafe​́ rules of 1991", rule)
    composed = identity_from_title("Act Café rules of 1991", rule)
    assert blocked == composed


def test_a_cited_edition_stays_in_the_base_and_the_last_one_is_this_acts() -> None:
    """A title citing the instrument it amends states two editions; taking the
    first and stripping both would give two amendments one identity."""
    rule = TitleIdentity(strip_prefixes=["Act "], year_particles=["of"], edition_markers=["No."])
    third = identity_from_title("Act amending Foo (No. 2) of 1980 (No. 3) of 1990", rule)
    fourth = identity_from_title("Act amending Foo (No. 2) of 1980 (No. 4) of 1990", rule)
    assert (third.edition, fourth.edition) == ("3", "4")
    assert third.segment != fourth.segment
    # The cited edition is what tells the two apart from a sibling amending
    # another instrument, so it stays in the base.
    assert third.body == "amending-foo-no-2-of-1980"


def test_a_latin_year_particle_needs_a_token_boundary() -> None:
    """Without one "of" matches inside "proof" and claims the digits after it."""
    rule = TitleIdentity(strip_prefixes=["Act "], year_particles=["of"])
    assert identity_from_title("Act proof 1991", rule)[1:] == ("proof-1991", "", "")
    # Control: the particle as a word still states the year.
    assert identity_from_title("Act widgets of 1991", rule)[1:] == ("widgets", "", "1991")


def test_a_non_latin_particle_keeps_matching_without_a_space() -> None:
    """A script written without inter-word spaces would lose its year to a
    boundary rule meant for Latin words."""
    rule = TitleIdentity(strip_prefixes=["พระราชบัญญัติ"], year_particles=["พ.ศ."])
    assert identity_from_title("พระราชบัญญัติเครื่องร่อนสุริยะพ.ศ.2511", rule).year == "2511"


def test_a_latin_kind_prefix_ends_on_a_word_boundary() -> None:
    """Without one "Act" strips itself out of "Action" and two different titles
    reduce to the same identity."""
    rule = TitleIdentity(strip_prefixes=["Act"], year_particles=["of"])
    assert identity_from_title("Action of 1991", rule).body == "action"
    assert identity_from_title("Act ion of 1991", rule).body == "ion"


def test_a_latin_marker_matches_a_whole_word_only() -> None:
    """A consolidation marker inside a longer word is not the marker: stripping
    the parenthetical on it collides two different titles."""
    rule = TitleIdentity(
        strip_prefixes=["Act"], year_particles=["of"], consolidation_markers=["Update"]
    )
    kept = identity_from_title("Act Widgets (Updated reporting) of 1991", rule)
    assert kept.body == "widgets-updated-reporting"
    assert kept.segment != identity_from_title("Act Widgets of 1991", rule).segment
    assert identity_from_title("Act Widgets (Update 2020) of 1991", rule).body == "widgets"


def test_a_latin_year_particle_matches_a_whole_word_only() -> None:
    """The particle's trailing edge is a boundary as well: "of" followed by
    digits without a space is not the word."""
    rule = TitleIdentity(strip_prefixes=["Act"], year_particles=["of"])
    assert identity_from_title("Act Widgets of 1991", rule).year == "1991"
    assert identity_from_title("Act Widgets thereof 1991", rule).year == ""


def test_a_retained_edition_is_spelt_with_the_canonical_marker() -> None:
    """An earlier edition stays in the base to tell two amendments apart, so its
    marker must read the same however the publisher spelt it."""
    rule = TitleIdentity(
        strip_prefixes=["พระราชบัญญัติ"],
        year_particles=["พ.ศ."],
        edition_markers=["ฉบับที่", "ฉะบับที่"],
    )
    canonical = identity_from_title(
        "พระราชบัญญัติมโหรีหลวง (ฉบับที่ 2) พ.ศ. 2475 (ฉบับที่ 5) พ.ศ. 2486", rule
    )
    variant = identity_from_title(
        "พระราชบัญญัติมโหรีหลวง (ฉะบับที่ 02) พ.ศ. 2475 (ฉบับที่ 5) พ.ศ. 2486", rule
    )
    assert variant == canonical
    assert canonical.body == "มโหรีหลวง-ฉบับที่-2-พ-ศ-2475"


def test_a_title_reducing_to_digits_cannot_collide_with_a_number() -> None:
    """A numberless title reducing to digits takes the digest form like any
    other, so it cannot meet the instrument numbered the same."""
    rule = TitleIdentity(strip_prefixes=["Act"], year_particles=["of"])
    found = identity_from_title("Act 17 of 1991", rule)
    assert found.body == "17"
    assert SEGMENT.fullmatch(found.segment)
    assert found.segment != "17"


def test_an_edition_after_a_republication_marker_leaves_the_base_too() -> None:
    """It is not this document's edition, and it is not part of the base title
    either: the republication folded it in, whatever its position."""
    rule = TitleIdentity(
        strip_prefixes=["Act"],
        year_particles=["of"],
        consolidation_markers=["Update"],
        edition_markers=["No."],
    )
    assert identity_from_title("Act Foo (Update) (No. 7) of 1991", rule)[1:] == ("foo", "", "1991")
    assert identity_from_title("Act Foo of 1991 (Update) (No. 7)", rule)[1:] == ("foo", "", "1991")
    assert identity_from_title("Act Foo (No. 7) of 1991", rule)[1:] == ("foo", "7", "1991")


@pytest.mark.parametrize("particle", ["พระพุทธศักราช", "พุทธศักราช", "พ.ศ."])
def test_a_retained_year_is_spelt_with_the_canonical_particle(particle: str) -> None:
    """A cited year stays in the base to tell two amendments apart, so its
    particle must read the same whichever configured alias the title used."""
    title = f"พระราชบัญญัติแก้ไขเพิ่มเติมมโหรีหลวง {particle} 2477 (ฉบับที่ 5) พ.ศ. 2486"
    assert identity_from_title(title, RULE).body == "แก้ไขเพิ่มเติมมโหรีหลวง-พระพุทธศักราช-2477"


@pytest.mark.parametrize(
    "title",
    ["ACT WIDGETS (NO. 3) OF 1991", "act widgets (no. 3) of 1991", "Act Widgets (No. 3) of 1991"],
)
def test_a_latin_title_grammar_reads_any_casing(title: str) -> None:
    """The kind prefix, the edition marker and the year particle are words: a
    title set in capitals is the same title, and reaches the same identity."""
    rule = TitleIdentity(strip_prefixes=["Act"], year_particles=["of"], edition_markers=["No."])
    assert identity_from_title(title, rule) == identity_from_title(
        "Act Widgets (No. 3) of 1991", rule
    )
    assert identity_from_title(title, rule)[1:] == ("widgets", "3", "1991")


def test_text_after_the_year_stays_in_the_identity() -> None:
    """Only the year expression itself leaves the body: a substantive suffix
    after it tells two instruments apart, and only declared markers are dropped."""
    rule = TitleIdentity(
        strip_prefixes=["Act"], year_particles=["of"], consolidation_markers=["Update"]
    )
    north = identity_from_title("Act Widgets of 1991 (Northern Region)", rule)
    south = identity_from_title("Act Widgets of 1991 (Southern Region)", rule)
    assert north.body == "widgets-northern-region"
    assert north.segment != south.segment
    assert (north.year, south.year) == ("1991", "1991")
    # A declared republication marker after the year still leaves.
    assert identity_from_title("Act Widgets of 1991 (Update 2020)", rule).body == "widgets"


def test_case_is_folded_not_lowered() -> None:
    """Lowering leaves a sharp s where its capitals fold to two letters, so one
    title set two ways minted two identities; folding reaches one."""
    rule = TitleIdentity(strip_prefixes=["Act"], year_particles=["of"])
    assert identity_from_title("Act Straße of 1991", rule) == identity_from_title(
        "Act STRASSE of 1991", rule
    )
    assert identity_from_title("Act Straße of 1991", rule).body == "strasse"
