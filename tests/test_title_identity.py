"""Identity derived from a title, for instrument series that number nothing."""

from __future__ import annotations

import pytest

from codify.frbr import identity_from_title
from codify.jurisdictions import TitleIdentity

# An abugida whose vowel and tone marks are separate codepoints, so a slug built
# on `\w` loses them. Used as script data; no corpus is implied.
RULE = TitleIdentity(
    strip_prefixes=["พระราชบัญญัติประกอบรัฐธรรมนูญ", "พระราชบัญญัติ"],
    year_particles=["พระพุทธศักราช", "พุทธศักราช", "พ.ศ."],
    edition_marker="ฉบับที่",
    consolidation_markers=["Update", "ครั้งที่"],
    max_length=100,
)


@pytest.mark.parametrize(
    ("title", "slug", "edition", "year"),
    [
        # The principal instrument and its amendment take different slugs.
        ("พระราชบัญญัติเครื่องหมายการค้า พ.ศ. 2534", "เครื่องหมายการค้า", "", "2534"),
        (
            "พระราชบัญญัติเครื่องหมายการค้า (ฉบับที่ 3) พ.ศ. 2559",
            "เครื่องหมายการค้า-ฉบับที่-3",
            "3",
            "2559",
        ),
        # Native digits reach the same identity as their ASCII spelling.
        ("พระราชบัญญัติเครื่องหมายการค้า พ.ศ. ๒๕๓๔", "เครื่องหมายการค้า", "", "2534"),
        # A longer kind word is stripped whole, not by its shorter prefix.
        ("พระราชบัญญัติประกอบรัฐธรรมนูญว่าด้วยพรรคการเมือง พ.ศ. 2560", "ว่าด้วยพรรคการเมือง", "", "2560"),
        # A re-publication marker is not part of the work's identity.
        (
            "พระราชบัญญัติเครื่องหมายการค้า พ.ศ. 2534 (ฉบับ Update ล่าสุด)",
            "เครื่องหมายการค้า",
            "",
            "2534",
        ),
        # An edition number after a re-publication marker is the edition folded
        # in, so it neither renames the work nor forks it from its own slug.
        (
            "พระราชบัญญัติบำเหน็จบำนาญ พ.ศ. 2500 (ฉบับ Update ล่าสุด) (ครั้งที่ 6) (ฉบับที่ 7)",
            "บำเหน็จบำนาญ",
            "",
            "2500",
        ),
        # Two years: the last is this instrument's, the earlier one names the
        # instrument being amended and stays, telling two amendments apart.
        (
            "พระราชบัญญัติแก้ไขเพิ่มเติมพระราชกำหนด พ.ศ. 2530 (ฉบับที่ 5) พ.ศ. 2543",
            "แก้ไขเพิ่มเติมพระราชกำหนด-พ-ศ-2530-ฉบับที่-5",
            "5",
            "2543",
        ),
        # A title stating no year still names the work.
        ("พระราชบัญญัติมหาวิทยาลัย (ฉบับ Update ล่าสุด)", "มหาวิทยาลัย", "", ""),
    ],
)
def test_identity_from_title(title: str, slug: str, edition: str, year: str) -> None:
    result = identity_from_title(title, RULE)
    assert (result.slug, result.edition, result.year) == (slug, edition, year)


def test_slug_keeps_the_marks_a_word_is_written_with() -> None:
    """`\\w` drops combining marks, so a slug built on it is not the title."""
    result = identity_from_title("พระราชบัญญัติเครื่องหมายการค้า พ.ศ. 2534", RULE)
    assert result.slug == "เครื่องหมายการค้า"
    assert "ื" in result.slug  # SARA UEE, a mark `\w` discards


def test_identity_is_a_pure_function_of_the_title() -> None:
    title = "พระราชบัญญัติเครื่องหมายการค้า (ฉบับที่ 3) พ.ศ. 2559"
    assert identity_from_title(title, RULE) == identity_from_title(title, RULE)
    # Typography a publisher varies must not fork the identity.
    assert (
        identity_from_title(title, RULE).slug
        == identity_from_title("พระราชบัญญัติ เครื่องหมายการค้า  (ฉบับที่ 3)  พ.ศ.2559", RULE).slug
    )


def test_slug_is_capped_at_the_declared_length() -> None:
    rule = RULE.model_copy(update={"max_length": 12})
    result = identity_from_title("พระราชบัญญัติ" + "ก" * 40 + " พ.ศ. 2534", rule)
    assert len(result.slug) == 12


def test_an_undeclared_grammar_derives_nothing() -> None:
    assert identity_from_title("Act No. 7 of 1992", TitleIdentity()).slug == "act-no-7-of-1992"
