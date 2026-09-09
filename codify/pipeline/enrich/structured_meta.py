"""Inject structured-parser amendment annotations into AKN analysis meta.

A structured parser recovers per-unit amendment notes (which unit was
amended, by which act, with what action). This writes them as
``<passiveModifications><textualMod>`` entries in the document's
``<analysis>`` block, the shape the AKN reader already turns into
``amendment_effects`` rows. Idempotent: existing codify-sourced entries are
replaced, not duplicated.
"""

from __future__ import annotations

from collections.abc import Sequence

from lxml import etree

from codify.pipeline.parsers.base import AmendmentAnnotation

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_SOURCE_ID = "codify-structured-parser"
# The XSD TextualMods enumeration has no generic change type;
# wording changes map to substitution.
_VALID_ACTIONS = {"insertion", "substitution", "repeal"}


def inject_passive_mods(akn_xml: str, amendments: Sequence[AmendmentAnnotation]) -> str:
    if not amendments:
        return akn_xml
    root = etree.fromstring(akn_xml.encode("utf-8"))
    meta = root.find(f".//{{{_AKN_NS}}}meta")
    if meta is None:
        return akn_xml
    work_el = meta.find(f".//{{{_AKN_NS}}}FRBRWork/{{{_AKN_NS}}}FRBRthis")
    work_uri = (work_el.get("value") or "") if work_el is not None else ""
    if not work_uri:
        return akn_xml

    analysis = meta.find(f"{{{_AKN_NS}}}analysis")
    if analysis is None:
        # Schema order: analysis precedes references within meta.
        analysis = etree.Element(f"{{{_AKN_NS}}}analysis")
        analysis.set("source", f"#{_SOURCE_ID}")
        refs_el = meta.find(f"{{{_AKN_NS}}}references")
        if refs_el is not None:
            refs_el.addprevious(analysis)
        else:
            meta.append(analysis)
    passive = analysis.find(f"{{{_AKN_NS}}}passiveModifications")
    if passive is None:
        passive = etree.SubElement(analysis, f"{{{_AKN_NS}}}passiveModifications")
    for stale in list(passive.findall(f"{{{_AKN_NS}}}textualMod")):
        if (stale.get("eId") or "").startswith(f"pmod_{_SOURCE_ID}_"):
            passive.remove(stale)

    # AKN 3.0 modificationType: <source> is the amending provision and
    # <destination> the amended unit of this work; both child elements.
    for i, note in enumerate(amendments, start=1):
        action = note.akn_action if note.akn_action in _VALID_ACTIONS else "substitution"
        mod = etree.SubElement(passive, f"{{{_AKN_NS}}}textualMod")
        mod.set("type", action)
        mod.set("eId", f"pmod_{_SOURCE_ID}_{i}")
        src = etree.SubElement(mod, f"{{{_AKN_NS}}}source")
        src.set("href", note.amender_href)
        dest = etree.SubElement(mod, f"{{{_AKN_NS}}}destination")
        dest.set("href", f"{work_uri}/main#{note.target_eid}")

    return str(etree.tostring(root, encoding="unicode"))
