"""Where a built acquisition index lives, one convention for every adapter."""

from __future__ import annotations

import os
from pathlib import Path

# Built indexes live outside the tree, under the datadump directory the
# deployment mounts, one per jurisdiction.
DEFAULT_INDEX_DIR = "data/datadumps"


def index_path(jurisdiction_code: str) -> Path:
    """`<ACQUISITION_INDEX_DIR or data/datadumps>/<jurisdiction>/index.json`."""
    root = Path(os.environ.get("ACQUISITION_INDEX_DIR") or DEFAULT_INDEX_DIR)
    return root / jurisdiction_code / "index.json"
