"""Rename Bluebell proxy elements to <hcontainer> based on jurisdiction config.

Bluebell doesn't know about jurisdiction-specific containers (Tabsareh,
Proviso, Disposición transitoria, etc.) so it proxies them through
standard AKN elements (SUBSECTION → <subsection>, SECTION → <section>).
This post-processor reads the config's hcontainer declarations and renames
matching elements.
"""

from __future__ import annotations

from typing import cast

import structlog
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.akn._schema import parse_xml
from codify.jurisdictions import load_config

logger = structlog.get_logger()

# Map Bluebell proxy keywords to the AKN elements they emit.
_PROXY_TO_TAG = {
    "SUBSECTION": "subsection",
    "SECTION": "section",
    "PART": "part",
    "ARTICLE": "article",
    "PARAGRAPH": "paragraph",
    "CLAUSE": "clause",
}


def postprocess_hcontainers(akn_xml: str, country: str, doctype: str) -> str:
    """Rename proxy elements to <hcontainer name="..."> per jurisdiction config."""
    cfg = load_config(country)
    dc = cfg.get_document_class(doctype)
    if dc is None or not dc.hcontainers:
        return akn_xml

    to_rename = [hc for hc in dc.hcontainers if hc.requires_postprocessing]
    if not to_rename:
        return akn_xml

    root = parse_xml(akn_xml)
    renamed = 0

    for hc in to_rename:
        proxy_tag_local = _PROXY_TO_TAG.get(hc.bluebell_proxy)
        if proxy_tag_local is None:
            continue
        proxy_tag = f"{{{AKN_NS}}}{proxy_tag_local}"
        target_term = (hc.local_term or "").strip().lower()
        if not target_term:
            continue

        for el in root.iter(proxy_tag):
            heading = el.find("akn:heading", NS)
            if heading is None or not heading.text:
                continue
            if heading.text.strip().lower().startswith(target_term):
                el.tag = f"{{{AKN_NS}}}hcontainer"
                el.set("name", hc.name)
                renamed += 1

    if renamed > 0:
        logger.info("hcontainer_postprocessing", country=country, renamed=renamed)

    return cast(str, etree.tostring(root, pretty_print=True, encoding="unicode"))
