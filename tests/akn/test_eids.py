"""eId-uniqueness guarantee for serialised AKN."""

from __future__ import annotations

import pytest
from lxml import etree

from codify.akn.eids import ensure_unique_eids

_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def test_duplicate_eids_get_suffixed_first_kept() -> None:
    xml = (
        f'<akomaNtoso xmlns="{_NS}"><act><body>'
        '<term eId="term-x" refersTo="#term-x">x</term>'
        '<term eId="term-x" refersTo="#term-x">x</term>'
        '<term eId="term-x" refersTo="#term-x">x</term>'
        "</body></act></akomaNtoso>"
    )
    out, renamed = ensure_unique_eids(xml)
    assert renamed == 2
    eids = [e.get("eId") for e in etree.fromstring(out.encode()).iter(f"{{{_NS}}}term")]
    assert eids == ["term-x", "term-x_dup2", "term-x_dup3"]
    # refersTo still points at the canonical first occurrence.
    refs = {e.get("refersTo") for e in etree.fromstring(out.encode()).iter(f"{{{_NS}}}term")}
    assert refs == {"#term-x"}


def test_unique_eids_unchanged() -> None:
    xml = f'<akomaNtoso xmlns="{_NS}"><act><body><section eId="sec_1"/></body></act></akomaNtoso>'
    out, renamed = ensure_unique_eids(xml)
    assert renamed == 0


def test_a_doctype_entity_is_not_resolved() -> None:
    """This runs on publisher-supplied XML and its output is persisted as the
    canonical document, so a resolved entity would outlive the request that
    carried it. The hardened parser refuses the declaration outright."""
    payload = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE a [<!ENTITY x "PWNED">]>'
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><body><p>&x;</p></body></act></akomaNtoso>"
    )
    # Refused outright: the hardened parser leaves the reference unresolved and
    # serialising drops the DOCTYPE that defined it, so persisting the result
    # would store a reference nothing can resolve.
    with pytest.raises(ValueError, match="DOCTYPE"):
        ensure_unique_eids(payload)


def test_large_tree_parsing_requires_an_explicit_opt_in() -> None:
    oversized = f"<akomaNtoso><doc><p>{'A' * 10_000_005}</p></doc></akomaNtoso>"

    with pytest.raises(etree.XMLSyntaxError, match="Resource limit exceeded"):
        ensure_unique_eids(oversized)

    output, renamed = ensure_unique_eids(oversized, huge_tree=True)
    assert renamed == 0
    assert "A" * 100 in output


def _terms(ids: list[str], ref: str) -> str:
    """Terms carrying the caller's ids, and one reference into them."""
    terms = "".join(
        f'<term eId="{eid}" refersTo="#concept">Original element {i}</term>'
        for i, eid in enumerate(ids, start=1)
    )
    return (
        f'<akomaNtoso xmlns="{_NS}"><act><body><section eId="sec_1"><num>1</num>'
        f'<content><p>{terms}<ref href="#{ref}">see</ref></p></content>'
        "</section></body></act></akomaNtoso>"
    )


def _term_ids(xml: str) -> list[str | None]:
    return [el.get("eId") for el in etree.fromstring(xml.encode()).iter(f"{{{_NS}}}term")]


def _text_at(xml: str, eid: str) -> list[str]:
    root = etree.fromstring(xml.encode())
    return ["".join(el.itertext()) for el in root.xpath("//*[@eId=$v]", v=eid)]


def test_a_minted_eid_never_takes_one_the_document_publishes() -> None:
    """Ids x, x, x_dup2: before repair `#x_dup2` names the third element and
    nothing else. Renaming the second `x` to `x_dup2` would keep every id
    unique while moving that reference onto the second element (#95)."""
    out, renamed = ensure_unique_eids(_terms(["x", "x", "x_dup2"], "x_dup2"))
    assert renamed == 1
    assert _term_ids(out) == ["x", "x_dup3", "x_dup2"]
    assert _text_at(out, "x_dup2") == ["Original element 3"]


def test_a_published_suffix_before_its_base_is_kept() -> None:
    out, renamed = ensure_unique_eids(_terms(["x_dup2", "x", "x"], "x_dup2"))
    assert renamed == 1
    assert _term_ids(out) == ["x_dup2", "x", "x_dup3"]
    assert _text_at(out, "x_dup2") == ["Original element 1"]


def test_the_issue_example_still_repairs_cleanly() -> None:
    """x, x_dup1, x never collided: minted names count from _dup2."""
    out, renamed = ensure_unique_eids(_terms(["x", "x_dup1", "x"], "x_dup1"))
    assert renamed == 1
    assert _term_ids(out) == ["x", "x_dup1", "x_dup2"]
    assert _text_at(out, "x_dup1") == ["Original element 2"]
