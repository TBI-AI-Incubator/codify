"""Guarantee eId uniqueness in a serialised AKN document.

Publishers (notably legislation.gov.uk) reuse an eId across occurrences:
e.g. the same ``term-personal-data`` on every inline mention of a defined
term, or a definition repeated verbatim in a schedule. That is invalid AKN
and breaks downstream tools. The first occurrence keeps the eId; each later
duplicate gets a ``_dupN`` suffix. Reference attributes (``refersTo`` etc.)
still resolve to the canonical first occurrence, so link integrity holds.
"""

from __future__ import annotations

from lxml import etree

from codify.akn._schema import safe_parser


def ensure_unique_eids(xml: str | bytes, *, huge_tree: bool = False) -> tuple[str, int]:
    """Return (xml with unique eIds, number of eIds renamed)."""
    # Hardened parser: this runs on publisher-supplied XML and its output is
    # persisted as the canonical document, so entity resolution here would
    # outlive the request that carried it.
    tree = etree.ElementTree(
        etree.fromstring(
            xml.encode() if isinstance(xml, str) else xml,
            parser=safe_parser(huge_tree=huge_tree),
        )
    )
    # A declared entity survives the hardened parse as an unresolved reference,
    # and serialising drops the DOCTYPE that defined it, so the stored document
    # would carry a reference nothing can resolve. Refuse it instead: AKN from
    # the pipeline never carries a DOCTYPE, and `parse_akn` already rejects one.
    if tree.docinfo.internalDTD is not None or tree.docinfo.doctype:
        raise ValueError("AKN document declares a DOCTYPE; refusing to persist it")
    root = tree.getroot()
    carriers: list[tuple[etree._Element, str]] = []
    for el in root.iter():
        if isinstance(el.tag, str) and (eid := el.get("eId")):
            carriers.append((el, eid))
    # Every id the publisher wrote, wherever it sits in the document. A minted
    # name must never take one of them: with ids ``x, x, x_dup2`` the second
    # ``x`` must not become ``x_dup2``, or a reference to ``#x_dup2`` silently
    # changes which element it names while every id still looks unique.
    published = {eid for _, eid in carriers}
    seen: set[str] = set()
    renamed = 0
    for el, eid in carriers:
        if eid not in seen:
            seen.add(eid)
            continue
        n = 2
        candidate = f"{eid}_dup{n}"
        while candidate in seen or candidate in published:
            n += 1
            candidate = f"{eid}_dup{n}"
        el.set("eId", candidate)
        seen.add(candidate)
        renamed += 1
    return etree.tostring(root, encoding="unicode"), renamed
