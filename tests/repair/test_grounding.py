"""eId → source-page grounding: exactness vs combine_page_texts + mapping."""

from __future__ import annotations

from codify.pipeline.enrich.ocr import PageResult, combine_page_texts
from codify.repair.grounding import (
    combine_with_spans,
    eid_to_page,
    offset_to_page,
    spans_from_json,
    spans_to_json,
)


def _pages() -> list[PageResult]:
    return [
        PageResult(page_number=1, text="Стаття 1. Перша\nтіло першої статті.", method="ocr"),
        PageResult(page_number=2, text="Стаття 2. Друга\nтіло другої статті.", method="text"),
    ]


def test_combine_with_spans_matches_combine_page_texts() -> None:
    pages = [
        PageResult(page_number=1, text="alpha\x00  beta", method="ocr"),  # control + double space
        PageResult(page_number=2, text="gamma", method="text"),
    ]
    text, spans = combine_with_spans(pages)
    assert text == combine_page_texts(pages)
    # Each span slices back to its cleaned page content.
    assert text[spans[0].start : spans[0].end] == "alpha beta"
    assert text[spans[1].start : spans[1].end] == "gamma"


def test_eid_to_page_maps_articles_to_their_pages() -> None:
    pages = _pages()
    text, spans = combine_with_spans(pages)
    mapping = eid_to_page(text, spans, country="ua")
    assert mapping.get("art_1") == 1
    assert mapping.get("art_2") == 2


def test_offset_to_page_clamps() -> None:
    _, spans = combine_with_spans(_pages())
    assert offset_to_page(spans, 0) == 1
    assert offset_to_page(spans, 10**9) == 2
    assert offset_to_page([], 5) is None


def test_spans_json_roundtrip() -> None:
    _, spans = combine_with_spans(_pages())
    assert spans_from_json(spans_to_json(spans)) == spans
    assert spans_from_json(None) == []


def test_toc_entry_maps_to_body_page_not_index() -> None:
    # A table-of-contents index on page 1, the real articles on page 2. The eId
    # must map to the body page, not the bare index line.
    pages = [
        PageResult(page_number=1, text="Стаття 1. Перша\nСтаття 2. Друга", method="text"),
        PageResult(
            page_number=2,
            text=(
                "Стаття 1. Перша\nПовний змістовний текст першої статті з достатнім "
                "обсягом правового тексту тут.\nСтаття 2. Друга\nЗмістовний текст другої статті."
            ),
            method="text",
        ),
    ]
    text, spans = combine_with_spans(pages)
    mapping = eid_to_page(text, spans, country="ua")
    assert mapping.get("art_1") == 2  # body, not the page-1 index
    assert mapping.get("art_2") == 2
