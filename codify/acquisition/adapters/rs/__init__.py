"""RS adapter, registers `paragraf_propisi` on import."""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters.rs.paragraf import ParagrafAcquirer

register_adapter("paragraf_propisi", ParagrafAcquirer.from_config)

__all__ = ["ParagrafAcquirer"]
