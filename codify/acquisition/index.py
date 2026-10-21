"""Where a built acquisition index lives, one convention for every adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Built indexes live outside the tree, under the datadump directory the
# deployment mounts, one per jurisdiction.
DEFAULT_INDEX_DIR = "data/datadumps"


def index_path(jurisdiction_code: str) -> Path:
    """`<ACQUISITION_INDEX_DIR or data/datadumps>/<jurisdiction>/index.json`."""
    root = Path(os.environ.get("ACQUISITION_INDEX_DIR") or DEFAULT_INDEX_DIR)
    return root / jurisdiction_code / "index.json"


def write_index(path: Path, payload: dict[str, Any]) -> None:
    """Whole file or nothing, durable: temp file, fsync, rename, fsync the directory."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(payload, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
