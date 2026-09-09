"""Shared progress-callback wrapper for lens scans."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


def emit(cb: Any, *, code: str, done: int, total: int, label: str | None = None) -> None:
    """Progress is decoration; never let it kill the scan."""
    if cb is None:
        return
    try:
        cb(code=code, done=done, total=total, label=label)
    except Exception as exc:  # noqa: BLE001, contract: progress failure never kills the scan
        logger.error("lens_progress_emit_failed", error=str(exc)[:200])


__all__ = ["emit"]
