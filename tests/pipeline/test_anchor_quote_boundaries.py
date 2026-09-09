"""Quote orientation recovery respects every article-level heading."""

from __future__ import annotations

import pytest

from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import (
    _stray_quote_mask,
    build_anchor_regex,
    scan_anchors,
)


def _numbers(text: str, kind: str) -> list[str | None]:
    config = load_config("xu")
    anchors = scan_anchors(text, build_anchor_regex(config, "act"), country="xu", doctype="act")
    return [a.number for a in anchors if a.kind == kind and not a.quoted_amendment]


@pytest.mark.parametrize("kind", ["article", "section"])
def test_mixed_quote_closer_cannot_borrow_a_later_provisions_opener(kind: str) -> None:
    marker = kind.upper()
    text = (
        "The authority adopts the “Account Rules”.\n\n"
        f'{marker} 1. These are the "Inquiry Rules.”\n\n'
        f"{marker} 2. All doubts shall favour implementation.\n\n"
        f"{marker} 3. The “Deposit Law” applies.\n\n"
        f"{marker} 4. A “day” means a calendar day.\n"
    )
    assert _numbers(text, kind) == ["1", "2", "3", "4"]


@pytest.mark.parametrize("kind", ["article", "section"])
@pytest.mark.parametrize("quotes", [("“", "”"), ("”", "“")])
def test_balanced_amendment_stays_masked_across_blank_lines(
    kind: str, quotes: tuple[str, str]
) -> None:
    marker = kind.upper()
    opening, closing = quotes
    text = (
        "The authority adopts the “Account Rules”.\n\n"
        f"{marker} 1. Replace the provision with:\n"
        f"{opening}{marker} 9. Replacement text.\n\n"
        f"Additional replacement text.{closing}\n\n"
        f"{marker} 2. Commencement.\n"
    )
    assert _numbers(text, kind) == ["1", "2"]
    assert not any(_stray_quote_mask(text, "xu"))


@pytest.mark.parametrize("kind", ["article", "section"])
def test_unclosed_curly_span_still_reports_hidden_text_and_resets(kind: str) -> None:
    marker = kind.upper()
    text = (
        f"{marker} 1. A “quotation with no closer.\n"
        f"{marker} 9. Hidden text.\n\n"
        f"{marker} 2. Commencement.\n"
    )
    assert _numbers(text, kind) == ["1", "2"]
    stray = _stray_quote_mask(text, "xu")
    assert stray[text.index(f"{marker} 9")]
    assert not stray[text.index(f"{marker} 2")]
