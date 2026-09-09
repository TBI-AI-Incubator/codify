"""Lift the promulgation and signature out of the final provision."""

from __future__ import annotations

import re

import structlog
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.pipeline.enrich.regions import Region, RegionVocabulary

logger = structlog.get_logger()


def emit_conclusions(
    akn_xml: str,
    *,
    vocab: RegionVocabulary,
    regions: dict[int, list[Region]] | None = None,
) -> str:
    """Move attestation paragraphs from the last provision into `<conclusions>`.

    The closing phrase locates it and being in the document's final container
    corroborates it. Whether a page region typed `signature` or `conclusions`
    agrees is only recorded as telemetry, it does not gate the lift.
    """
    if not vocab.closing_phrases:
        return akn_xml

    root = etree.fromstring(akn_xml.encode("utf-8"))
    act = _act(root)
    if act is None or act.find("akn:conclusions", NS) is not None:
        return akn_xml

    container, start = find_displaced_attestation(root, vocab.closing_phrases)
    if container is None or start is None:
        return akn_xml

    moved = lift_attestation(root, container, start)
    if not moved:
        return akn_xml

    logger.info(
        "conclusions_lifted",
        paragraphs=moved,
        corroborated_by_region=_region_agrees(regions),
    )
    rendered: str = etree.tostring(root, encoding="unicode")
    return rendered


def find_displaced_attestation(
    root: etree._Element, closing_phrases: tuple[str, ...] | list[str]
) -> tuple[etree._Element | None, etree._Element | None]:
    """Attestation paragraphs still sitting inside the last body provision:
    the container and the first attestation `<p>`, or (None, None). Read-only:
    shared by the ingest pass, the validator check and the repair op."""
    if not closing_phrases:
        return None, None
    act = _act(root)
    body = act.find("akn:body", NS) if act is not None else None
    if body is None:
        return None, None
    return _attestation_start(body, tuple(closing_phrases))


def lift_attestation(root: etree._Element, container: etree._Element, start: etree._Element) -> int:
    """Move `start` and everything after it into `<conclusions>` (created after
    the body if absent), regroup the signatory, prune emptied wrappers. Returns
    the number of paragraphs moved."""
    act = _act(root)
    body = act.find("akn:body", NS) if act is not None else None
    if body is None:
        return 0
    paragraphs = container.findall("akn:p", NS)
    moved = paragraphs[paragraphs.index(start) :]
    if not moved:
        return 0
    conclusions = act.find("akn:conclusions", NS) if act is not None else None
    created = conclusions is None
    if conclusions is None:
        conclusions = etree.Element(f"{{{AKN_NS}}}conclusions")
    # Place and date are prose, so they are direct `<p>`s: `<formula>` accepts
    # only enactingFormula and promulgation as its name.
    for element in moved:
        _keep_tail(element)
        container.remove(element)
        conclusions.append(element)
    _regroup_signatory(conclusions)
    if created:
        body.addnext(conclusions)
    _prune_empty(container)
    return len(moved)


def _act(root: etree._Element) -> etree._Element | None:
    if etree.QName(root).localname == "act":
        return root
    return root.find("akn:act", NS)


def _attestation_start(
    body: etree._Element, closing_phrases: tuple[str, ...]
) -> tuple[etree._Element | None, etree._Element | None]:
    """The container and first paragraph of the attestation, or (None, None)."""
    containers = [el for el in body.iter() if el.findall("akn:p", NS)]
    if not containers:
        return None, None
    # Only the final container: a closing phrase quoted mid-document is prose.
    last = containers[-1]
    paragraphs = last.findall("akn:p", NS)
    for index, paragraph in enumerate(paragraphs):
        text = "".join(paragraph.itertext())
        if not any(phrase in text for phrase in closing_phrases):
            continue
        # It and everything after it has to look like attestation. A closing
        # phrase inside a provision that keeps going is prose, not attestation.
        if all(_is_attestation_line(p) for p in paragraphs[index:]):
            return last, paragraph
        return None, None
    return None, None


# Place, date, name and role are short lines. A sentence of obligations is not.
_ATTESTATION_MAX_WORDS = 14
_HAS_DIGIT = re.compile(r"[\d٠-٩۰-۹]")


def _is_attestation_line(paragraph: etree._Element) -> bool:
    return len("".join(paragraph.itertext()).split()) <= _ATTESTATION_MAX_WORDS


def _keep_tail(element: etree._Element) -> None:
    """`remove()` drops the tail, which is bare text belonging to the container."""
    tail = element.tail
    if not tail or not tail.strip():
        return
    previous = element.getprevious()
    if previous is not None:
        previous.tail = (previous.tail or "") + tail
    else:
        parent = element.getparent()
        if parent is not None:
            parent.text = (parent.text or "") + tail
    element.tail = None


def _regroup_signatory(conclusions: etree._Element) -> None:
    """Name and role become a `<blockContainer>`, matching the EU lane.

    `<signature>` is reserved for the inline name-block, so the block-level
    grouping is a container per AKN4EU Vol II.
    """
    paragraphs = conclusions.findall("akn:p", NS)
    if len(paragraphs) < 3:
        return
    # Neither the first pair nor the last: the place line and the second calendar
    # both carry numerals, and a stray line swept in behind the signature may too.
    # A name and a role do not, so the signatory is the last digit-free pair.
    signatory = [p for p in paragraphs if not _HAS_DIGIT.search("".join(p.itertext()))][-2:]
    if len(signatory) < 2:
        return
    block = etree.Element(f"{{{AKN_NS}}}blockContainer")
    block.set("eId", "sig_1")
    # In place, so anything swept in behind the signature keeps its reading order.
    signatory[0].addprevious(block)
    for paragraph in signatory:
        conclusions.remove(paragraph)
        block.append(paragraph)


def _prune_empty(container: etree._Element) -> None:
    """Drop a wrapper the move emptied, so no bare `<content/>` is left."""
    parent = container.getparent()
    while parent is not None and etree.QName(container).localname != "body":
        if _carries_content(container):
            return
        _keep_tail(container)
        parent.remove(container)
        container, parent = parent, parent.getparent()


def _carries_content(container: etree._Element) -> bool:
    """A `<num>` alone is not content. Bare text and any other child are."""
    if (container.text or "").strip():
        return True
    for element in container:
        if etree.QName(element).localname != "num" or (element.tail or "").strip():
            return True
    return False


def _region_agrees(regions: dict[int, list[Region]] | None) -> bool:
    """Whether the layout typed a `conclusions` region. Telemetry only, logged
    alongside the lift, never a condition on it."""
    if not regions:
        return False
    return any(r.kind == "conclusions" for page in regions.values() for r in page)


__all__ = ["emit_conclusions", "find_displaced_attestation", "lift_attestation"]
