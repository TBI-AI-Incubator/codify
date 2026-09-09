"""Lift amendment footnotes out of provision text into `<authorialNote>`."""

from __future__ import annotations

import re

import structlog
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.pipeline.enrich.regions import OVERLAP_FLOOR, Region, RegionVocabulary, overlap

logger = structlog.get_logger()

_SUPERSCRIPT_DIGITS = "¹²³⁴⁵⁶⁷⁸⁹⁰"
_SUPERSCRIPT_TO_ASCII = str.maketrans(_SUPERSCRIPT_DIGITS, "1234567890")
_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_ARABIC_TO_ASCII = str.maketrans(_ARABIC_INDIC, "0123456789")

_DIGITS = rf"\d{_ARABIC_INDIC}"
# The endpoint returns a superscript either as the Unicode character or as the
# LaTeX form, on the same page, so both are the same marker.
_RAISED = rf"(?:[{_SUPERSCRIPT_DIGITS}]+|\$\^\{{?[{_DIGITS}]+\}}?\$)"
_OPENING_MARKER = re.compile(rf"^\s*(?:(?P<sup>{_RAISED})|[(（]\s*(?P<paren>[{_DIGITS}]+)\s*[)）])")
# Inline, only the raised forms. A bare `(1)` mid-provision is subsection
# numbering far more often than it is a reference to a footnote.
_INLINE_MARKER = re.compile(_RAISED)
# The rule a scan prints above the footnote block, read back as leading dashes.
_SEPARATOR = re.compile(r"^[\s_\-–—]{4,}")


def emit_authorial_notes(
    akn_xml: str,
    *,
    vocab: RegionVocabulary,
    regions: dict[int, list[Region]] | None = None,
) -> str:
    """Move a footnote onto the provision its marker refers to.

    Each candidate must match a `references` region's own text. A leading `(1)`
    is the commonest opener in legislative prose, so the marker alone would lift
    ordinary numbered subsections out of the body.
    """
    footnotes = _footnote_regions(regions)
    if not footnotes:
        return akn_xml

    root = etree.fromstring(akn_xml.encode("utf-8"))
    body = root.find(".//akn:body", NS)
    if body is None:
        return akn_xml

    candidates, uncorroborated = _candidates(body, vocab, footnotes)
    if not candidates:
        logger.info("authorial_notes_none", uncorroborated=uncorroborated)
        return akn_xml

    # `claimed` is taken before anything moves: appending a note into a paragraph
    # that is itself a later candidate would merge the two and lose the first.
    claimed = {paragraph for paragraph, _ in candidates}
    lifted = inferred = 0
    for index, (paragraph, marker) in enumerate(candidates, start=1):
        target, exact = _target_for(body, paragraph, marker, claimed)
        parent = paragraph.getparent()
        if target is None or parent is None:
            continue
        # Built only once it has somewhere to land: it empties the paragraph.
        note = _build_note(paragraph, eid=f"fn_{index}")
        parent.remove(paragraph)
        target.append(note)
        lifted += 1
        if not exact:
            note.set("refersTo", "#inferred-binding")
            inferred += 1

    # `uncorroborated` is the recall the gate costs: a marker-shaped paragraph
    # the layout did not type `references`, left in the body rather than guessed.
    logger.info(
        "authorial_notes_lifted",
        lifted=lifted,
        inferred=inferred,
        uncorroborated=uncorroborated,
    )
    rendered: str = etree.tostring(root, encoding="unicode")
    return rendered


def _footnote_regions(regions: dict[int, list[Region]] | None) -> list[Region]:
    if not regions:
        return []
    return [r for page in regions.values() for r in page if r.kind == "footnote"]


def _matching_region(text: str, footnotes: list[Region]) -> bool:
    """Whether a footnote region carries most of this paragraph's words.

    The two engines read the same page separately, so the strings differ; word
    overlap survives that where an exact match would not.
    """
    return any(overlap(text, region.text) >= OVERLAP_FLOOR for region in footnotes)


def _candidates(
    body: etree._Element, vocab: RegionVocabulary, footnotes: list[Region]
) -> tuple[list[tuple[etree._Element, str]], int]:
    """Paragraphs opening with a marker that a footnote region corroborates.

    The count returned alongside is the ones the region check rejected, which is
    what the corroboration gate costs in recall.
    """
    found: list[tuple[etree._Element, str]] = []
    rejected = 0
    for paragraph in body.findall(".//akn:p", NS):
        text = _SEPARATOR.sub("", "".join(paragraph.itertext()).strip())
        match = _OPENING_MARKER.match(text)
        if match is None or not any(p.search(text) for p in vocab.footnote_markers):
            continue
        if not _matching_region(text, footnotes):
            rejected += 1
            continue
        raw = match.group("sup") or match.group("paren") or ""
        found.append((paragraph, _normalise(raw)))
    return found, rejected


def _normalise(marker: str) -> str:
    """The digits of a marker, whatever form it was raised in."""
    plain = marker.translate(_SUPERSCRIPT_TO_ASCII).translate(_ARABIC_TO_ASCII)
    return "".join(ch for ch in plain if ch.isdigit())


def _target_for(
    body: etree._Element,
    note: etree._Element,
    marker: str,
    claimed: set[etree._Element],
) -> tuple[etree._Element | None, bool]:
    """The nearest preceding paragraph carrying the same marker inline.

    Nearest rather than first, because markers restart per page in a scan, so the
    same number recurs and the first match in document order is the wrong one.
    """
    nearest: etree._Element | None = None
    for paragraph in body.findall(".//akn:p", NS):
        if paragraph is note:
            break
        if paragraph in claimed:
            continue
        text = "".join(paragraph.itertext())
        if any(_normalise(m) == marker for m in _INLINE_MARKER.findall(text)):
            nearest = paragraph
    if nearest is not None:
        return nearest, True
    # The marker did not survive the read. Bind at the nearest sibling and record
    # it as inferred; never the container, which cannot hold an inline note.
    parent = note.getparent()
    if parent is None:
        return None, False
    siblings = [p for p in parent.findall("akn:p", NS) if p is not note and p not in claimed]
    return (siblings[-1] if siblings else None), False


def _build_note(paragraph: etree._Element, *, eid: str) -> etree._Element:
    """`<authorialNote>` needs a placement and a block child to pass the schema."""
    note = etree.Element(f"{{{AKN_NS}}}authorialNote")
    note.set("eId", eid)
    note.set("placement", "bottom")
    inner = etree.SubElement(note, f"{{{AKN_NS}}}p")
    # Move the children rather than flatten: the Bluebell parse produced the
    # paragraph's inline markup, and a footnote is where the amendment
    # citations are.
    inner.text = _SEPARATOR.sub("", paragraph.text or "")
    for child in list(paragraph):
        inner.append(child)
    return note


__all__ = ["emit_authorial_notes"]
