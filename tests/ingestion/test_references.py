# ruff: noqa: E501  # AKN XML test fixtures keep single-line elements
"""Tests for the TLC <references> emitter."""

from __future__ import annotations

import pytest
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.jurisdictions import JurisdictionConfigError
from codify.pipeline.enrich.references import emit_references


def _act(body_extra: str = "") -> str:
    return f'''<?xml version="1.0"?>
<akomaNtoso xmlns="{AKN_NS}">
  <act>
    <meta><identification source="#codify"/></meta>
    <preamble><formula name="enactingFormula" refersTo="#codify"><p>Be it enacted.</p></formula></preamble>
    <body><section eId="sec_1"><content><p refersTo="#parliament">X{body_extra}</p></content></section></body>
  </act>
</akomaNtoso>
'''


class TestEmitReferences:
    def test_injects_only_referenced_tlcs(self):
        # The fixture references #codify (identification + formula source)
        # and #parliament (refersTo); crown and officialGazette are in the
        # gb catalogue but unreferenced, so they must not be emitted.
        out = emit_references(_act(), "gb")
        root = etree.fromstring(out.encode("utf-8"))
        refs = root.find(".//akn:meta/akn:references", NS)
        assert refs is not None
        eids = {c.get("eId") for c in refs}
        assert eids == {"parliament", "codify"}

    def test_no_references_element_when_nothing_referenced(self):
        bare = f'''<?xml version="1.0"?>
<akomaNtoso xmlns="{AKN_NS}">
  <act>
    <meta><identification source="#cobalt"/></meta>
    <body><section eId="sec_1"><content><p>X</p></content></section></body>
  </act>
</akomaNtoso>
'''
        out = emit_references(bare, "gb")
        root = etree.fromstring(out.encode("utf-8"))
        assert root.find(".//akn:references", NS) is None

    def test_tlc_class_used_as_element_name(self):
        out = emit_references(_act(), "gb")
        root = etree.fromstring(out.encode("utf-8"))
        refs = root.find(".//akn:meta/akn:references", NS)
        tags = {c.tag.split("}")[-1] for c in refs}
        assert tags == {"TLCOrganization"}

    def test_idempotent(self):
        once = emit_references(_act(), "gb")
        twice = emit_references(once, "gb")
        root = etree.fromstring(twice.encode("utf-8"))
        refs = root.find(".//akn:meta/akn:references", NS)
        eids = [c.get("eId") for c in refs]
        assert len(eids) == len(set(eids))  # no duplicates

    def test_an_unknown_country_raises(self):
        """It used to emit a document with no references, which reads as a law
        that cites nothing rather than a pass that had no rules to apply."""
        with pytest.raises(JurisdictionConfigError, match="zz"):
            emit_references(_act(), "zz")


class TestReconciliation:
    def test_stale_configured_entries_pruned_on_reprocess(self):
        contaminated = f'''<?xml version="1.0"?>
<akomaNtoso xmlns="{AKN_NS}">
  <act>
    <meta><identification source="#codify"/>
      <references source="#codify">
        <TLCOrganization eId="crown" href="/ontology/org/gb/crown" showAs="The Crown"/>
        <TLCOrganization eId="codify" href="/ontology/org/codify" showAs="Codify"/>
        <TLCOrganization eId="cobalt" href="https://github.com/laws-africa/cobalt" showAs="cobalt"/>
      </references>
    </meta>
    <body><section eId="sec_1"><content><p>X</p></content></section></body>
  </act>
</akomaNtoso>
'''
        out = emit_references(contaminated, "gb")
        root = etree.fromstring(out.encode("utf-8"))
        eids = {c.get("eId") for c in root.find(".//akn:meta/akn:references", NS)}
        # crown is configured but unreferenced: pruned. codify is
        # referenced (identification source); cobalt is not ours to touch.
        assert eids == {"codify", "cobalt"}
