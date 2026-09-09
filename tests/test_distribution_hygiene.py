"""Built artifacts must exclude caches, credentials and private implementations."""

from pathlib import Path
from zipfile import ZipFile

import pytest
from check_distribution import check


@pytest.mark.parametrize(
    "name",
    [
        ".hypothesis/examples/cache",
        "codify/__pycache__/module.pyc",
        "codify/lenses/anticorruption/detector.py",
        ".env",
    ],
)
def test_forbidden_file_is_rejected(tmp_path: Path, name: str) -> None:
    wheel = tmp_path / "sample.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(name, "synthetic")
    with pytest.raises(ValueError, match="forbidden files"):
        check(wheel)


def test_public_plugin_interface_is_allowed(tmp_path: Path) -> None:
    wheel = tmp_path / "sample.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("codify/lenses/types.py", "synthetic")
    check(wheel)
