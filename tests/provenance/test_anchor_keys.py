"""Source anchors are keyed by eId, which is unique once duplicates are renamed."""

from __future__ import annotations

from lxml import etree

from codify.akn.eids import ensure_unique_eids

_XML = (
    "<akomaNtoso xmlns='http://docs.oasis-open.org/legaldocml/ns/akn/3.0'><act><body>"
    "<paragraph eId='art_10' src='para10'/><paragraph eId='art_10' src='para10b1'/>"
    "<paragraph eId='art_101' src='para101'/></body></act></akomaNtoso>"
)


def test_two_source_ids_never_share_an_eid_after_duplicates_are_renamed() -> None:
    unique, _ = ensure_unique_eids(_XML)
    anchors = {
        el.get("eId"): el.get("src")
        for el in etree.fromstring(unique.encode()).iter()
        if el.get("src")
    }
    assert anchors == {"art_10": "para10", "art_10_dup2": "para10b1", "art_101": "para101"}
