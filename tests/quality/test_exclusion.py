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


def test_a_unit_under_an_excluded_heading_leaves_the_pool() -> None:
    duty = "The Secretariat shall publish the register."
    assert exclusion_reason(duty, under_excluded_heading=True) == "heading"
    assert exclusion_reason(duty) is None
    # A placeholder keeps its own reason; an empty unit is still judged elsewhere.
    assert exclusion_reason("[TIFF not transcribed: a.tif]", under_excluded_heading=True) == (
        "placeholder"
    )
    assert exclusion_reason("", under_excluded_heading=True) is None


def test_heading_scope_covers_the_container_and_everything_under_it() -> None:
    from codify.akn import Article, Paragraph, Point, Section
    from codify.quality.sentinels import SCOPING_KINDS, heading_scoped
    from codify.storage.mappers import ROW_SECTION_KINDS

    point = Point(akn_eid="sec_2__para_2__point_a", akn_type="point", position=0, text="x")
    para = Paragraph(akn_eid="sec_2__para_2", akn_type="paragraph", position=0, children=[point])
    invites = Section(
        akn_eid="sec_2", akn_type="section", position=1, heading=" INVITES ", children=[para]
    )
    kept = Section(
        akn_eid="sec_1",
        akn_type="section",
        position=0,
        heading="RECOMMENDS",
        children=[Article(akn_eid="sec_1__art_1", akn_type="article", position=0, text="y")],
    )
    # A leaf's heading never scopes: only the kinds stored as section rows do. The
    # match is as printed, so an article titled in sentence case is not a verb heading.
    headed_leaf = Paragraph(akn_eid="para_9", akn_type="paragraph", position=2, heading="INVITES")
    titled = Article(akn_eid="art_9", akn_type="article", position=3, heading="Invites", text="z")
    scoped = heading_scoped([kept, invites, headed_leaf, titled], frozenset({"INVITES"}))
    assert scoped == {invites.id, para.id, point.id}
    assert heading_scoped([kept, invites], frozenset()) == frozenset()
    # The backfill scopes by section rows, so the two kind sets must agree.
    assert SCOPING_KINDS == ROW_SECTION_KINDS
