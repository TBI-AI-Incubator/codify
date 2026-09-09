"""Inject <meta><references> from jurisdiction config core_tlcs.

Each TLCEntry becomes a child element of <references>, using the TLC class
as the element name (TLCOrganization, TLCRole, TLCObject, etc.) per AKN 3.0.
The config list is a vocabulary, not output: an entry is only emitted when
the document actually references its eId (``source="#codify"``,
``refersTo="#president"``, any ``#eid``-shaped attribute). Emitting the whole
catalogue put anachronistic institutions into every statute's references.
"""

from __future__ import annotations

from typing import cast

import structlog
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.akn._schema import parse_xml
from codify.jurisdictions import load_config

logger = structlog.get_logger()


def _referenced_eids(root: etree._Element) -> set[str]:
    """Every ``#eid`` an attribute anywhere in the document points at."""
    out: set[str] = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for value in el.attrib.values():
            # refersTo may hold a space-separated reference list.
            for token in value.split():
                if token.startswith("#") and len(token) > 1:
                    out.add(token[1:])
    return out


def emit_references(akn_xml: str, country: str) -> str:
    """Inject <references> into <meta> from jurisdiction config. Idempotent;
    only entries the document references by eId are emitted."""
    cfg = load_config(country)
    if not cfg.core_tlcs:
        return akn_xml

    root = parse_xml(akn_xml)
    meta = root.find(".//akn:meta", NS)
    if meta is None:
        return akn_xml

    referenced = _referenced_eids(root)
    wanted = [tlc for tlc in cfg.core_tlcs if tlc.eId in referenced]

    # Reconcile, not just add: a previously enriched document may carry
    # configured entries the document never references; strip them so
    # re-processing removes stale catalogue injections.
    configured_eids = {tlc.eId for tlc in cfg.core_tlcs}
    refs = meta.find("akn:references", NS)
    pruned = 0
    if refs is not None:
        for el in list(refs):
            eid = el.get("eId")
            if eid in configured_eids and eid not in referenced:
                refs.remove(el)
                pruned += 1
    if pruned:
        logger.info("references_pruned", country=country, count=pruned)

    if not wanted:
        if pruned:
            return cast(str, etree.tostring(root, pretty_print=True, encoding="unicode"))
        return akn_xml

    if refs is None:
        refs = etree.SubElement(meta, f"{{{AKN_NS}}}references", attrib={"source": "#codify"})

    existing_eids = {el.get("eId") for el in refs}
    added = 0

    for tlc in wanted:
        if tlc.eId in existing_eids:
            continue
        etree.SubElement(
            refs,
            f"{{{AKN_NS}}}{tlc.tlc_class}",
            attrib={
                "eId": tlc.eId,
                "href": tlc.href,
                "showAs": tlc.showAs,
            },
        )
        added += 1

    if added > 0:
        logger.info("references_injected", country=country, count=added)

    return cast(str, etree.tostring(root, pretty_print=True, encoding="unicode"))
