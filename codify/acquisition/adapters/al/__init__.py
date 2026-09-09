"""AL adapter, registers `pdf_gazette` on import."""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters.al.qbz import QbzAcquirer

register_adapter("pdf_gazette", QbzAcquirer.from_config)

__all__ = ["QbzAcquirer"]
