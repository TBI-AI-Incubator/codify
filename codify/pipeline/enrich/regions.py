"""Classify a page's OCR blocks, so provision text can contain only provisions. Two of
three signals agreeing is a decision; fewer is a flag, never a guess. Regions decide
and never supply text, the layout and the authoritative text coming from different
engines, so block offsets do not transfer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from codify.jurisdictions import JurisdictionConfig, load_config
from codify.pipeline.enrich.closing import mentions_closing_phrase
from codify.pipeline.enrich.ocr import OcrBlock, PageDimensions, PageLayout

RegionKind = Literal["body", "furniture", "toc", "footnote", "conclusions", "aside", "unknown"]

# All thirteen documented types are mapped, not only the nine observed.
_BODY_TYPES = frozenset({"text", "list", "table", "equation", "code", "caption", "title"})
_DROPPED_TYPES = frozenset({"image"})

# Measured: 16 of 16 footnote blocks start at or below 0.82, no body block does.
FOOTNOTE_BAND = 0.80

# A marginal label is a narrow column, not the full text measure, so band and type must
# agree: an `aside_text` block wider than this fraction of the page reads as body and is
# flagged. Tuned against real aside blocks; widen it if another era's margin spans wider.
ASIDE_MAX_WIDTH = 0.5

# Both forms, or an era is silently dropped: modern superscript, older numeral.
_SUPERSCRIPT = re.compile(r"^\s*(?:[¹²³⁰-⁹]+|\$\^\{?\d+\}?\$)")
_PAREN_NUMERAL = re.compile(r"^\s*[(（]\s*[\d٠-٩۰-۹]+\s*[)）]")
DEFAULT_FOOTNOTE_MARKERS: tuple[re.Pattern[str], ...] = (_SUPERSCRIPT, _PAREN_NUMERAL)

# The endpoint writes a structural heading as markdown, so a `header` with one lies.
_MARKDOWN_HEADING = re.compile(r"^\s*#{1,6}\s")


@dataclass(frozen=True)
class RegionVocabulary:
    """Jurisdiction phrasing, resolved for one era before it gets here."""

    closing_phrases: tuple[str, ...] = ()
    footnote_markers: tuple[re.Pattern[str], ...] = DEFAULT_FOOTNOTE_MARKERS
    heading_markers: tuple[re.Pattern[str], ...] = (_MARKDOWN_HEADING,)


def vocabulary_for(config: JurisdictionConfig | None, era: str) -> RegionVocabulary:
    """An era's own lists win; the jurisdiction's are the fallback."""
    if config is None:
        return RegionVocabulary()
    closing = list(config.closing_phrases)
    markers = list(config.footnote_markers)
    for declared in config.legal_eras:
        if declared.id != era:
            continue
        closing = list(declared.closing_phrases) or closing
        markers = list(declared.footnote_markers) or markers
        break
    return RegionVocabulary(
        closing_phrases=tuple(closing),
        # Config extends the documented forms; it never replaces them.
        footnote_markers=DEFAULT_FOOTNOTE_MARKERS + tuple(_compile(m) for m in markers),
    )


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.UNICODE)


# Region-to-text matching by word overlap, since the layout blocks and the
# authoritative markdown come from two engines: offsets do not transfer and exact
# strings differ. The one definition of whether text belongs to a region, shared by the
# footnote lift, the structure-text filter and the aside pass so they cannot drift.
_WORD = re.compile(r"\w+", re.UNICODE)
OVERLAP_FLOOR = 0.6


def words(text: str) -> set[str]:
    return set(_WORD.findall(text))


def overlap(text: str, other: str) -> float:
    """Fraction of `text`'s words that also appear in `other` (0.0 when empty)."""
    w = words(text)
    return len(w & words(other)) / len(w) if w else 0.0


def vocabulary_for_jurisdiction(jurisdiction_code: str, year: str) -> RegionVocabulary:
    """Phrasing for the era the work belongs to, so modern wording is not applied
    to an older one where it would be a guess."""
    from codify.quality.structural_scan import era_of

    config = load_config(jurisdiction_code)
    return vocabulary_for(config, era_of(config, int(year) if year.isdigit() else None))


def classify_layouts(
    layouts: Mapping[int, PageLayout], *, vocab: RegionVocabulary | None = None
) -> dict[int, list[Region]]:
    """Classify whole pages, keyed by page number. Both ingest lanes come here."""
    return {
        page_number: classify_page(
            layout.blocks, layout.dimensions, page_number=page_number, vocab=vocab
        )
        for page_number, layout in layouts.items()
    }


@dataclass(frozen=True)
class Region:
    """One block, classified, carrying the evidence for the call."""

    kind: RegionKind
    page_number: int
    block_index: int
    block_type: str
    text: str
    top: int
    bottom: int
    signals: tuple[str, ...] = ()
    # Signals disagreed: the kind is the safe reading, a reviewer settles it.
    flagged: bool = False
    detail: str = ""


@dataclass
class _Signals:
    names: list[str] = field(default_factory=list)

    def add(self, name: str, *, when: bool) -> bool:
        if when:
            self.names.append(name)
        return when


def _band(block: OcrBlock, height: int) -> float:
    """Where the block starts as a fraction of page height, 0.0 when unknown."""
    return block.top_left_y / height if height > 0 else 0.0


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(text) for p in patterns)


def classify_page(
    blocks: list[OcrBlock],
    dimensions: PageDimensions | None,
    *,
    page_number: int,
    vocab: RegionVocabulary | None = None,
) -> list[Region]:
    """Classify every block on one page. Pure; blocks arrive in reading order."""
    vocab = vocab or RegionVocabulary()
    height = dimensions.height if dimensions is not None else 0
    width = dimensions.width if dimensions is not None else 0
    last_heading = _last_heading_index(blocks)
    regions: list[Region] = []
    # Attestation runs to the page foot: name and role follow as their own blocks.
    in_conclusions = False

    for index, block in enumerate(blocks):
        kind, signals, flagged, detail = _classify_block(
            block,
            index=index,
            height=height,
            width=width,
            last_heading=last_heading,
            vocab=vocab,
            in_conclusions=in_conclusions,
        )
        if kind == "conclusions":
            in_conclusions = True
        elif block.type == "title":
            in_conclusions = False
        regions.append(
            Region(
                kind=kind,
                page_number=page_number,
                block_index=index,
                block_type=block.type,
                text=block.content,
                top=block.top_left_y,
                bottom=block.bottom_right_y,
                signals=tuple(signals),
                flagged=flagged,
                detail=detail,
            )
        )
    return regions


def _last_heading_index(blocks: list[OcrBlock]) -> int:
    """Reading-order index of the final structural heading, or -1."""
    return max((i for i, b in enumerate(blocks) if b.type == "title"), default=-1)


def _classify_block(
    block: OcrBlock,
    *,
    index: int,
    height: int,
    width: int,
    last_heading: int,
    vocab: RegionVocabulary,
    in_conclusions: bool,
) -> tuple[RegionKind, list[str], bool, str]:
    content = block.content or ""
    sig = _Signals()
    band = _band(block, height)
    block_width = (block.bottom_right_x - block.top_left_x) / width if width > 0 else 1.0

    is_footer = sig.add("type_footer", when=block.type == "footer")
    is_header = sig.add("type_header", when=block.type == "header")
    sig.add("type_references", when=block.type == "references")
    sig.add("type_signature", when=block.type == "signature")
    is_narrow = sig.add("margin_narrow", when=width > 0 and block_width < ASIDE_MAX_WIDTH)
    sig.add("band_bottom", when=height > 0 and band >= FOOTNOTE_BAND)
    sig.add("marker_footnote", when=_matches_any(content, vocab.footnote_markers))
    sig.add(
        "phrase_closing",
        when=mentions_closing_phrase(content, vocab.closing_phrases),
    )
    sig.add("after_last_heading", when=last_heading >= 0 and index > last_heading)

    names = sig.names

    # A footer is furniture whatever else it looks like.
    if is_footer:
        return "furniture", names, False, ""

    # The inverse error: a heading called furniture would leave the body silently.
    if is_header and _matches_any(content, vocab.heading_markers):
        return "body", names, True, "header block carries a structural heading"

    if is_header:
        return "furniture", names, False, ""

    # Band and type must agree: a narrow block in the margin is the aside, while one
    # spanning the text measure, or with no geometry to corroborate, is more likely a
    # mis-typed provision. Such a block is read as body and flagged, rather than
    # removed as a provision paragraph on the strength of its type alone.
    if block.type == "aside_text":
        if is_narrow:
            return "aside", names, False, ""
        return "body", names, True, "aside_text block is not a narrow margin column"

    if block.type in _DROPPED_TYPES:
        return "unknown", names, False, "non-text region"

    conclusions_signals = {"type_signature", "phrase_closing", "after_last_heading"} & set(names)
    if len(conclusions_signals) >= 2:
        return "conclusions", names, False, ""
    # Name and role carry no phrase of their own; reading order binds them.
    if in_conclusions and block.type in _BODY_TYPES:
        return "conclusions", names, False, "continues the attestation"

    footnote_signals = {"type_references", "band_bottom", "marker_footnote"} & set(names)
    if len(footnote_signals) >= 2:
        return "footnote", names, False, ""
    if "type_references" in names:
        return "body", names, True, "references block failed the band and marker check"

    if block.type in _BODY_TYPES:
        return "body", names, False, ""

    return "unknown", names, True, f"unmapped block type {block.type!r}"


__all__ = [
    "DEFAULT_FOOTNOTE_MARKERS",
    "FOOTNOTE_BAND",
    "Region",
    "RegionKind",
    "RegionVocabulary",
    "classify_layouts",
    "classify_page",
    "vocabulary_for",
    "vocabulary_for_jurisdiction",
]
