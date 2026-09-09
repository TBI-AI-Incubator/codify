"""Invariants for the Work-scoped identifier (`akn_wid`) on parsed elements.

`akn_eid` is Expression-scoped and mutates on renumbering; `akn_wid` is
Work-scoped and stable across renumbering (AKN 3.0 Naming Convention).
Anchors and the parser both populate `akn_wid` so downstream reads never
need a fallback."""

from __future__ import annotations

from lxml import etree

from codify.akn._parser import _parse_element
from codify.akn._schema import AKN_NS
from codify.pipeline.enrich.anchors import StructuralAnchor, _assign_eids


def test_fresh_anchor_wid_equals_eid() -> None:
    anchors = [
        StructuralAnchor(
            kind="section",
            keyword="Section",
            number="1",
            char_offset=0,
            line=1,
            matched_text="Section 1",
        ),
        StructuralAnchor(
            kind="paragraph",
            keyword="Paragraph",
            number="1",
            char_offset=10,
            line=2,
            matched_text="Paragraph 1",
        ),
    ]
    assigned = _assign_eids(anchors)
    assert all(a.akn_wid == a.akn_eid for a in assigned)
    assert assigned[0].akn_eid == "sec_1"
    assert assigned[1].akn_eid == "sec_1__para_1"


def test_akn_wid_defaults_empty_on_bare_anchor() -> None:
    a = StructuralAnchor(
        kind="section",
        keyword="Section",
        number="1",
        char_offset=0,
        line=1,
        matched_text="Section 1",
    )
    assert a.akn_wid == ""
    assert a.akn_eid == ""


def _paragraph_xml(*, eid: str, wid: str | None) -> etree._Element:
    """Build a minimal AKN <paragraph> under position 0 for parser tests."""
    ns = f"{{{AKN_NS}}}"
    attrs = {"eId": eid}
    if wid is not None:
        attrs["wId"] = wid
    el = etree.Element(f"{ns}paragraph", attrib=attrs)
    content = etree.SubElement(el, f"{ns}content")
    p = etree.SubElement(content, f"{ns}p")
    p.text = "body text"
    return el


def test_parser_reads_wid_when_present() -> None:
    """Imported AKN with explicit @wId preserves it distinct from @eId."""
    el = _paragraph_xml(eid="sec_1__para_2", wid="sec_1__para_1_orig")
    parsed = _parse_element(el, position=0)
    assert parsed is not None
    assert parsed.akn_eid == "sec_1__para_2"
    assert parsed.akn_wid == "sec_1__para_1_orig"


def test_parser_defaults_wid_to_eid_when_absent() -> None:
    """Fresh AKN (no @wId), parser defaults wId to eId so downstream writes
    always have a non-empty Work-scoped identifier."""
    el = _paragraph_xml(eid="sec_1__para_1", wid=None)
    parsed = _parse_element(el, position=0)
    assert parsed is not None
    assert parsed.akn_wid == "sec_1__para_1"
    assert parsed.akn_wid == parsed.akn_eid


def test_parser_strips_whitespace_wid() -> None:
    """A malformed @wId with surrounding whitespace falls back to eId
    rather than propagating the whitespace into the join key."""
    el = _paragraph_xml(eid="sec_1__para_1", wid="   ")
    parsed = _parse_element(el, position=0)
    assert parsed is not None
    assert parsed.akn_wid == "sec_1__para_1"
