"""Split a source's opening material and tag the enacting formula it carries.

Codify never adds a formula the source does not print."""

from __future__ import annotations

from typing import cast

import structlog
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.jurisdictions import OPEN_STRUCTURE_ROOTS, load_config
from codify.pipeline.enrich.anchors import cached_regex
from codify.pipeline.enrich.arabic_normalise import (
    drop_header_only_lines,
    fold_arabic_for_match,
)

logger = structlog.get_logger()


def mark_source_formula(akn_xml: str, country: str) -> str:
    """In a `doc` or `statement`, the preamble line a terminator closes becomes the
    source's own enacting formula, and each line before it a recital, so preamble,
    formula and operative body stay apart and the recitals' citations are rows."""
    terminators = load_config(country).opening_material_terminators
    root = etree.fromstring(akn_xml.encode("utf-8"))
    main = next((c for c in root if isinstance(c.tag, str)), None)
    if main is None or etree.QName(main).localname not in OPEN_STRUCTURE_ROOTS:
        return akn_xml
    preamble = main.find("akn:preamble", NS)
    if preamble is None or preamble.find("akn:recitals", NS) is not None:
        return akn_xml
    lines = preamble.findall("akn:p", NS)
    formula = preamble.find("akn:formula", NS)
    folded = [fold_arabic_for_match(t) for t in terminators if t.strip()]
    closing = next(
        (
            p
            for p in reversed(lines)
            if any(t in fold_arabic_for_match("".join(p.itertext())) for t in folded)
        ),
        None,
    )
    if formula is None and closing is not None:
        formula = etree.Element(f"{{{AKN_NS}}}formula", name="enactingFormula")
        closing.addprevious(formula)
        closing.attrib.pop("eId", None)
        formula.append(closing)
    # Every block before the formula, in source order; none after it, so a rerun wraps none.
    blocks = formula.itersiblings(preceding=True) if formula is not None else reversed(preamble)
    recited = [b for b in blocks if isinstance(b.tag, str)][::-1]
    if recited:
        recitals = etree.Element(f"{{{AKN_NS}}}recitals", eId="recitals")
        recited[0].addprevious(recitals)
        for index, line in enumerate(recited, start=1):
            line.attrib.pop("eId", None)
            etree.SubElement(recitals, f"{{{AKN_NS}}}recital", eId=f"recitals__rec_{index}").append(
                line
            )
    return cast(str, etree.tostring(root, pretty_print=True, encoding="unicode"))


# Generic Arabic preamble openers: citation clauses (having reviewed /
# pursuant to / based upon) that begin recital chains across legal eras.
# Config enacting_formula_markers extend this per jurisdiction.
_PREAMBLE_OPENERS = (
    "بعد الاطلاع",
    "استنادا",
    "وبناء على",
    "بناء على",
    "باسم الشعب",
)
_FOLDED_PREAMBLE_OPENERS = tuple(fold_arabic_for_match(o) for o in _PREAMBLE_OPENERS)


def split_opening_material(text: str, country: str) -> tuple[str | None, str | None]:
    """Split pre-first-anchor text into (preface, preamble). The preamble starts at the first
    line carrying a citation opener or a config enacting-formula marker; everything before
    it is preface, and no opener means all preface.

    Lines that are wholly gazette furniture by the jurisdiction's
    ``furniture_line_patterns`` are dropped first, so they never become preface or
    preamble paragraphs: carrying them invites the translator to write legal-sounding text
    over a page footer, and one it declines is one the front-matter contract must recover.
    """
    stripped = text.strip()
    if not stripped:
        return None, None
    cfg = load_config(country)
    if cfg.furniture_line_patterns:
        cleaned, dropped = drop_header_only_lines(stripped, cfg.furniture_line_patterns)
        if dropped:
            logger.info("opening_material_furniture_dropped", country=country, lines=dropped)
        stripped = cleaned.strip()
        if not stripped:
            return None, None
    formula_markers = [fold_arabic_for_match(m) for m in cfg.enacting_formula_markers]
    markers = formula_markers + [fold_arabic_for_match(m) for m in _PREAMBLE_OPENERS]
    terminators = [
        fold_arabic_for_match(m) for m in (cfg.opening_material_terminators if cfg else [])
    ]
    lines = _truncate_after_enacting_formula(stripped.splitlines(), terminators, country)
    for i, line in enumerate(lines):
        folded = fold_arabic_for_match(line)
        if folded and any(m in folded for m in markers):
            preface = "\n".join(lines[:i]).strip() or None
            preamble = "\n".join(lines[i:]).strip() or None
            return preface, preamble
    return "\n".join(lines).strip() or None, None


def _truncate_after_enacting_formula(
    lines: list[str], terminators: list[str], country: str
) -> list[str]:
    """Drop everything after the phrase closing the opening material; what follows is the
    next scan page bleeding in.

    Terminators rather than `enacting_formula_markers`, because the latter is evidence a
    source carries its own formula, and a phrase that opens an instrument is evidence too:
    cutting there deletes the recitals after it. No terminator means no cut, and a tail
    carrying real content is kept and warned about rather than deleted.
    """
    if not terminators:
        return lines
    # First, not last: a bled-in page repeats the formula with its masthead.
    first = next(
        (
            i
            for i, line in enumerate(lines)
            if (folded := fold_arabic_for_match(line)) and any(m in folded for m in terminators)
        ),
        -1,
    )
    if first < 0 or first == len(lines) - 1:
        return lines
    tail = lines[first + 1 :]
    # Content after the formula means the cut point is wrong, not the tail.
    # The jurisdiction's own anchor regex, not Bluebell keywords: this runs on
    # source OCR, where an Arabic article opens `مادة (١)`, not `ARTICLE 1`.
    anchors = cached_regex(country, "act")
    kept = [
        line
        for line in tail
        if anchors.search(line)
        or any(op in fold_arabic_for_match(line) for op in _FOLDED_PREAMBLE_OPENERS)
    ]
    if kept:
        logger.warning(
            "opening_material_truncation_declined",
            reason="tail_carries_content",
            lines=[line.strip()[:60] for line in kept[:3]],
        )
        return lines
    logger.info(
        "opening_material_truncated_at_formula",
        dropped=len(tail),
        text=[line.strip()[:60] for line in tail[:6]],
    )
    return lines[: first + 1]
