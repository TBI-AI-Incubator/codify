"""TH adapter, registers `json_portal` on import."""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters.th.ocs import OcsAcquirer

register_adapter("json_portal", OcsAcquirer.from_config)

__all__ = ["OcsAcquirer"]
