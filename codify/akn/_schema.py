from __future__ import annotations

from cobalt.schemas import get_schema, validate_xml
from lxml import etree

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
NSMAP = {None: AKN_NS}
NS = {"akn": AKN_NS}


def safe_parser() -> etree.XMLParser:
    """XML parser hardened against XXE / entity-expansion / network attacks.

    Every path that reads publisher- or reader-supplied AKN goes through this
    or `parse_xml`. Prefer `parse_xml`: it adds the DOCTYPE refusal, and this
    on its own accepts a DOCTYPE-bearing document (inertly, but it accepts it).
    """
    return etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)


def parse_xml(xml: str | bytes) -> etree._Element:
    """Parse document XML with a hardened parser, and refuse a DOCTYPE.

    What actually stops an entity being resolved is `safe_parser`: no entity
    resolution, no DTD load, no network. This check does not replace that and
    could not: it runs after `fromstring` returns, so if the parser ever did
    resolve, the damage would already be done.

    What it adds is that the document is refused on policy rather than tolerated
    because the current libxml2 happens to object. Before this, one format path
    rejected a DOCTYPE (`ensure_unique_eids`) and the other did not, so whether
    a DOCTYPE-bearing document got anywhere depended on which path it took and
    on a pinned dependency's behaviour. Neither is a property of this project.

    Raises `XMLSyntaxError`, not `ValueError`: twenty-two callers already treat
    that as "this XML is unusable" and degrade accordingly, and a DOCTYPE is
    that same answer. A new exception type would have turned six documented
    graceful degradations into escaping errors.

    It costs nothing measurable: no document the pipeline emits carries a
    DOCTYPE, no XML fixture in the repository declares one, and EUR-Lex serves
    its XML without one.
    """
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    root = etree.fromstring(xml, parser=safe_parser())
    info = root.getroottree().docinfo
    if info.internalDTD is not None or info.doctype:
        raise etree.XMLSyntaxError("XML declares a DOCTYPE; refusing to parse it", None, 0, 0)
    return root


def akn_schema(strict: bool = False) -> etree.XMLSchema:
    return get_schema(AKN_NS, strict=strict)


def validate_akn(xml: str | bytes | etree._Element, strict: bool = False) -> None:
    """Validate AKN-XML against the OASIS schema. Raises DocumentInvalid on failure."""
    root = xml if isinstance(xml, etree._Element) else parse_xml(xml)
    valid, errors = validate_xml(root, akn_schema(strict))
    if not valid:
        raise etree.DocumentInvalid("\n".join(str(e) for e in errors))
