from __future__ import annotations

from codify.akn._emitter import emit_document
from codify.akn._parser import parse_document
from codify.akn._schema import validate_akn
from codify.akn.document import Document


def parse_akn(xml: str | bytes) -> Document:
    """Parse AKN-XML into a typed in-memory `Document`.

    Lenient: non-canonical hierarchical tags (`<part>`, `<book>`, `<hcontainer>`,
    `<item>`…) fold into the closest of the seven `BodyElement` kinds; the
    original tag survives on `akn_type` so `to_akn` can write it back unchanged.
    """
    return parse_document(xml)


def to_akn(doc: Document) -> str:
    """Serialise a `Document` to AKN-XML."""
    return emit_document(doc).decode("utf-8")


__all__ = ["parse_akn", "to_akn", "validate_akn"]
