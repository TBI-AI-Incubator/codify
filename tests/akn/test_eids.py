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
