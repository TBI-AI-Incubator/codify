"""Every `codify.` symbol the product imports, asserted to still resolve, so a
rename or a deletion fails here rather than downstream after a release."""

from __future__ import annotations

import importlib

import pytest

from codify.contract import product_surface


def _entries() -> list[str]:
    return product_surface()


def test_the_manifest_is_not_empty() -> None:
    # A manifest that failed to load would make every other check vacuous.
    assert len(_entries()) > 100


@pytest.mark.parametrize("entry", _entries())
def test_the_product_surface_still_resolves(entry: str) -> None:
    module_name, _, symbol = entry.partition(":")
    module = importlib.import_module(module_name)
    if symbol:
        assert hasattr(module, symbol), f"{module_name} no longer exports {symbol}"
