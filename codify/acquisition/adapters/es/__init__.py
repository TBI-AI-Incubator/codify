"""ES adapter, registers `gazette_xml` on import."""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters.es.boe import BoeAcquirer

register_adapter("gazette_xml", BoeAcquirer.from_config)

__all__ = ["BoeAcquirer"]
