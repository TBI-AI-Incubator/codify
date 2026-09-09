"""Reject generated or private-boundary files in built distributions."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

_GENERATED = {
    ".git",
    ".venv",
    ".hypothesis",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
_PRIVATE = (
    "codify/lenses/anticorruption/",
    "codify/lenses/eu_acquis/",
    "codify/lenses/defunct_authority/",
    "codify/storage/anticorruption.py",
)


def check(path: Path) -> None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
    bad = []
    for name in names:
        parts = PurePosixPath(name).parts
        if (
            _GENERATED.intersection(parts)
            or name.endswith((".pyc", ".pyo"))
            or PurePosixPath(name).name == "EXTRACTION.json"
            or any(segment in name for segment in _PRIVATE)
            or any(
                part == ".env" or (part.startswith(".env.") and part != ".env.example")
                for part in parts
            )
        ):
            bad.append(name)
    if bad:
        raise ValueError(f"{path.name} contains forbidden files: {bad}")


def main() -> None:
    archives = sorted(Path(sys.argv[1]).glob("*.whl")) + sorted(Path(sys.argv[1]).glob("*.tar.gz"))
    if not archives:
        raise SystemExit("no distributions found")
    for archive in archives:
        check(archive)
    print(f"Checked {len(archives)} distributions")


if __name__ == "__main__":
    main()
