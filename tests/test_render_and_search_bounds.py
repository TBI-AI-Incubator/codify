"""Bounds on the two paths where a caller picks how much work the server does:
PDF rasterisation (pixels per render window, windows at once) and the repair
agent's regex tools (wall-clock per search)."""

from __future__ import annotations

import asyncio
import io
import time

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject

from codify.pipeline.enrich.ocr import (
    _RENDER_SLOTS,
    LETTER_AREA_SQ_IN,
    MAX_CONCURRENT_RENDERS,
    MAX_WINDOW_PIXELS,
    _dpi_for_pages,
    _render_gate,
)
from codify.repair.tools import _SEARCH_BUDGET_S, _compile, _matcher

A4_PT = (595.0, 842.0)


def _pdf(*sizes: tuple[float, float]) -> bytes:
    writer = PdfWriter()
    for width, height in sizes:
        writer.add_blank_page(width=width, height=height)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _pdf_with_raw_mediabox(box: list[float] | None) -> bytes:
    """A page whose /MediaBox is inverted or absent. Both are legal enough that
    poppler renders them, so the clamp has to cope rather than refuse."""
    writer = PdfWriter()
    writer.add_blank_page(width=A4_PT[0], height=A4_PT[1])
    page = writer.pages[0]
    if box is None:
        del page[NameObject("/MediaBox")]
    else:
        page[NameObject("/MediaBox")] = ArrayObject([FloatObject(v) for v in box])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _window_megapixels(sizes: list[tuple[float, float]], dpi: int) -> float:
    return sum((w / 72 * dpi) * (h / 72 * dpi) for w, h in sizes)


class TestRenderPixelCap:
    def test_the_ordinary_corpus_is_not_clamped(self) -> None:
        """A full window of A4 at the OCR default is what the corpus looks like.
        If the ceiling taxes that, it is set wrong."""
        assert _dpi_for_pages(_pdf(*([A4_PT] * 8)), 1, 8, 300) == 300

    def test_the_viewer_default_is_not_clamped(self) -> None:
        assert _dpi_for_pages(_pdf(A4_PT), 1, 1, 150) == 150

    def test_an_oversized_mediabox_lands_under_the_ceiling(self) -> None:
        # 43 inches square: legal at the PDF level, 169 Mpx at 300dpi.
        applied = _dpi_for_pages(_pdf((3120.0, 3120.0)), 1, 1, 300)
        assert applied < 300
        assert _window_megapixels([(3120.0, 3120.0)], applied) <= MAX_WINDOW_PIXELS

    def test_an_inverted_mediabox_is_still_clamped(self) -> None:
        """A signed area reads as 'no geometry' and skips the clamp, which is the
        whole bomb back in four edited numbers. Poppler normalises the box and
        renders it full size."""
        applied = _dpi_for_pages(_pdf_with_raw_mediabox([0, 0, -3120, 3120]), 1, 1, 300)
        assert applied < 300
        assert _window_megapixels([(3120.0, 3120.0)], applied) <= MAX_WINDOW_PIXELS

    def test_a_page_with_no_mediabox_still_renders(self) -> None:
        """Poppler defaults it to Letter. Raising here would fail an ingest that
        succeeded before the cap existed."""
        assert _dpi_for_pages(_pdf_with_raw_mediabox(None), 1, 1, 150) == 150

    def test_the_ceiling_is_per_window_not_per_page(self) -> None:
        """pdf2image reads the whole window before decoding any of it, so eight
        pages each under a per-page cap still peak at eight times it."""
        eight = [(1200.0, 1600.0)] * 8
        one = _dpi_for_pages(_pdf((1200.0, 1600.0)), 1, 1, 300)
        many = _dpi_for_pages(_pdf(*eight), 1, 8, 300)
        assert many < one
        assert _window_megapixels(eight, many) <= MAX_WINDOW_PIXELS

    def test_there_is_no_dpi_floor_to_escape_through(self) -> None:
        """A floor binds past roughly 12,650pt square and the pixel count grows
        quadratically again from there, which is the defect, not a mitigation."""
        for side in (12_650.0, 30_000.0, 200_000.0):
            applied = _dpi_for_pages(_pdf((side, side)), 1, 1, 300)
            assert _window_megapixels([(side, side)], applied) <= MAX_WINDOW_PIXELS, side

    def test_an_unreadable_pdf_falls_through_to_the_renderer(self) -> None:
        """The clamp is advisory, not a second validator: poppler recovers files
        pypdf cannot parse, and the renderer reports the real error."""
        assert _dpi_for_pages(b"not a pdf", 1, 1, 150) == 150

    def test_letter_is_the_assumed_size_for_a_missing_box(self) -> None:
        assert LETTER_AREA_SQ_IN == pytest.approx(93.5)


class TestRenderConcurrency:
    def test_the_gate_is_bounded_and_shared_per_loop(self) -> None:
        async def gates() -> tuple[int, bool]:
            first, second = _render_gate(), _render_gate()
            return first._value, first is second

        value, same = asyncio.run(gates())
        assert value == MAX_CONCURRENT_RENDERS
        assert same, "one gate per loop, or the bound counts nothing"

    def test_the_budget_composes_with_the_container_limit(self) -> None:
        """Measured: a window at the ceiling peaks near 1 GB resident. Times the
        concurrency this must leave headroom inside the api's 6g."""
        assert MAX_WINDOW_PIXELS * MAX_CONCURRENT_RENDERS <= 150_000_000

    def test_separate_loops_each_get_a_full_asyncio_gate(self) -> None:
        assert [asyncio.run(_gate_value()) for _ in range(2)] == [MAX_CONCURRENT_RENDERS] * 2

    def test_the_process_bound_is_not_per_loop(self) -> None:
        """DBOS runs workflows on its own loop, so the per-loop asyncio gate
        hands out a full budget twice. The threading semaphore is one object
        for the process and is what the render actually holds."""
        import threading

        assert isinstance(_RENDER_SLOTS, type(threading.BoundedSemaphore(1)))
        acquired = [_RENDER_SLOTS.acquire(blocking=False) for _ in range(MAX_CONCURRENT_RENDERS)]
        try:
            assert all(acquired)
            assert not _RENDER_SLOTS.acquire(blocking=False), "over-admitted a render"
        finally:
            for ok in acquired:
                if ok:
                    _RENDER_SLOTS.release()


async def _gate_value() -> int:
    return _render_gate()._value


class TestSearchTimeout:
    def test_a_pathological_pattern_gives_up_on_budget(self) -> None:
        rx = _compile(r"(a|a)*$")
        assert not isinstance(rx, str)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            _matcher(rx)("a" * 40 + "!")
        assert time.monotonic() - started < _SEARCH_BUDGET_S * 3

    def test_an_ordinary_pattern_still_matches(self) -> None:
        rx = _compile(r"section \d+")
        assert not isinstance(rx, str)
        match = _matcher(rx)
        assert match("see Section 12 below")
        assert not match("no numbered reference here")

    def test_the_budget_bounds_the_call_not_each_candidate(self) -> None:
        """A candidate cheap enough to finish inside a sliver used to reset the
        clock, so a gazette's line count multiplied the budget instead of
        sharing it. Every candidate here is individually fast."""
        rx = _compile(r"(a|a)*$")
        assert not isinstance(rx, str)
        match = _matcher(rx)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            for _ in range(200_000):
                match("a" * 11 + "!")
        assert time.monotonic() - started < _SEARCH_BUDGET_S * 3

    def test_a_long_pattern_is_still_refused_by_length(self) -> None:
        assert isinstance(_compile("a" * 500), str)

    def test_a_bad_pattern_returns_a_message_not_an_exception(self) -> None:
        assert isinstance(_compile("("), str)
