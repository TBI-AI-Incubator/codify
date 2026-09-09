"""The evidence-only exclusion rules: what carries no law, and what must never be
excluded however it is punctuated or however many digits it carries."""

from __future__ import annotations

from codify.quality.sentinels import exclusion_reason, is_placeholder_only

MARKER = (r"^\s*Elucidation omitted\.?\s*$",)


def test_markers_and_shapes_are_excluded() -> None:
    assert exclusion_reason("[TIFF not transcribed: annex.tif]") == "placeholder"
    assert exclusion_reason("[FORMEX.DOC not transcribed: L_2019.doc]") == "placeholder"
    assert exclusion_reason("Elucidation omitted.", markers=MARKER) == "placeholder"
    assert exclusion_reason("| 7408 | Copper wire | 3 % |") == "table"
    assert exclusion_reason("Name of applicant ........................") == "table"
    assert exclusion_reason("☐ Yes ☐ No") == "table"
    assert exclusion_reason("2019 07 12 4045 89 1768 92", "att_1__paragraph_4") == "digits"


def test_law_is_never_excluded_by_length_colon_digits_or_a_form_word() -> None:
    # A colon-ended chapeau carries the operative verb; a short norm is still a norm.
    assert exclusion_reason("The city health officer shall:") is None
    assert exclusion_reason("Article 5 applies.") is None
    assert exclusion_reason("الوزير يصدر اللائحة التنفيذية:") is None
    assert (
        exclusion_reason("[TIFF not transcribed: a.tif] Member States shall keep records.") is None
    )
    assert exclusion_reason("Elucidation omitted.") is None  # no jurisdiction marker supplied
    assert exclusion_reason("Elucidation omitted. Article 3 applies.", markers=MARKER) is None
    # A unit stating an obligation is law however a table fragment sits inside it,
    # short or long.
    assert (
        exclusion_reason("A carrier shall record consignments as heading | description | rate.")
        is None
    )
    duty = (
        "A carrier shall record every consignment in the form heading | description | rate, "
        "and shall retain the manifest for six years after the movement it records."
    )
    assert exclusion_reason(duty) is None
    assert exclusion_reason("☐ The declarant must confirm the goods are as described.") is None
    # Blanks, enumerations and sums in the body are law, not forms or data.
    assert exclusion_reason("A sum of [ ] shall be inserted before signature.") is None
    assert exclusion_reason("Sections 12, 13, 14, 15, 16, 17.") is None
    assert exclusion_reason("Amount: 1,250,000") is None
    # Digits exclude only in an annex and only without an operative verb.
    assert exclusion_reason("2019 07 12 4045 89 1768 92") is None
    assert (
        exclusion_reason("Rates for 2019: 12, 13 and 14 shall apply.", "att_1__paragraph_2") is None
    )
    # An empty unit is a heading, not a placeholder.
    assert exclusion_reason("   ") is None


def test_placeholder_helper_honours_extra_markers() -> None:
    assert is_placeholder_only("[TIFF not transcribed: a.tif]")
    assert not is_placeholder_only("Elucidation omitted.")
    assert is_placeholder_only("Elucidation omitted.", markers=MARKER)
