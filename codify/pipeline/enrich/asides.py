"""Attach a marginal article-subject label to the article it labels.

Older instruments print the article's subject in the margin (e.g. `تعاريف` beside
article 1). Read inline it becomes the first token of the provision. The classifier
already required band and type to agree before typing a block `aside`, so this pass
trusts the kind and only decides attachment: the leaked label, emitted as its own
body paragraph, is moved onto its article as a lifted `<authorialNote>`. It never
touches a full provision paragraph; a label embedded mid-sentence is left and
counted, the same recall the corroboration gate costs in `notes.py`.
"""

from __future__ import annotations

import structlog
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.pipeline.enrich.regions import OVERLAP_FLOOR, Region, overlap, words

logger = structlog.get_logger()

_UNIT = {"article", "section"}
# A label paragraph is at most this many words longer than the label itself; more
# and it is provision prose that merely opens with the label, which we never move.
_LABEL_SLACK = 2


def emit_marginal_notes(
    akn_xml: str,
    *,
    regions: dict[int, list[Region]] | None = None,
) -> str:
    asides = _aside_regions(regions)
    if not asides:
        return akn_xml

    root = etree.fromstring(akn_xml.encode("utf-8"))
    body = root.find(".//akn:body", NS)
    if body is None:
        return akn_xml

    attached = uncorroborated = 0
    for index, aside in enumerate(asides, start=1):
        paragraph = _label_paragraph(body, aside)
        if paragraph is None:
            uncorroborated += 1
            continue
        unit = _ancestor_unit(paragraph)
        host = _attach_host(unit, exclude=paragraph) if unit is not None else None
        parent = paragraph.getparent()
        if host is None or parent is None:
            uncorroborated += 1
            continue
        parent.remove(paragraph)
        host.append(_build_note(aside.text, eid=f"aside_{index}"))
        attached += 1

    logger.info("marginal_notes", attached=attached, uncorroborated=uncorroborated)
    rendered: str = etree.tostring(root, encoding="unicode")
    return rendered


def _aside_regions(regions: dict[int, list[Region]] | None) -> list[Region]:
    if not regions:
        return []
    return [r for page in regions.values() for r in page if r.kind == "aside"]


def _label_paragraph(body: etree._Element, aside: Region) -> etree._Element | None:
    """A short body `<p>` that IS this label, never a provision paragraph that
    merely opens with it (its extra words push the overlap below the floor)."""
    label_words = words(aside.text)
    if not label_words:
        return None
    for paragraph in body.findall(".//akn:p", NS):
        text = "".join(paragraph.itertext()).strip()
        if not text:
            continue
        if (
            overlap(text, aside.text) >= OVERLAP_FLOOR
            and len(words(text)) <= len(label_words) + _LABEL_SLACK
        ):
            return paragraph
    return None


def _ancestor_unit(element: etree._Element) -> etree._Element | None:
    node = element.getparent()
    while node is not None:
        if etree.QName(node).localname in _UNIT:
            return node
        node = node.getparent()
    return None


def _attach_host(unit: etree._Element, *, exclude: etree._Element) -> etree._Element | None:
    """An inline host that accepts an `<authorialNote>`: the article heading if it
    has one, else its first content paragraph, never the label being moved."""
    heading = unit.find("akn:heading", NS)
    if heading is not None and heading is not exclude:
        return heading
    for paragraph in unit.findall(".//akn:p", NS):
        if paragraph is not exclude:
            return paragraph
    return None


def _build_note(text: str, *, eid: str) -> etree._Element:
    """`<authorialNote>` needs a placement and a block child to pass the schema.

    `placement="bottom"` is the load-bearing choice, not a cosmetic one: it is the
    single discriminator every projection uses to keep a lifted note out of the
    provision text (`codify.akn.vocabulary.is_lifted_note`). Any other placement
    would read the label back as part of the sentence, the contamination this pass
    removes.
    """
    note = etree.Element(f"{{{AKN_NS}}}authorialNote")
    note.set("eId", eid)
    note.set("placement", "bottom")
    inner = etree.SubElement(note, f"{{{AKN_NS}}}p")
    inner.text = text
    return note


__all__ = ["emit_marginal_notes"]
