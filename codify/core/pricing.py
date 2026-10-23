"""Prices for calls whose endpoint reports usage but no cost.

A `telemetry_mode="direct"` client and an agent run priced through
`record_agent_usage` cost their calls here; without a price they land at $0.00.
A `provider/` prefix on the model id is ignored.
"""

from __future__ import annotations

from collections.abc import Mapping

# USD per token, (input, output). Gemini: https://ai.google.dev/gemini-api/docs/pricing
_TOKEN_PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash": (0.0000015, 0.000009),
    # 3.6 and 3.7 are on intro pricing to 2026-12-31, then $1.50 and $7.50.
    "gemini-3.6-flash": (0.00000075, 0.00000375),
    "gemini-3.7-flash": (0.00000075, 0.00000375),
    "gemini-3.5-flash-lite": (0.0000003, 0.0000025),
    # Text tier; embeddings bill no output.
    "gemini-embedding-2": (0.0000002, 0.0),
    # Azure Global Standard, short context: meters "5.6 sol ShortCo Inp Std Gl"
    # and "5.6 sol ShortCo Opt Std Gl" in the Azure retail prices API.
    "gpt-5.6-sol": (0.000004, 0.00002),
}

# USD per page on the meter this subscription bills: "OCR 4 DZ Pages", SE
# Central, $4.40 per 1K. The zone selects the meter, so re-check on a move;
# the global meter is $4.00.
_PAGE_PRICES: dict[str, float] = {
    "mistral-ocr-4-0": 0.0044,
}


def cost_details(model: str, usage: Mapping[str, int]) -> dict[str, float] | None:
    """Cost in USD keyed to match `usage`, or None for a model with no price.

    Returning None rather than zero matters: an absent cost reads as unpriced,
    where a zero reads as free.
    """
    name = model.rpartition("/")[2]

    per_page = _PAGE_PRICES.get(name)
    if per_page is not None:
        pages = usage.get("pages_processed")
        return {"total": pages * per_page} if pages else None

    prices = _TOKEN_PRICES.get(name)
    if prices is None:
        return None
    details = {
        key: usage[key] * price
        for key, price in (("input", prices[0]), ("output", prices[1]))
        if usage.get(key)
    }
    if not details:
        return None
    return {**details, "total": sum(details.values())}


__all__ = ["cost_details"]
