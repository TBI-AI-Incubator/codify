"""UA adapter, registers `html_portal` on import."""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters.ua.zakon_rada import ZakonRadaAcquirer

register_adapter("html_portal", ZakonRadaAcquirer.from_config)

__all__ = ["ZakonRadaAcquirer"]
