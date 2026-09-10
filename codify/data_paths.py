"""Where a data directory sits, for each tree this package is read from.

One resolver, not a list per caller: two such lists drifted apart.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir(module_file: str, leaf: str) -> Path:
    """The first `data/<leaf>` that exists, nearest tree first.

    Anchored on the package directory, so a caller's own depth cannot drift.
    """
    override = os.environ.get("CODIFY_DATA_ROOT")
    if override is not None:
        root = Path(override)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("CODIFY_DATA_ROOT must name an existing absolute directory")
        selected = root.resolve() / leaf
        if not selected.is_dir():
            raise ValueError(f"CODIFY_DATA_ROOT is missing the {leaf!r} directory")
        return selected
    package = next(p for p in Path(module_file).resolve().parents if p.name == "codify")
    # A `data/` inside the package settles it: only a build puts one there, and
    # where it is present no ancestor is a candidate whether or not it holds
    # this leaf. Nothing named is guessed, so any install directory answers the
    # same, and a leaf a build omitted reports absent rather than reading on.
    bundled = package / "data" / leaf
    if (package / "data").is_dir():
        return bundled
    roots = [package.parents[i] for i in (0, 2, 1, 3) if i < len(package.parents)]
    candidates = [r / "data" / leaf for r in roots] + [bundled]
    return next((p for p in candidates if p.exists()), candidates[0])
