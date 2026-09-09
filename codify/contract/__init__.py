"""The symbols the product imports from this package, as shipped data so the
check still resolves once the two repositories are separate."""

from __future__ import annotations

from importlib.resources import files


def product_surface() -> list[str]:
    """Every `module:symbol` the product depends on, one per line."""
    text = (files(__package__) / "product_surface.txt").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


__all__ = ["product_surface"]
