"""Measure furniture leaking into `<body>`, the leak metric behind the structurer.

A `references` (footnote) or `aside` (marginal label) region whose text still
appears in the body's provision prose is furniture that reached the reader. The
count is over regions, not paragraphs, so a note nested in a paragraph is not
counted twice; and it reads the body through `provision_text`, so a lifted note
(`placement="bottom"`) is excluded while an inline note stays, matching the
`is_lifted_note` contract. Reuses the same word overlap the structure filter and
the enrich passes use, so the metric cannot drift from the mechanism. Target 0
per run; the body-fill is an LLM, so this is reported, not asserted hard in CI.
"""

from __future__ import annotations

from lxml import etree

from codify.akn.vocabulary import provision_text
from codify.pipeline.enrich.regions import OVERLAP_FLOOR, Region, overlap

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
NS = {"akn": AKN_NS}


def furniture_in_body(akn_xml: str, regions: dict[int, list[Region]]) -> dict[str, int]:
    """How many `footnote` and `aside` regions still read as provision text."""
    footnotes = [r for page in regions.values() for r in page if r.kind == "footnote"]
    asides = [r for page in regions.values() for r in page if r.kind == "aside"]
    counts = {"references": 0, "aside_text": 0}

    root = etree.fromstring(akn_xml.encode("utf-8"))
    body = root.find(".//akn:body", NS)
    if body is None:
        return counts

    # The body as a reader of the provisions reads it: lifted notes out, inline in.
    body_text = provision_text(body)
    counts["references"] = sum(1 for r in footnotes if overlap(r.text, body_text) >= OVERLAP_FLOOR)
    counts["aside_text"] = sum(1 for r in asides if overlap(r.text, body_text) >= OVERLAP_FLOOR)
    return counts


__all__ = ["furniture_in_body"]
