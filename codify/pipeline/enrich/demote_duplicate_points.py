"""Demote duplicate-num `<point>` siblings whose num is a misread inline citation.

The body-fill LLM promotes an inline reference like "to clause (1) of this article"
into a new top-level `<point>`, splitting one sentence across the prior point and the
spurious one. The dupe continues the prior sibling's deepest leaf, so its intro merges
there, its sub-points move under it, and the wrapper goes.

Two shapes merge: both sides nested, or both bare `<content>` with the dupe short
enough to be a fragment. Asymmetric duplicates go to the validator.
"""

from __future__ import annotations

from lxml import etree

from codify.akn import AKN_NS
from codify.lang import normalise_digits

_POINT_TAG = f"{{{AKN_NS}}}point"
_NUM_TAG = f"{{{AKN_NS}}}num"
_INTRO_TAG = f"{{{AKN_NS}}}intro"
_CONTENT_TAG = f"{{{AKN_NS}}}content"
_P_TAG = f"{{{AKN_NS}}}p"


def _num_text(point: etree._Element) -> str:
    """The point's num as a duplicate-detection key. Non-ASCII digits fold to ASCII so `(۳)`
    and `(٣)` collide: both render as "3" and mean the same position, and without the fold
    the structurer emits them as distinct siblings and the heuristic misses the merge.
    """
    num_el = point.find(_NUM_TAG)
    if num_el is None:
        return ""
    return normalise_digits("".join(num_el.itertext()).strip())


def _direct_point_children(parent: etree._Element) -> list[etree._Element]:
    return [c for c in parent if c.tag == _POINT_TAG]


def _last_leaf_point(
    point: etree._Element, moved: set[etree._Element] | None = None
) -> etree._Element:
    """The deepest rightmost descendant `<point>`, skipping any this pass moved.
    Descending into them puts each duplicate a level below the last, which turns
    a flat list into a chain as long as itself."""
    current = point
    while True:
        children = [c for c in _direct_point_children(current) if not moved or c not in moved]
        if not children:
            return current
        current = children[-1]


def _ensure_intro(point: etree._Element) -> etree._Element:
    """Return the point's `<intro>` element, creating it from existing
    `<content>` if needed. The caller is about to attach `<point>` children,
    which makes this point a container, AKN container points hold prose in
    `<intro>`, not `<content>`."""
    existing_intro = point.find(_INTRO_TAG)
    if existing_intro is not None:
        return existing_intro
    new_intro = etree.SubElement(point, _INTRO_TAG)
    content = point.find(_CONTENT_TAG)
    if content is not None:
        for child in list(content):
            new_intro.append(child)
        point.remove(content)
    # Place <intro> immediately after <num> so the document order matches AKN.
    num = point.find(_NUM_TAG)
    if num is not None:
        point.remove(new_intro)
        num.addnext(new_intro)
    return new_intro


def _append_paragraphs(source: etree._Element, target: etree._Element) -> None:
    """Append source's `<p>` children to target: join the first source `<p>`
    onto target's last `<p>` with a space (the two were one sentence the LLM
    split), then append the rest verbatim. Both `source` and `target` are
    already-resolved containers (an `<intro>` or a `<content>`)."""
    source_ps = list(source.findall(_P_TAG))
    if not source_ps:
        return
    target_ps = list(target.findall(_P_TAG))
    first_text = "".join(source_ps[0].itertext()).strip()
    if target_ps and first_text:
        last = target_ps[-1]
        existing = (last.text or "").rstrip()
        # Join with a single space so the rebuilt sentence reads naturally.
        last.text = f"{existing} {first_text}" if existing else first_text
    elif first_text:
        new_p = etree.SubElement(target, _P_TAG)
        new_p.text = first_text
    for extra in source_ps[1:]:
        target.append(extra)


# Past this a num is not one misread citation, so the pass declines and the
# validator reports it. Every fixture repairs a single duplicate; the shape this
# guards against repeats a num 9 to 45 times.
_MAX_OCCURRENCES = 3


def demote_duplicate_points(root: etree._Element) -> int:
    """Walk every article; merge each duplicate-num `<point>` whose previous
    sibling shares the num AND both have nested children. Returns the count
    of demotions performed."""
    fixed = 0
    for art in root.iter(f"{{{AKN_NS}}}article"):
        children = _direct_point_children(art)
        if len(children) < 2:
            continue
        # Counted before any merging: a num past the cap is left whole, on
        # either branch, because a wrong repair loses the original numbering
        # and a reported defect does not.
        occurrences: dict[str, int] = {}
        for child in children:
            num = _num_text(child)
            if num:
                occurrences[num] = occurrences.get(num, 0) + 1
        moved: set[etree._Element] = set()
        seen_nums: dict[str, etree._Element] = {}
        # Walk in document order; for each child, check whether its num
        # matches one we've already seen at this level.
        for child in list(children):
            num = _num_text(child)
            if not num or occurrences.get(num, 0) > _MAX_OCCURRENCES:
                continue
            prior = seen_nums.get(num)
            if prior is None:
                seen_nums[num] = child
                continue
            # Duplicate. Trigger only if BOTH prior and dupe carry nested points.
            dupe_nested = _direct_point_children(child)
            # The previous sibling is the most recent direct point, not the first
            # occurrence of the num, so walk the article's children backwards from the
            # dupe's position.
            siblings = _direct_point_children(art)
            try:
                idx = siblings.index(child)
            except ValueError:
                continue
            if idx == 0:
                continue
            prev_sibling = siblings[idx - 1]
            prev_nested = _direct_point_children(prev_sibling)
            if dupe_nested and prev_nested:
                # Both have nested children, the art_١٥٦ shape. Treat the dupe
                # as a continuation of the prior sibling's deepest leaf.
                leaf = _last_leaf_point(prev_sibling, moved)
                target_intro = _ensure_intro(leaf)
                dupe_intro = child.find(_INTRO_TAG)
                if dupe_intro is not None:
                    _append_paragraphs(dupe_intro, target_intro)
                for nested in dupe_nested:
                    leaf.append(nested)
                    moved.add(nested)
                art.remove(child)
                fixed += 1
                continue
            if not dupe_nested and not prev_nested and _is_short_dupe(child):
                # Bare-content shape (art_٢٦٣): both sides carry only `<content>` and
                # the dupe is short, so it is a split-off sentence fragment rather than
                # a parallel definition. The short-dupe guard is what stops two
                # genuinely distinct short points merging.
                _merge_bare_content(prev_sibling, child)
                art.remove(child)
                fixed += 1
                continue
            # Asymmetric structure (one side nested, the other not), different
            # failure mode, leave alone for the validator to surface.
            continue
    return fixed


_SHORT_DUPE_THRESHOLD = 200  # chars; longer than this suggests a real parallel point


def _is_short_dupe(point: etree._Element) -> bool:
    text = "".join(point.itertext())
    return len(text.strip()) < _SHORT_DUPE_THRESHOLD


def _merge_bare_content(prior: etree._Element, dupe: etree._Element) -> None:
    """Append the dupe's content `<p>` elements to the prior sibling's. Both carry `<content>`
    with one or more `<p>` children, and the dupe is almost always a fragment broken off
    mid-sentence, so concatenating its prose to the prior sibling's last `<p>`, separated
    by a space, rebuilds the original sentence.
    """
    prior_content = prior.find(_CONTENT_TAG)
    if prior_content is None:
        return
    dupe_content = dupe.find(_CONTENT_TAG)
    if dupe_content is None:
        return
    _append_paragraphs(dupe_content, prior_content)


__all__ = ["demote_duplicate_points"]
