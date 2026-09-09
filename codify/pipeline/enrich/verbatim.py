"""No-LLM body fill: splice raw source text into an anchor scaffold verbatim.

For sources whose text is already clean (official plain-text or HTML-derived
bodies), the inter-anchor content *is* the body, transcription adds nothing
but cost and paraphrase risk. This walks the scanned anchors in order,
assigns each anchor the text up to the next anchor, and reuses the standard
scaffold assembly.
"""

from __future__ import annotations

from codify.pipeline.enrich.anchors import (
    StructuralAnchor,
    anchor_summary,
    cached_regex,
    scan_anchors,
)
from codify.pipeline.enrich.scaffold import (
    BodyBlock,
    BodyFillResponse,
    assemble_filled_scaffold,
    scaffold_from_anchors,
)


def fill_bodies_verbatim(text: str, anchors: list[StructuralAnchor]) -> BodyFillResponse:
    """One BodyBlock per anchor: heading from the anchor line's remainder,
    body lines from the text up to the next anchor."""
    ordered = sorted(anchors, key=lambda a: a.char_offset)
    blocks: list[BodyBlock] = []
    for i, anchor in enumerate(ordered):
        end = ordered[i + 1].char_offset if i + 1 < len(ordered) else len(text)
        chunk = text[anchor.char_offset : end].lstrip()
        first_line, _, rest = chunk.partition("\n")
        heading = first_line[len(anchor.matched_text.lstrip()) :].strip(" .-—:") or None
        lines = [ln.strip() for ln in rest.splitlines() if ln.strip()]
        # A heading captured from the next source line (bare-keyword annex)
        # must not repeat as the first body paragraph.
        if heading is None and anchor.heading and lines and lines[0] == anchor.heading:
            heading = anchor.heading
            lines = lines[1:]
        blocks.append(BodyBlock(eid=anchor.akn_eid, heading=heading, lines=lines))
    return BodyFillResponse(bodies=blocks)


def text_to_bluebell_verbatim(
    text: str,
    *,
    country: str,
    doctype: str = "act",
    scan_text: str | None = None,
) -> tuple[str, dict[str, int], dict[str, StructuralAnchor]]:
    """scan → scaffold → verbatim fill → assemble. No LLM.

    ``scan_text``, when given, is scanned instead of ``text``, it must be
    offset-aligned (same length) and lets callers blank spans where
    structural mentions are prose (editorial notes, mid-paragraph
    references) so the scan sees only genuine headings. Bodies always fill
    from ``text``. Returns (bluebell, anchor_summary, eid_to_anchor).
    """
    if scan_text is not None and len(scan_text) != len(text):
        raise ValueError("scan_text must be offset-aligned with text")
    regex = cached_regex(country, doctype)
    anchors = scan_anchors(
        scan_text if scan_text is not None else text,
        regex,
        country=country,
        doctype=doctype,
    )
    if not anchors:
        raise ValueError("no structural anchors found — use the LLM structuring lane")

    from codify.pipeline.enrich.enacting import split_opening_material

    preface, preamble = split_opening_material(text[: min(a.char_offset for a in anchors)], country)
    scaffold, eid_to_anchor = scaffold_from_anchors(
        anchors, preface=preface, preamble=preamble, country=country
    )
    response = fill_bodies_verbatim(text, anchors)
    bluebell = assemble_filled_scaffold(scaffold, eid_to_anchor, response)

    return bluebell, anchor_summary(anchors), eid_to_anchor
