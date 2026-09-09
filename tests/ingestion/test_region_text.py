"""The structurer's text drops furniture and relocates footnotes, and is a no-op
(byte-identical to combine_page_texts) when a page carries no layout."""

from __future__ import annotations

from codify.pipeline.enrich.ocr import (
    OcrBlock,
    PageDimensions,
    PageLayout,
    PageResult,
    combine_page_texts,
    combine_page_texts_with_spans,
)
from codify.pipeline.enrich.region_text import combine_text_for_structure
from codify.pipeline.enrich.regions import classify_page

A4 = PageDimensions(dpi=87, width=720, height=1018)


def _combine_for_structure(pages: list[PageResult], regions: dict[int, list]) -> str:
    """The in-process lane's structurer input: combined page text, filtered."""
    text, spans = combine_page_texts_with_spans(pages)
    return combine_text_for_structure(text, regions, spans)


_BODY = "The Minister shall organise the accounts of the entity."
_FOOTER = "Official Gazette issue 12 page 47"
_FOOTNOTE = "(1) This article was amended by Decree-Law 5 of 2019."


def _blocks() -> list[OcrBlock]:
    return [
        OcrBlock(
            type="text",
            content=_BODY,
            top_left_x=40,
            top_left_y=300,
            bottom_right_x=680,
            bottom_right_y=340,
        ),
        OcrBlock(
            type="footer",
            content=_FOOTER,
            top_left_x=40,
            top_left_y=990,
            bottom_right_x=680,
            bottom_right_y=1010,
        ),
        # references + bottom band → footnote (two signals agree).
        OcrBlock(
            type="references",
            content=_FOOTNOTE,
            top_left_x=40,
            top_left_y=900,
            bottom_right_x=680,
            bottom_right_y=930,
        ),
    ]


def _page(markdown: str, blocks: list[OcrBlock] | None, page_number: int = 1) -> PageResult:
    layout = (
        PageLayout(engine="mistral-ocr-4-0", blocks=blocks, dimensions=A4)
        if blocks is not None
        else None
    )
    return PageResult(page_number=page_number, text=markdown, method="ocr", layout=layout)


def _regions(*pages: PageResult) -> dict[int, list]:
    return {
        page.page_number: classify_page(
            page.layout.blocks, page.layout.dimensions, page_number=page.page_number
        )
        for page in pages
        if page.layout is not None
    }


def test_footer_dropped_and_footnote_relocated_to_tail() -> None:
    # Footnote first, so a successful relocation is visible as a move to the foot.
    markdown = f"{_FOOTNOTE}\n{_BODY}\n{_FOOTER}"
    page = _page(markdown, _blocks())
    out = _combine_for_structure([page], _regions(page))
    assert _FOOTER not in out  # header/footer furniture dropped
    assert _BODY in out
    assert _FOOTNOTE in out  # retained, not dropped
    # The footnote now trails the body (a standalone paragraph the lift can move).
    assert out.index(_BODY) < out.index(_FOOTNOTE)


def test_body_line_never_dropped_below_the_floor() -> None:
    # A body line that shares no words with any furniture block survives intact.
    page = _page(f"{_BODY}\n{_FOOTER}", _blocks())
    out = _combine_for_structure([page], _regions(page))
    assert _BODY in out


def test_a_body_line_partially_overlapping_a_footer_is_kept() -> None:
    # Shares only "page" with the running footer, well below the floor: a real
    # provision line is never dropped for echoing a word or two of the furniture.
    line = "the register page must be signed each quarter"
    page = _page(f"{_BODY}\n{line}\n{_FOOTER}", _blocks())
    out = _combine_for_structure([page], _regions(page))
    assert line in out


def test_no_layout_is_byte_identical_to_combine_page_texts() -> None:
    page = _page(f"{_BODY}\n{_FOOTER}\n{_FOOTNOTE}", blocks=None)
    assert _combine_for_structure([page], {}) == combine_page_texts([page])


# A heading straddling a page break: a catchword at the foot of one page and the
# real heading overleaf. Both are two tokens, so both clear the region floor and
# match each other exactly.
_CATCHWORD = "BAB V . . ."
_HEADING = "BAB V"
_TITLE = "KEWENANGAN DAERAH PROVINSI DI LAUT"


def _catchword_page() -> PageResult:
    blocks = [
        OcrBlock(
            type="text",
            content=_BODY,
            top_left_x=40,
            top_left_y=300,
            bottom_right_x=680,
            bottom_right_y=340,
        ),
        OcrBlock(
            type="footer",
            content=_CATCHWORD,
            top_left_x=40,
            top_left_y=990,
            bottom_right_x=680,
            bottom_right_y=1010,
        ),
    ]
    return _page(f"{_BODY}\n{_CATCHWORD}", blocks, page_number=21)


def _heading_page() -> PageResult:
    blocks = [
        OcrBlock(
            type="title",
            content=f"{_HEADING}\n{_TITLE}",
            top_left_x=40,
            top_left_y=300,
            bottom_right_x=680,
            bottom_right_y=360,
        ),
    ]
    return _page(f"{_HEADING}\n{_TITLE}", blocks, page_number=22)


def test_catchword_is_dropped_only_on_its_own_page() -> None:
    pages = [_catchword_page(), _heading_page()]
    out = _combine_for_structure(pages, _regions(*pages))
    assert _CATCHWORD not in out  # still furniture where it was seen
    assert _HEADING in out  # the real heading overleaf survives
    assert _TITLE in out


def test_without_page_spans_the_catchword_still_reaches_every_page() -> None:
    # The pre-fix reading, kept explicit: a region that cannot be placed applies
    # to the whole document, and that is what deleted the heading. This fails if
    # the spans stop being threaded from either ingest lane.
    pages = [_catchword_page(), _heading_page()]
    text, _ = combine_page_texts_with_spans(pages)
    assert _HEADING not in combine_text_for_structure(text, _regions(*pages))


def test_a_running_footer_is_still_dropped_from_every_page() -> None:
    # Page scoping must not weaken furniture removal: each page carries its own
    # footer region, so a footer repeated throughout goes from all of them.
    pages = [_page(f"{_BODY}\n{_FOOTER}", _blocks(), page_number=n) for n in (1, 2, 3)]
    out = _combine_for_structure(pages, _regions(*pages))
    assert _FOOTER not in out
    assert out.count(_BODY) == 3
