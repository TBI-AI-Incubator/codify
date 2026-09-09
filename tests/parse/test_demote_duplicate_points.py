# ruff: noqa: E501  # AKN XML test fixtures: line wraps would distort whitespace
"""Tests for the demote-duplicate-points post-pass.

Targets the body-fill LLM failure mode where an inline citation like
"clause (1) of this article" is misread as a new top-level enumerator,
splitting one sentence across two `<point>` elements with the same `<num>`.
"""

from __future__ import annotations

from lxml import etree

from codify.akn import AKN_NS
from codify.pipeline.enrich.demote_duplicate_points import demote_duplicate_points


def _act(body: str) -> etree._Element:
    return etree.fromstring(
        f'<akomaNtoso xmlns="{AKN_NS}"><act><body>{body}</body></act></akomaNtoso>'.encode()
    )


def _max_point_depth(el, depth=0):
    kids = [c for c in el if c.tag == f"{{{AKN_NS}}}point"]
    return depth if not kids else max(_max_point_depth(k, depth + 1) for k in kids)


def _points(el):
    return [c for c in el if c.tag == f"{{{AKN_NS}}}point"]


def _nested_repeats(count: int) -> etree._Element:
    """A flat list reusing one enumerator, each item carrying a sub-point: the
    shape an amending decree-law takes when the num is repeated rather than
    incremented."""
    items = "".join(
        f'<point eId="art_1__point_{i}"><num>(1)</num><intro><p>item {i}.</p></intro>'
        f'<point eId="art_1__point_{i}__point_a"><num>(a)</num>'
        f"<content><p>sub {i}</p></content></point></point>"
        for i in range(1, count + 1)
    )
    return _act(f'<article eId="art_1"><num>1</num>{items}</article>')


def test_within_cap_duplicates_do_not_deepen_each_other():
    """Each duplicate continues the prior sibling's original leaf. Targeting the
    current deepest leaf instead put every duplicate one level below the last,
    which is how a flat list became a chain as long as itself."""
    root = _nested_repeats(3)
    art = root.find(f".//{{{AKN_NS}}}article")
    assert _max_point_depth(art) == 2

    assert demote_duplicate_points(root) == 2
    assert _max_point_depth(art) == 3


def test_a_num_repeated_past_the_cap_is_left_whole():
    """Ten repetitions is not one misread citation, so the pass declines and the
    validator reports it: a wrong repair loses the numbering, a report does not."""
    root = _nested_repeats(10)
    art = root.find(f".//{{{AKN_NS}}}article")

    assert demote_duplicate_points(root) == 0
    assert len(_points(art)) == 10
    assert _max_point_depth(art) == 2


def test_the_cap_covers_the_bare_content_branch_too():
    """The cap sits in front of both merge branches. Without it here, ten
    provisions collapsed into one paragraph rather than into a deep chain."""
    items = "".join(
        f'<point eId="art_1__point_{i}"><num>(1)</num>'
        f"<content><p>fragment {i}.</p></content></point>"
        for i in range(1, 11)
    )
    root = _act(f'<article eId="art_1"><num>1</num>{items}</article>')
    art = root.find(f".//{{{AKN_NS}}}article")

    assert demote_duplicate_points(root) == 0
    assert len(_points(art)) == 10


def test_no_duplicates_noop():
    root = _act("""
    <article eId="art_1"><num>1</num>
      <point eId="art_1__point_1"><num>(1)</num><content><p>one</p></content></point>
      <point eId="art_1__point_2"><num>(2)</num><content><p>two</p></content></point>
    </article>
    """)
    assert demote_duplicate_points(root) == 0


def test_bare_short_duplicate_merges():
    """Short bare-content duplicates merge into the prior sibling, they're
    almost always a sentence fragment broken off by the body-fill LLM. Longer
    bare duplicates are handled by ``test_bare_long_dupe_not_merged`` below."""
    root = _act("""
    <article eId="art_1"><num>1</num>
      <point eId="art_1__point_1"><num>(1)</num><content><p>first clause.</p></content></point>
      <point eId="art_1__point_1_2"><num>(1)</num><content><p>fragment.</p></content></point>
    </article>
    """)
    assert demote_duplicate_points(root) == 1


def test_duplicate_with_nested_children_demoted():
    """The signature pattern: prior sibling AND dupe both have nested points.
    The dupe is treated as a continuation of the prior sibling's last leaf:
    its intro text appends to the leaf and its children move under the leaf."""
    root = _act("""
    <article eId="art_1"><num>1</num>
      <point eId="art_1__point_1"><num>(1)</num>
        <intro><p>first</p></intro>
        <point eId="art_1__point_1__point_a"><num>(a)</num><content><p>1a</p></content></point>
      </point>
      <point eId="art_1__point_2"><num>(2)</num>
        <intro><p>applies to clause</p></intro>
        <point eId="art_1__point_2__point_a"><num>(a)</num><content><p>from paragraph</p></content></point>
      </point>
      <point eId="art_1__point_1_2"><num>(1)</num>
        <intro><p>of this article, where</p></intro>
        <point eId="art_1__point_1_2__point_a"><num>(a)</num><content><p>adult</p></content></point>
        <point eId="art_1__point_1_2__point_b"><num>(b)</num><content><p>certificate</p></content></point>
      </point>
    </article>
    """)
    n = demote_duplicate_points(root)
    assert n == 1

    # The article now has 2 direct points, not 3.
    art = root.iter(f"{{{AKN_NS}}}article").__next__()
    direct_points = [c for c in art if c.tag == f"{{{AKN_NS}}}point"]
    assert len(direct_points) == 2
    # The dupe wrapper is gone.
    assert root.find(f".//{{{AKN_NS}}}point[@eId='art_1__point_1_2']") is None

    # The dupe's children now live under the prior sibling's leaf.
    leaf = root.find(f".//{{{AKN_NS}}}point[@eId='art_1__point_2__point_a']")
    assert leaf is not None
    nested_in_leaf = leaf.findall(f"{{{AKN_NS}}}point")
    assert {p.get("eId") for p in nested_in_leaf} == {
        "art_1__point_1_2__point_a",
        "art_1__point_1_2__point_b",
    }


def test_leaf_content_promoted_to_intro_on_merge():
    """The leaf was a content-only point. After children attach, it should
    hold its prose in `<intro>` (container form), not `<content>` (leaf form)."""
    root = _act("""
    <article eId="art_1"><num>1</num>
      <point eId="art_1__point_1"><num>(1)</num>
        <intro><p>first</p></intro>
        <point eId="art_1__p1__a"><num>(a)</num><content><p>1a</p></content></point>
      </point>
      <point eId="art_1__point_2"><num>(2)</num>
        <intro><p>second</p></intro>
        <point eId="art_1__p2__a"><num>(a)</num><content><p>leaf-prose</p></content></point>
      </point>
      <point eId="art_1__point_1_2"><num>(1)</num>
        <intro><p>continuation</p></intro>
        <point eId="art_1__dup__a"><num>(a)</num><content><p>moved</p></content></point>
      </point>
    </article>
    """)
    assert demote_duplicate_points(root) == 1
    leaf = root.find(f".//{{{AKN_NS}}}point[@eId='art_1__p2__a']")
    assert leaf is not None
    assert leaf.find(f"{{{AKN_NS}}}content") is None
    intro = leaf.find(f"{{{AKN_NS}}}intro")
    assert intro is not None
    intro_text = " ".join(intro.itertext()).strip()
    # Merge joined the original 'leaf-prose' with the dupe's intro 'continuation'.
    assert "leaf-prose" in intro_text
    assert "continuation" in intro_text


def test_only_dupe_with_nested_not_prior_left_alone():
    """If only one side has nested children, the asymmetric structure is more
    likely a different bug, leave it alone (validator will still flag it)."""
    root = _act("""
    <article eId="art_1"><num>1</num>
      <point eId="art_1__point_1"><num>(1)</num><content><p>bare</p></content></point>
      <point eId="art_1__point_1_2"><num>(1)</num>
        <intro><p>has children</p></intro>
        <point eId="art_1__point_1_2__a"><num>(a)</num><content><p>nested</p></content></point>
      </point>
    </article>
    """)
    assert demote_duplicate_points(root) == 0


def test_first_point_position_zero_skipped():
    """A duplicate that has no previous sibling (would happen only if the
    first child is somehow a dupe of itself, defensive guard)."""
    root = _act("""
    <article eId="art_1"><num>1</num>
      <point eId="art_1__point_1"><num>(1)</num>
        <intro><p>x</p></intro>
        <point eId="art_1__p1__a"><num>(a)</num><content><p>y</p></content></point>
      </point>
    </article>
    """)
    assert demote_duplicate_points(root) == 0


def test_bare_short_dupe_appends_to_prior_content():
    """The art_٢٦٣ pattern: two `<point>`s with the same num, both bare-content,
    the dupe carrying a short sentence fragment. Merge the fragment into the
    prior sibling's last <p>."""
    root = _act("""
    <article eId="art_1"><num>1</num>
      <point eId="art_1__point_1"><num>(1)</num>
        <content><p>The first long passage about taking property by deceit ending mid-sentence,</p></content>
      </point>
      <point eId="art_1__point_1_2"><num>(1)</num>
        <content><p>by any of the tricks.</p></content>
      </point>
    </article>
    """)
    n = demote_duplicate_points(root)
    assert n == 1
    art = next(root.iter(f"{{{AKN_NS}}}article"))
    points = [c for c in art if c.tag == f"{{{AKN_NS}}}point"]
    assert len(points) == 1
    merged = points[0]
    p_text = "".join(merged.find(f"{{{AKN_NS}}}content").find(f"{{{AKN_NS}}}p").itertext())
    assert "by any of the tricks" in p_text
    assert "ending mid-sentence" in p_text


def test_bare_long_dupe_not_merged():
    """A long bare-content duplicate is more likely a genuinely-distinct point
    than a fragment continuation, leave it alone."""
    long_text = "x" * 250
    root = _act(f"""
    <article eId="art_1"><num>1</num>
      <point eId="art_1__point_1"><num>(1)</num>
        <content><p>First clause.</p></content>
      </point>
      <point eId="art_1__point_1_2"><num>(1)</num>
        <content><p>{long_text}</p></content>
      </point>
    </article>
    """)
    assert demote_duplicate_points(root) == 0


def test_arabic_indic_vs_persian_digit_collide_under_normalisation():
    """A `<num>` with `(۳)` (Persian) and another with `(٣)` (Arabic-Indic) refer
    to the same logical position. The demote pass folds digits before comparing
    so the duplicate is recognised. (The validator doesn't currently flag
    these as duplicates, different question, but the structurer should still
    repair them when it sees them.)"""
    root = _act("""
    <article eId="art_1"><num>1</num>
      <point eId="art_1__point_3"><num>(٣)</num>
        <content><p>Arabic-Indic three.</p></content>
      </point>
      <point eId="art_1__point_3_2"><num>(۳)</num>
        <content><p>Persian three — fragment.</p></content>
      </point>
    </article>
    """)
    assert demote_duplicate_points(root) == 1
