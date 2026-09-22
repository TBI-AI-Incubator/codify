"""Every closing-phrase consumer reads a phrase split across a line break alike."""

from __future__ import annotations

from lxml import etree

from codify.pipeline.enrich.closing import mentions_closing_phrase
from codify.pipeline.enrich.conclusions import _attestation_start, _is_direct_signature

AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
PHRASES = ("Sealed by the Harbour Clerk",)


def _block(tag: str, lines: list[str]) -> etree._Element:
    el = etree.Element(f"{{{AKN}}}{tag}")
    for line in lines:
        etree.SubElement(el, f"{{{AKN}}}p").text = line
    return el


def test_split_phrase_is_mentioned() -> None:
    assert mentions_closing_phrase("Sealed by the\nHarbour Clerk", PHRASES)
    assert mentions_closing_phrase("Sealed by the\r\n  Harbour Clerk", PHRASES)


def test_two_line_breaks_are_not_one_phrase() -> None:
    assert not mentions_closing_phrase("Sealed by the\n\nHarbour Clerk", PHRASES)


def test_blank_phrase_mentions_nothing() -> None:
    assert not mentions_closing_phrase("anything", ("  ",))


def test_attestation_found_with_a_split_phrase() -> None:
    body = etree.Element(f"{{{AKN}}}body")
    body.append(_block("content", ["Sealed by the\nHarbour Clerk", "Port Town", "Warden"]))
    container, paragraph = _attestation_start(body, PHRASES)
    assert container is not None and paragraph is not None
    assert paragraph.text == "Sealed by the\nHarbour Clerk"


def test_signature_block_found_with_a_split_phrase() -> None:
    conclusions = _block(
        "conclusions", ["Sealed by the\nHarbour Clerk", "Warden", "Keeper of Lights"]
    )
    assert _is_direct_signature(conclusions, PHRASES)
