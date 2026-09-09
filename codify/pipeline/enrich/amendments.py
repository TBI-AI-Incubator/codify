"""Lift Bluebell quote blocks into <mod>/<quotedStructure> pairs.

Pure XML transformation. Every `<block name="quote">` containing an
`<embeddedStructure>` is wrapped, whether the replacement content is
nested provisions or prose paragraphs doesn't matter here. The
amendment_parser agent classifies each resulting <mod> and flags false
positives via `is_amendment_operation=false`.
"""

from __future__ import annotations

from typing import cast

import structlog
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.akn.eid import EID_ABBREV

logger = structlog.get_logger()


def lift_amendment_markup(akn_xml: str) -> str:
    """Wrap every Bluebell quote block in <mod>/<quotedStructure>.

    Mods are eId-scoped to their nearest eId-bearing ancestor so sibling
    mods get sequential ids (sec_8__mod_1, sec_8__mod_2, att_1__mod_1, …).
    """
    root = etree.fromstring(akn_xml.encode("utf-8"))

    groups: dict[etree._Element, list[etree._Element]] = {}
    orphan_blocks: list[etree._Element] = []

    for block in root.findall(".//akn:block[@name='quote']", NS):
        if block.find("akn:embeddedStructure", NS) is None:
            continue
        container = _nearest_structural_ancestor(block)
        if container is None:
            orphan_blocks.append(block)
        else:
            groups.setdefault(container, []).append(block)

    total_mods = 0

    def _emit_mod(block: etree._Element, container_eid: str, local_idx: int) -> None:
        nonlocal total_mods
        embedded = block.find("akn:embeddedStructure", NS)
        if embedded is None:  # pragma: no cover, gated in the caller
            return

        mod_id = f"{container_eid}__mod_{local_idx}" if container_eid else f"mod_{total_mods + 1}"
        total_mods += 1

        embedded.tag = f"{{{AKN_NS}}}quotedStructure"
        qstr_id = f"{mod_id}__qstr_1"
        embedded.set("eId", qstr_id)
        _reprefix_eids(embedded, qstr_id)

        mod = etree.Element(f"{{{AKN_NS}}}mod")
        mod.set("eId", mod_id)

        parent = block.getparent()
        if parent is None:  # pragma: no cover
            return
        block_idx = list(parent).index(block)
        block.remove(embedded)
        mod.append(embedded)
        parent.remove(block)
        parent.insert(block_idx, mod)

    for container, blocks in groups.items():
        container_eid = container.get("eId", "")
        for local_idx, block in enumerate(blocks, start=1):
            _emit_mod(block, container_eid, local_idx)

    for block in orphan_blocks:
        _emit_mod(block, container_eid="", local_idx=0)

    if total_mods > 0:
        logger.info("amendment_lift_complete", mods=total_mods)

    return cast(str, etree.tostring(root, pretty_print=True, encoding="unicode"))


def _nearest_structural_ancestor(element: etree._Element) -> etree._Element | None:
    """Nearest ancestor carrying an eId, section, article, attachment, item…

    Any AKN element with an eId is a legitimate scope for mod numbering,
    including schedules (<attachment>) and list items. Returns None if the
    element sits directly under <body> or <mainBody> with no eId'd wrapper.
    """
    parent = element.getparent()
    while parent is not None:
        if parent.get("eId"):
            return parent
        parent = parent.getparent()
    return None


# The canonical prefixes, plus the non-hierarchical tags a quoted structure
# carries. Anything absent keeps its own lowercased name.
_TAG_ABBR = {
    **EID_ABBREV,
    "blockContainer": "bc",
    "heading": "hd",
    "intro": "intro",
    "mod": "mod",
    "num": "num",
    "p": "p",
    "subitem": "subitem",
    "subpoint": "subpoint",
}


def _reprefix_eids(element: etree._Element, parent_eid: str) -> None:
    """Replace every descendant's eId with a deterministic positional
    path beneath `parent_eid`. The original eIds inside an embedded
    structure are not meaningful (the Bluebell parser restarts numbering
    inside each quote block, so siblings can collide on `p_1`); positional
    paths guarantee uniqueness across the document."""

    def assign(parent_el: etree._Element, parent_path: str) -> None:
        per_tag: dict[str, int] = {}
        for child in parent_el:
            tag = etree.QName(child).localname if isinstance(child.tag, str) else ""
            abbr = _TAG_ABBR.get(tag, tag.lower())
            idx = per_tag.get(abbr, 0) + 1
            per_tag[abbr] = idx
            new_path = f"{parent_path}__{abbr}_{idx}"
            if child.get("eId") is not None:
                child.set("eId", new_path)
            assign(child, new_path)

    assign(element, parent_eid)
