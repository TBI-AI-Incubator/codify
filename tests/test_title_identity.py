"""Identity derived from a title, for instrument series that number nothing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from codify.frbr import DRAFT_PREFIX, identity_from_title, is_citable_work_uri
from codify.jurisdictions import SLUG_DIGEST_CHARS, SLUG_FLOOR, TitleIdentity

# Every title here is fabricated. Thai vowel and tone marks are separate
# codepoints, which is the property the combining-mark case turns on.
RULE = TitleIdentity(
    strip_prefixes=["พระราชบัญญัติประกอบรัฐธรรมนูญ", "พระราชบัญญัติ"],
    year_particles=["พระพุทธศักราช", "พุทธศักราช", "พุทธสักราช", "พ.ศ."],
    edition_markers=["ฉบับที่", "ฉะบับที่"],
    consolidation_markers=["Update", "ครั้งที่"],
    max_length=100,
)


@pytest.mark.parametrize(
    ("title", "slug", "edition", "year"),
    [
        # The principal instrument and its amendment take different slugs.
        ("พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511", "เครื่องร่อนสุริยะ", "", "2511"),
        (
            "พระราชบัญญัติเครื่องร่อนสุริยะ (ฉบับที่ 3) พ.ศ. 2468",
            "เครื่องร่อนสุริยะ-ฉบับที่-3",
            "3",
            "2468",
        ),
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
        # in, so it neither renames the work nor forks it from its own slug.
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
            "แก้ไขเพิ่มเติมพระราชกำหนดว่าด้วยหมอกเงิน-พ-ศ-2477-ฉบับที่-5",
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
def test_identity_from_title(title: str, slug: str, edition: str, year: str) -> None:
    result = identity_from_title(title, RULE)
    assert (result.slug, result.edition, result.year) == (slug, edition, year)


def test_slug_keeps_the_marks_a_word_is_written_with() -> None:
    """`\\w` drops combining marks, so a slug built on it is not the title."""
    result = identity_from_title("พระราชบัญญัติเครื่องร่อนสุริยะ พ.ศ. 2511", RULE)
    assert result.slug == "เครื่องร่อนสุริยะ"
    assert "ื" in result.slug  # SARA UEE, a mark `\w` discards


def test_identity_is_a_pure_function_of_the_title() -> None:
    title = "พระราชบัญญัติเครื่องร่อนสุริยะ (ฉบับที่ 3) พ.ศ. 2468"
    assert identity_from_title(title, RULE) == identity_from_title(title, RULE)
    # Typography a publisher varies must not fork the identity.
    assert (
        identity_from_title(title, RULE).slug
        == identity_from_title("พระราชบัญญัติ เครื่องร่อนสุริยะ  (ฉบับที่ 3)  พ.ศ.2468", RULE).slug
    )


def test_slug_is_capped_at_the_declared_length() -> None:
    rule = RULE.model_copy(update={"max_length": 12})
    result = identity_from_title("พระราชบัญญัติ" + "ก" * 40 + " พ.ศ. 2511", rule)
    assert len(result.slug) == 12


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        # Both declared spellings of the edition word reach one canonical slug,
        # so a reform of the orthography does not fork an identity.
        ("พระราชบัญญัติมโหรีหลวง (ฉบับที่ 2) พุทธศักราช 2475", "มโหรีหลวง-ฉบับที่-2"),
        ("พระราชบัญญัติมโหรีหลวง (ฉะบับที่ 2) พุทธศักราช 2475", "มโหรีหลวง-ฉบับที่-2"),
    ],
)
def test_an_edition_spelling_variant_reaches_the_canonical_slug(title: str, slug: str) -> None:
    assert identity_from_title(title, RULE).slug == slug


def test_an_undeclared_grammar_derives_nothing() -> None:
    assert identity_from_title("Act No. 7 of 1992", TitleIdentity()).slug == "act-no-7-of-1992"


_ADVERSARIAL = """
import sys
sys.path.insert(0, {root!r})
from tests.test_title_identity import RULE
from codify.frbr import DRAFT_PREFIX, identity_from_title, is_citable_work_uri
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


def test_two_titles_sharing_a_long_prefix_keep_separate_identities() -> None:
    """Truncation would merge two works onto one URI."""
    rule = RULE.model_copy(update={"max_length": 24})
    a = identity_from_title("พระราชบัญญัติ" + "ก" * 40 + "ข พ.ศ. 2511", rule)
    b = identity_from_title("พระราชบัญญัติ" + "ก" * 40 + "ค พ.ศ. 2511", rule)
    assert a.slug != b.slug
    assert len(a.slug) <= 24 and len(b.slug) <= 24


def test_the_cap_covers_the_edition_suffix_too() -> None:
    rule = RULE.model_copy(update={"max_length": SLUG_FLOOR})
    result = identity_from_title("พระราชบัญญัติ" + "ก" * 40 + " (ฉบับที่ 3) พ.ศ. 2511", rule)
    assert result.edition == "3"
    assert len(result.slug) <= SLUG_FLOOR


def test_two_long_titles_differing_late_do_not_share_a_digest() -> None:
    """Six hex characters collide on ordinary titles: these two do."""
    rule = RULE.model_copy(update={"max_length": SLUG_FLOOR})
    made = [
        identity_from_title(f"พระราชบัญญัติ{'a' * 120}{tail} พ.ศ. 2511", rule).slug
        for tail in ("889", "5838")
    ]
    assert made[0] != made[1]


def test_a_suffix_that_consumes_the_whole_cap_keeps_both_parts() -> None:
    """The edition separates an amendment from what it amends and the digest two
    long titles, so a limit too tight for both is exceeded, not either dropped."""
    rule = RULE.model_copy(update={"max_length": SLUG_FLOOR, "edition_markers": ["ฉ" * SLUG_FLOOR]})
    result = identity_from_title(
        "พระราชบัญญัติ" + "ก" * 60 + f" ({'ฉ' * SLUG_FLOOR} 3) พ.ศ. 2511", rule
    )
    assert result.edition == "3"
    assert result.slug.endswith(f"-{'ฉ' * SLUG_FLOOR}-3")
    assert result.slug[:SLUG_DIGEST_CHARS].isalnum()
    assert len(result.slug) == SLUG_DIGEST_CHARS + len(f"-{'ฉ' * SLUG_FLOOR}-3")


def test_a_longer_digit_run_is_not_the_titles_year() -> None:
    """A prefix of a longer run is not a year, as `sole_year_token` also holds."""
    assert identity_from_title("พระราชบัญญัติมโหรีหลวง พ.ศ. 25110", RULE).year == ""


@pytest.mark.parametrize("limit", [SLUG_FLOOR, SLUG_FLOOR + 4, 40, 100])
def test_the_declared_limit_covers_the_edition_suffix(limit: int) -> None:
    """The whole segment fits the limit, and it still carries the edition, which
    is what separates an amendment from the act it amends."""
    rule = RULE.model_copy(update={"max_length": limit})
    result = identity_from_title("พระราชบัญญัติ" + "ก" * 40 + " (ฉบับที่ 3) พ.ศ. 2511", rule)
    assert result.edition == "3"
    assert result.slug.endswith("-ฉบับที่-3")
    assert len(result.slug) <= limit


@pytest.mark.parametrize("limit", [0, 1, SLUG_FLOOR - 1])
def test_a_limit_too_short_to_hold_an_identity_is_refused(limit: int) -> None:
    """Below the floor the segment cannot carry both a digest and an edition,
    so the cap would be exceeded silently instead."""
    with pytest.raises(ValidationError):
        RULE.model_copy(update={"max_length": limit}).model_validate(
            RULE.model_dump() | {"max_length": limit}
        )


def test_a_marker_word_in_a_substantive_title_is_not_a_republication() -> None:
    """The marker is a parenthetical, not a word: read anywhere it would strip
    the edition and collide the amendment with its principal."""
    principal = identity_from_title("พระราชบัญญัติUpdate Services พ.ศ. 2511", RULE)
    amendment = identity_from_title("พระราชบัญญัติUpdate Services (ฉบับที่ 2) พ.ศ. 2511", RULE)
    assert amendment.edition == "2"
    assert amendment.slug != principal.slug


@pytest.mark.parametrize("written", ["3", "03", "003"])
def test_an_edition_is_its_number_not_its_typography(written: str) -> None:
    """A padded ordinal and a bare one are one edition; two URIs would fork it."""
    result = identity_from_title(f"พระราชบัญญัติมโหรีหลวง (ฉบับที่ {written}) พ.ศ. 2475", RULE)
    assert result.edition == "3"
    assert result.slug == "มโหรีหลวง-ฉบับที่-3"


def test_a_lone_zero_edition_survives_canonicalisation() -> None:
    assert identity_from_title("พระราชบัญญัติมโหรีหลวง (ฉบับที่ 0) พ.ศ. 2475", RULE).edition == "0"


def test_a_title_reducing_to_the_draft_namespace_is_moved_out_of_it() -> None:
    """`draft-` names a content address; an identity that reads as one would be
    refused as uncitable."""
    rule = TitleIdentity(strip_prefixes=["Act "], year_particles=["of"], edition_markers=["No."])
    slug = identity_from_title("Act draft rules of 1991", rule).slug
    assert not slug.startswith(DRAFT_PREFIX)
    assert is_citable_work_uri(f"/akn/xa/act/1991/{slug}")


def test_an_invisible_between_a_letter_and_its_mark_does_not_fork_the_slug() -> None:
    """Removed after composition it leaves a decomposed letter where the same
    word composed; removed before, both reach one slug."""
    rule = TitleIdentity(strip_prefixes=["Act "], year_particles=["of"])
    blocked = identity_from_title("Act Cafe​́ rules of 1991", rule)
    composed = identity_from_title("Act Café rules of 1991", rule)
    assert blocked.slug == composed.slug


_ESCAPE_RULE = TitleIdentity(
    strip_prefixes=["Act "], year_particles=["of"], edition_markers=["No."]
)


def test_an_edition_suffix_cannot_complete_the_reserved_namespace() -> None:
    """The base alone does not open it; the base and the suffix together do, and
    the URI carries them together."""
    slug = identity_from_title("Act draft (No. 2) of 1991", _ESCAPE_RULE).slug
    assert is_citable_work_uri(f"/akn/xa/act/1991/{slug}")
    assert not slug.startswith(DRAFT_PREFIX)


def test_the_escape_is_reserved_too_so_it_cannot_merge_two_titles() -> None:
    """Escaping only the reserved namespace maps a title already wearing the
    escape onto the escaped form of another."""
    plain = identity_from_title("Act draft rules of 1991", _ESCAPE_RULE).slug
    wearing = identity_from_title("Act t draft rules of 1991", _ESCAPE_RULE).slug
    assert plain != wearing
    assert is_citable_work_uri(f"/akn/xa/act/1991/{wearing}")


def test_an_ordinary_title_is_not_escaped() -> None:
    assert identity_from_title("Act normal rules of 1991", _ESCAPE_RULE).slug == "normal-rules"


def test_a_cited_edition_stays_in_the_base_and_the_last_one_is_this_acts() -> None:
    """A title citing the instrument it amends states two editions; taking the
    first and stripping both would give two amendments one identity."""
    rule = TitleIdentity(strip_prefixes=["Act "], year_particles=["of"], edition_markers=["No."])
    third = identity_from_title("Act amending Foo (No. 2) of 1980 (No. 3) of 1990", rule)
    fourth = identity_from_title("Act amending Foo (No. 2) of 1980 (No. 4) of 1990", rule)
    assert (third.edition, fourth.edition) == ("3", "4")
    assert third.slug != fourth.slug
    # The cited edition is what tells the two apart from a sibling amending
    # another instrument, so it stays in the base.
    assert "2" in third.slug


def test_a_latin_year_particle_needs_a_token_boundary() -> None:
    """Without one "of" matches inside "proof" and claims the digits after it."""
    rule = TitleIdentity(strip_prefixes=["Act "], year_particles=["of"])
    assert identity_from_title("Act proof 1991", rule) == ("proof-1991", "", "")
    # Control: the particle as a word still states the year.
    assert identity_from_title("Act widgets of 1991", rule) == ("widgets", "", "1991")


def test_a_non_latin_particle_keeps_matching_without_a_space() -> None:
    """A script written without inter-word spaces would lose its year to a
    boundary rule meant for Latin words."""
    rule = TitleIdentity(strip_prefixes=["พระราชบัญญัติ"], year_particles=["พ.ศ."])
    assert identity_from_title("พระราชบัญญัติเครื่องร่อนสุริยะพ.ศ.2511", rule).year == "2511"


def test_a_latin_kind_prefix_ends_on_a_word_boundary() -> None:
    """Without one "Act" strips itself out of "Action" and two different titles
    reduce to the same slug."""
    rule = TitleIdentity(strip_prefixes=["Act"], year_particles=["of"])
    assert identity_from_title("Action of 1991", rule).slug == "action"
    assert identity_from_title("Act ion of 1991", rule).slug == "ion"


def test_a_latin_marker_matches_a_whole_word_only() -> None:
    """A consolidation marker inside a longer word is not the marker: stripping
    the parenthetical on it collides two different titles."""
    rule = TitleIdentity(
        strip_prefixes=["Act"], year_particles=["of"], consolidation_markers=["Update"]
    )
    kept = identity_from_title("Act Widgets (Updated reporting) of 1991", rule).slug
    assert kept == "widgets-updated-reporting"
    assert kept != identity_from_title("Act Widgets of 1991", rule).slug
    assert identity_from_title("Act Widgets (Update 2020) of 1991", rule).slug == "widgets"


def test_a_latin_year_particle_matches_a_whole_word_only() -> None:
    """The particle's trailing edge is a boundary as well: "of" followed by
    digits without a space is not the word."""
    rule = TitleIdentity(strip_prefixes=["Act"], year_particles=["of"])
    assert identity_from_title("Act Widgets of 1991", rule).year == "1991"
    assert identity_from_title("Act Widgets thereof 1991", rule).year == ""
