"""Which jurisdiction directories the open-core wheel carries.

Standard library only: the build hook loads this by path while the package is
being built, where the runtime dependencies are absent.
"""

from __future__ import annotations

import json
from pathlib import Path


def ships_in_open_wheel(config_path: Path) -> bool:
    """Whether this jurisdiction ships, decided by `synthetic` or
    `public_reference` rather than by a list that would drift from the flags. A
    config that will not parse is not shipped; the suite validates them all."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("synthetic") or data.get("public_reference"))


__all__ = ["ships_in_open_wheel"]
