"""A page image over the vision cap is downscaled before the model or the ink
measure sees it, and the work runs on the CPU pool rather than the event loop."""

from __future__ import annotations

import io
import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PIL import Image, ImageDraw

from codify.core.llm import LLMClient, VisionText
from codify.pipeline.enrich import ocr
from codify.pipeline.enrich.ocr import MAX_VISION_EDGE_PX, cap_vision_image
from codify.quality.page_read import ink_ratio


def _page(size: tuple[int, int]) -> bytes:
    """A white PNG page ruled with black bars, as a render hands it over."""
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    width, height = size
    for top in range(height // 10, height - height // 10, height // 8):
        draw.rectangle((width // 10, top, width - width // 10, top + height // 40), fill=0)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _size_and_format(data: bytes) -> tuple[tuple[int, int], str | None]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size, image.format


# A 4967 x 3508 pt plan sheet as the render clamp leaves it, and a tall strip.
@pytest.mark.parametrize("size", [(9934, 7016), (3000, 9000)])
def test_a_page_over_the_cap_is_downscaled_keeping_its_aspect(size: tuple[int, int]) -> None:
    original = _page(size)

    capped = cap_vision_image(original)

    (out_w, out_h), out_format = _size_and_format(capped)
    width, height = size
    assert out_format == "JPEG"
    assert max(out_w, out_h) == MAX_VISION_EDGE_PX
    expected_short = round(min(width, height) * MAX_VISION_EDGE_PX / max(width, height))
    assert abs(min(out_w, out_h) - expected_short) <= 1, (out_w, out_h, expected_short)
    assert (out_w > out_h) == (width > height)
    # The bars survive the resize, so the ink measure reads the same page.
    original_ink, capped_ink = ink_ratio(original), ink_ratio(capped)
    assert original_ink is not None and capped_ink is not None
    assert abs(capped_ink - original_ink) < 0.01, (original_ink, capped_ink)


# A4 at the default 300 dpi, and a page exactly at the cap.
@pytest.mark.parametrize("size", [(2480, 3508), (MAX_VISION_EDGE_PX, 1000)])
def test_a_page_within_the_cap_passes_through_unchanged(size: tuple[int, int]) -> None:
    original = _page(size)

    assert cap_vision_image(original) is original


def test_bytes_that_will_not_decode_pass_through() -> None:
    assert cap_vision_image(b"\x89PNG") == b"\x89PNG"


async def test_the_model_and_the_ink_see_the_capped_page_off_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    giant = _page((9934, 7016))
    sent: list[bytes] = []
    ink_threads: list[str] = []

    class _Page(dict[str, Any]):
        def extract_text(self) -> str:
            return ""

    class _Client:
        model = "vision-model"

        async def vision(
            self, *, prompt: str, images: list[bytes], system: str, model: str | None = None
        ) -> str:
            sent.extend(images)
            return VisionText("Lisa 1", "stop")

    def _recording_ink(image: bytes) -> float | None:
        ink_threads.append(threading.current_thread().name)
        return ink_ratio(image)

    async def _window(_pdf: bytes, first: int, last: int, _dpi: int) -> dict[int, bytes]:
        return {n: giant for n in range(first, last + 1)}

    async def _no_layout(*_a: object, **_k: object) -> tuple[dict[int, Any], dict[int, str]]:
        return {}, {}

    monkeypatch.setattr(ocr, "PdfReader", lambda *_: SimpleNamespace(pages=[_Page()]))
    monkeypatch.setattr(ocr, "build_document_context", lambda *_: "")
    monkeypatch.setattr(ocr, "_layout_pass", _no_layout)
    monkeypatch.setattr(ocr, "_render_window", _window)
    monkeypatch.setattr(ocr, "ink_ratio", _recording_ink)

    pages = await ocr.extract_text_from_pdf(b"%PDF-fake", client=cast(LLMClient, _Client()))

    assert len(sent) == 1
    (sent_w, sent_h), sent_format = _size_and_format(sent[0])
    assert (max(sent_w, sent_h), sent_format) == (MAX_VISION_EDGE_PX, "JPEG")
    assert pages[0].method == "vision_ocr"
    assert pages[0].ink == ink_ratio(sent[0])
    assert ink_threads and all(name.startswith("codify-cpu") for name in ink_threads), ink_threads


async def test_the_embedded_image_fallback_is_capped_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no render (poppler absent), the page's embedded scan goes instead, and
    a scan can be any size."""
    sent: list[bytes] = []

    class _Client:
        async def vision(
            self, *, prompt: str, images: list[bytes], system: str, model: str | None = None
        ) -> str:
            sent.extend(images)
            return VisionText("Lisa 1", "stop")

    monkeypatch.setattr(ocr, "_extract_first_image", lambda *_: _page((3000, 9000)))

    await ocr._ocr_page_with_context(
        b"%PDF-fake", 1, 1, cast(LLMClient, _Client()), "", render_if_missing=False
    )

    assert len(sent) == 1
    (sent_w, sent_h), _ = _size_and_format(sent[0])
    assert max(sent_w, sent_h) == MAX_VISION_EDGE_PX
