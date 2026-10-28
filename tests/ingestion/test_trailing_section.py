"""A declared trailing section is cut from every paid read, found from the text layer
alone, and its pages stay in the result marked as skipped rather than going missing."""

from __future__ import annotations

import io
from typing import Any

import pytest
from pypdf import PdfReader, PdfWriter
from structlog.testing import capture_logs

from codify.jurisdictions import SegmentationConfig, load_config, try_load_config
from codify.pipeline.enrich import ocr
from codify.pipeline.enrich.ocr import (
    TRAILING_SECTION,
    combine_page_texts_with_spans,
    extract_text_from_pdf,
    unreadable_pages,
)

ACT = "Article 1. Every licence holder shall keep a register of works. " * 4
NOTICE = "Tender for the resurfacing of the harbour road, closing in thirty days. " * 3


def _issue() -> list[str]:
    """Eight pages: contents, an act with one scanned page, then the notices."""
    return [
        "OFFICIAL BULLETIN\nContents\nAct No. 4 ........ 2\nNOTICES ........ 6\n" + ACT,
        ACT,
        ACT,
        "",
        ACT,
        "NOTICES\n" + NOTICE,
        NOTICE,
        "",
    ]


def _page_index(page: Any) -> int:
    return int(float(page.mediabox.width)) - 100


def _pdf(texts: list[str]) -> bytes:
    """Blank pages told apart by width, so a page keeps its text once copied."""
    writer = PdfWriter()
    for i in range(len(texts)):
        writer.add_blank_page(width=100 + i, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class _Client:
    """Records every page a paid engine is handed."""

    model = "test-model"

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.layout_pages: list[int] = []
        self.vision_pages: list[int] = []

    async def ocr(self, *, pdf: bytes, **_: Any) -> list[dict[str, Any]]:
        pages = [_page_index(p) + 1 for p in PdfReader(io.BytesIO(pdf)).pages]
        self.layout_pages += pages
        return [
            {"index": i, "markdown": self.texts[n - 1] or "scanned text"}
            for i, n in enumerate(pages)
        ]


@pytest.fixture
def texts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    pages = _issue()
    monkeypatch.setattr(
        "pypdf._page.PageObject.extract_text",
        lambda self, *a, **k: pages[_page_index(self)],
    )
    return pages


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, texts: list[str]) -> _Client:
    fake = _Client(texts)

    async def no_render(*_: Any) -> dict[int, bytes]:
        return {}

    async def vision(_pdf: bytes, page: int, *_: Any, **__: Any) -> str:
        fake.vision_pages.append(page)
        return "scanned text"

    monkeypatch.setattr(ocr, "_render_window", no_render)
    monkeypatch.setattr(ocr, "_ocr_page_with_context", vision)
    return fake


def _declare(monkeypatch: pytest.MonkeyPatch) -> None:
    """The synthetic jurisdiction, declaring a trailing section."""
    config = load_config("xa").model_copy(
        update={"segmentation": SegmentationConfig(trailing_section_patterns=["NOTICES"])}
    )
    monkeypatch.setattr(
        "codify.jurisdictions.try_load_config",
        lambda code: config if code == "xa" else try_load_config(code),
    )


@pytest.fixture
def declared(monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(monkeypatch)


@pytest.mark.asyncio
@pytest.mark.usefixtures("declared")
async def test_the_trailing_section_is_not_read_and_stays_on_the_record(
    texts: list[str], client: _Client
) -> None:
    with capture_logs() as captured:
        pages = await extract_text_from_pdf(_pdf(texts), client=client, country="xa")  # type: ignore[arg-type]

    assert client.layout_pages == [1, 2, 3, 4, 5]
    assert client.vision_pages == [4], "the act's scanned page is still read"
    assert [p.page_number for p in pages] == list(range(1, 9)), "no page goes missing"
    assert [p.skipped for p in pages] == [""] * 5 + [TRAILING_SECTION] * 3
    assert pages[5].text.startswith("NOTICES"), "the heading stays for the segmenter"
    assert all(p.layout is None and not p.rival_available for p in pages[5:])
    assert not unreadable_pages(pages), "a page not read is not a page lost"
    _, spans = combine_page_texts_with_spans(pages)
    assert [s.page for s in spans] == [1, 2, 3, 4, 5, 6, 7], "a blank back cover drops as before"
    assert [e for e in captured if e["event"] == "trailing_section_skipped"] == [
        {
            "event": "trailing_section_skipped",
            "log_level": "info",
            "first_page": 6,
            "last_page": 8,
            "pages": 3,
            "reason": TRAILING_SECTION,
        }
    ]


@pytest.mark.asyncio
async def test_the_acts_read_the_same_with_the_section_cut(
    texts: list[str], client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = await extract_text_from_pdf(_pdf(texts), client=client, country="xa")  # type: ignore[arg-type]
    _declare(monkeypatch)
    after = await extract_text_from_pdf(_pdf(texts), client=_Client(texts), country="xa")  # type: ignore[arg-type]

    assert [p.model_dump() for p in after[:5]] == [p.model_dump() for p in before[:5]]
    assert [p.layout for p in after[:5]] == [p.layout for p in before[:5]]


@pytest.mark.asyncio
async def test_a_jurisdiction_without_the_key_reads_every_page(
    texts: list[str], client: _Client
) -> None:
    pages = await extract_text_from_pdf(_pdf(texts), client=client, country="xa")  # type: ignore[arg-type]

    assert client.layout_pages == list(range(1, 9))
    assert client.vision_pages == [4, 8]
    assert not any(p.skipped for p in pages)


@pytest.mark.asyncio
@pytest.mark.usefixtures("declared")
@pytest.mark.parametrize(
    ("page", "text"),
    [
        # The heading's page carries no text layer: nothing to cut on.
        (5, ""),
        # Only the contents names the section; a listing is not a heading.
        (5, NOTICE),
        # A listing past the contents still carries its page number.
        (2, "NOTICES ........ 6\n" + ACT),
        # A heading line inside the issue's opening quarter.
        (1, "NOTICES\n" + ACT),
    ],
    ids=["image_only_heading", "contents_only", "listing_mid_issue", "too_early"],
)
async def test_nothing_is_cut_without_a_heading_line_past_the_contents(
    texts: list[str], client: _Client, page: int, text: str
) -> None:
    if page != 5:
        texts[5] = NOTICE
    texts[page] = text

    pages = await extract_text_from_pdf(_pdf(texts), client=client, country="xa")  # type: ignore[arg-type]

    assert client.layout_pages == list(range(1, 9))
    assert not any(p.skipped for p in pages)
