"""Unit tests for window-based anchor extraction (Phase C)."""

from __future__ import annotations

from codify.pipeline.enrich.anchors import StructuralAnchor, windows_from_anchors


def _section(number: str, offset: int) -> StructuralAnchor:
    return StructuralAnchor(
        kind="section",
        keyword="SECTION",
        number=number,
        char_offset=offset,
        line=1 + offset // 80,
        matched_text=f"Section {number}",
    )


def test_windows_partition_anchor_run_with_overlap() -> None:
    text = "X" * 30_000
    anchors = [_section(str(i), i * 1_500) for i in range(1, 19)]  # 18 sections, ~1500 chars apart
    windows = windows_from_anchors(text, anchors, target_size=6_000, overlap=400)

    # Anchors must partition completely, every input anchor lives in
    # exactly one window.
    owned = [a.number for w in windows for a in w.anchors]
    assert owned == [str(i) for i in range(1, 19)]

    # Each window owns between 3 and 8 anchors (the configured bounds).
    for w in windows:
        assert 3 <= len(w.anchors) <= 8, f"window has {len(w.anchors)} anchors"

    # Body span sizing: roughly target_size for the inner windows.
    inner_body_sizes = [w.body_end - w.body_start for w in windows[:-1]]
    assert all(4_500 <= b <= 12_000 for b in inner_body_sizes), inner_body_sizes


def test_windows_have_overlap_chars_on_both_sides() -> None:
    text = "X" * 20_000
    anchors = [_section(str(i), 2_000 * i) for i in range(1, 8)]
    windows = windows_from_anchors(text, anchors, target_size=4_000, overlap=400)

    assert len(windows) >= 2
    # Every non-edge window's text must extend `overlap` chars before
    # the first owned anchor and after the last.
    for i, w in enumerate(windows):
        assert w.body_start >= 0
        assert w.body_end <= len(w.text)
        if i > 0:
            # body_start is the overlap on the left edge, should equal overlap.
            assert w.body_start == 400, w.body_start
        if i < len(windows) - 1:
            # Tail overlap of `overlap` chars sits between body_end and
            # the end of the slice.
            assert len(w.text) - w.body_end == 400, len(w.text) - w.body_end


def test_windows_return_empty_when_no_top_level_anchors() -> None:
    # Container anchors only, windows_from_anchors should bail.
    anchors = [
        StructuralAnchor(
            kind="chapter",
            keyword="CHAPTER",
            number="I",
            char_offset=0,
            line=1,
            matched_text="CHAPTER I",
        ),
    ]
    assert windows_from_anchors("text", anchors) == []


def test_windows_text_slices_actual_raw_text() -> None:
    text = "PREFIX\nSection 1 body\nSection 2 body\nSection 3 body\nSUFFIX"
    anchors = [
        _section("1", text.find("Section 1")),
        _section("2", text.find("Section 2")),
        _section("3", text.find("Section 3")),
    ]
    windows = windows_from_anchors(text, anchors, target_size=10, overlap=4)
    # Anchors are dense so they end up in one window.
    assert len(windows) == 1
    w = windows[0]
    # The body slice should start at "Section 1".
    assert w.text[w.body_start : w.body_start + len("Section 1")] == "Section 1"
    # The full slice should include the SUFFIX overlap.
    assert "SUFFIX"[:4] in w.text or "SUFFIX" in w.text


def test_bold_wrapped_marker_normalised_before_scan() -> None:
    """The vision OCR wraps some markers in markdown bold (`# **مادة (٤٨)**`);
    the structure-stage normaliser strips the fences so the anchor regex's
    column boundary matches (ArbReg 39/2004 lost 12 articles this way)."""
    from codify.pipeline.enrich.structure import normalise_rtl_extract

    text = "# **مادة (٤٨)**\nنص المادة الثامنة والأربعين.\n"
    normalised = normalise_rtl_extract(text)
    assert "**" not in normalised
    assert "مادة (٤٨)" in normalised


def test_unpaired_bold_fence_preserved() -> None:
    """Only balanced `**...**` pairs are unwrapped; a lone `**` may be
    literal source content and survives normalisation."""
    from codify.pipeline.enrich.structure import normalise_rtl_extract

    assert "**" not in normalise_rtl_extract("# **مادة (٤٨)**\nنص.\n")
    assert "**" in normalise_rtl_extract("نص يحتوي على ** وحيد بلا إغلاق.\n")
