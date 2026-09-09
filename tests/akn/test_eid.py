from __future__ import annotations

from functools import cmp_to_key

from codify.akn.eid import (
    ancestor_chain,
    compare_document_order,
    parent_eid,
    parse_eid,
)


def test_parse_eid_returns_containers_in_document_order():
    parts = parse_eid("tit_2__chp_1__sec_3__art_12__para_1")
    assert [(p.prefix, p.number, p.kind) for p in parts] == [
        ("tit", "2", "title"),
        ("chp", "1", "chapter"),
        ("sec", "3", "section"),
        ("art", "12", "article"),
        ("para", "1", "paragraph"),
    ]


def test_parse_eid_preserves_sub_numbered_identifiers():
    parts = parse_eid("chp_1__para_5_a")
    assert parts[1].prefix == "para"
    assert parts[1].number == "5_a"
    assert parts[1].kind == "paragraph"


def test_parse_eid_marks_unknown_prefixes_with_none_kind():
    parts = parse_eid("foo_3__art_1")
    assert parts[0].kind is None
    assert parts[1].kind == "article"


def test_parse_eid_empty_input():
    assert parse_eid("") == []


def test_ancestor_chain_root_to_self_inclusive():
    assert ancestor_chain("chp_1__art_5__para_2") == [
        "chp_1",
        "chp_1__art_5",
        "chp_1__art_5__para_2",
    ]


def test_ancestor_chain_single_segment():
    assert ancestor_chain("chp_1") == ["chp_1"]


def test_ancestor_chain_empty():
    assert ancestor_chain("") == []


def test_parent_eid_strips_rightmost():
    assert parent_eid("chp_1__art_5__para_2") == "chp_1__art_5"
    assert parent_eid("chp_1__art_5") == "chp_1"


def test_parent_eid_root_returns_none():
    assert parent_eid("chp_1") is None
    assert parent_eid("") is None


def test_compare_document_order_orders_numerically():
    eids = ["chp_2__art_1", "chp_1__art_10", "chp_1__art_2", "chp_1__art_1"]
    eids.sort(key=cmp_to_key(compare_document_order))
    assert eids == ["chp_1__art_1", "chp_1__art_2", "chp_1__art_10", "chp_2__art_1"]


def test_compare_document_order_handles_sub_numbered():
    assert compare_document_order("art_1__para_5_a", "art_1__para_5_b") < 0
    assert compare_document_order("art_1__para_5_b", "art_1__para_5_a") > 0


def test_compare_document_order_shallower_before_deeper():
    assert compare_document_order("chp_1", "chp_1__art_1") < 0
    assert compare_document_order("chp_1__art_1", "chp_1") > 0


def test_compare_document_order_uses_container_kind_depth():
    # Title comes before Chapter in document order.
    assert compare_document_order("tit_2", "chp_1") < 0


def test_the_abbreviations_agree_with_bluebell() -> None:
    """Bluebell serialises what we store, so a disagreement here is a rename
    nothing else would report."""
    from bluebell.xml import IdGenerator

    from codify.akn.eid import eid_abbrev

    assert {k: eid_abbrev(k) for k in IdGenerator.aliases} == IdGenerator.aliases
