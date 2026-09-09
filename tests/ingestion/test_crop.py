"""Crop a scanned page to an OCR block region.

The geometry is exercised in pure isolation; `render_block` is exercised with a
synthetic page so the crop-and-encode path runs without poppler.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from codify.pipeline.enrich import ocr
from codify.pipeline.enrich.ocr import (
    OcrBlock,
    PageDimensions,
    crop_box_for_block,
    render_block,
)

# The OCR endpoint reports ordinates in the source page's own units (dpi 87); we
# rasterise at 300, so a block's box scales by 300/87 onto the rendered page.
DIMS = PageDimensions(dpi=87, width=720, height=1018)
BLOCK = OcrBlock(
    type="title",
    content="# مادة",
    top_left_x=40,
    top_left_y=551,
    bottom_right_x=670,
    bottom_right_y=572,
)


def test_crop_box_scales_from_source_dpi_to_render_dpi() -> None:
    scale = 300 / 87
    assert crop_box_for_block(BLOCK, DIMS, render_dpi=300) == (
        round(40 * scale),
        round(551 * scale),
        round(670 * scale),
        round(572 * scale),
    )


def test_crop_box_higher_render_dpi_scales_linearly() -> None:
    """Zoom is a larger render: 4x the dpi puts the block 4x further out."""
    at1 = crop_box_for_block(BLOCK, DIMS, render_dpi=300)
    at4 = crop_box_for_block(BLOCK, DIMS, render_dpi=1200)
    for near, far in zip(at1, at4, strict=True):
        assert abs(far - 4 * near) <= 2  # rounding only


def test_crop_box_raises_without_dpi() -> None:
    """Legacy blocks land with dpi 0; a box off that would be nonsense."""
    legacy = PageDimensions(dpi=0, width=0, height=0)
    with pytest.raises(ValueError, match="non-positive dpi"):
        crop_box_for_block(BLOCK, legacy, render_dpi=300)


def test_crop_box_raises_on_non_positive_render_dpi() -> None:
    with pytest.raises(ValueError, match="non-positive dpi"):
        crop_box_for_block(BLOCK, DIMS, render_dpi=0)


def test_crop_box_raises_on_missing_or_inverted_ordinates() -> None:
    """`OcrBlock` defaults omitted ordinates to zero; a zero-area or inverted box
    must fail here, not on an opaque Pillow crop after the render."""
    empty = OcrBlock(type="text")  # all ordinates default to 0
    inverted = OcrBlock(
        type="text", top_left_x=670, top_left_y=572, bottom_right_x=40, bottom_right_y=551
    )
    for block in (empty, inverted):
        with pytest.raises(ValueError, match="no positive area"):
            crop_box_for_block(block, DIMS, render_dpi=300)


def _white_png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("zoom", [1.0, 2.0, 4.0])
async def test_render_block_crops_to_the_scaled_box(
    zoom: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, int] = {}

    async def fake_render(pdf_bytes: bytes, first: int, last: int, dpi: int) -> dict[int, bytes]:
        captured["dpi"] = dpi
        return {first: _white_png(4000, 5000)}

    monkeypatch.setattr(ocr, "_render_window", fake_render)

    png = await render_block(b"%PDF", 1, BLOCK, DIMS, base_dpi=300, zoom=zoom)

    render_dpi = round(300 * zoom)
    assert captured["dpi"] == render_dpi  # zoom re-renders the page larger
    left, top, right, bottom = crop_box_for_block(BLOCK, DIMS, render_dpi=render_dpi)
    with Image.open(io.BytesIO(png)) as cropped:
        assert cropped.size == (right - left, bottom - top)


@pytest.mark.asyncio
async def test_render_block_raises_when_page_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty_render(pdf_bytes: bytes, first: int, last: int, dpi: int) -> dict[int, bytes]:
        return {}

    monkeypatch.setattr(ocr, "_render_window", empty_render)
    with pytest.raises(ValueError, match="did not render"):
        await render_block(b"%PDF", 7, BLOCK, DIMS)
