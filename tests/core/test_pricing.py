"""Cost for the routes the gateway never sees."""

from __future__ import annotations

import pytest

from codify.core.pricing import cost_details


def test_ocr_is_priced_per_page_not_per_token() -> None:
    # $4.40 per 1000 pages on the data-zone meter we are billed on, and the page
    # count is the only usage key that bills.
    assert cost_details("mistral-ocr-4-0", {"pages_processed": 4}) == pytest.approx(
        {"total": 0.0176}
    )


def test_ocr_reconciles_against_the_azure_invoice() -> None:
    """24 Aug 2026: Langfuse recorded 3,031 pages, Azure billed quantity 3.031
    on "OCR 4 DZ Pages" at unitPrice 4.4, costInUSD 13.3364. Pins the rate to
    the invoice so a silent revert to the global meter fails here."""
    assert cost_details("mistral-ocr-4-0", {"pages_processed": 3031}) == pytest.approx(
        {"total": 13.3364}
    )


def test_ocr_without_a_page_count_is_unpriced() -> None:
    assert cost_details("mistral-ocr-4-0", {"total": 34285}) is None


def test_token_model_prices_input_and_output_separately() -> None:
    costs = cost_details("gemini-3.7-flash", {"input": 1_000_000, "output": 1_000_000})
    assert costs == pytest.approx({"input": 0.75, "output": 3.75, "total": 4.5})


def test_provider_prefix_is_stripped() -> None:
    assert cost_details("gemini/gemini-3.7-flash", {"input": 1_000_000}) == pytest.approx(
        {"input": 0.75, "total": 0.75}
    )


def test_embedding_output_is_free_and_omitted() -> None:
    assert cost_details(
        "gemini-embedding-2", {"input": 1_000_000, "total": 1_000_000}
    ) == pytest.approx({"input": 0.2, "total": 0.2})


@pytest.mark.parametrize(
    "model,usage",
    [
        ("some-model-we-have-never-priced", {"input": 100}),
        ("gemini-3.7-flash", {}),
        ("gemini-3.7-flash", {"total": 500}),
    ],
)
def test_unpriced_returns_none_rather_than_zero(model: str, usage: dict[str, int]) -> None:
    # A zero reads as free; an absent cost reads as unpriced, which is the truth.
    assert cost_details(model, usage) is None
