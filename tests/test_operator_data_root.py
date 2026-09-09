"""Explicit operator datasets replace, rather than merge with, bundled data."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codify.data_paths import data_dir


@pytest.mark.parametrize("leaf", ["jurisdictions", "frameworks"])
@pytest.mark.parametrize("installed", [False, True])
def test_operator_directory_wins_over_available_defaults(tmp_path, monkeypatch, leaf, installed):
    package = tmp_path / "checkout" / "codify"
    default = package / "data" if installed else package.parent / "data"
    (default / leaf).mkdir(parents=True)
    operator = tmp_path / "operator"
    (operator / leaf).mkdir(parents=True)
    monkeypatch.setenv("CODIFY_DATA_ROOT", str(operator))
    assert data_dir(str(package / "jurisdictions.py"), leaf) == operator / leaf


@pytest.mark.parametrize("value", ["", "relative", "~/data"])
def test_relative_and_empty_roots_are_rejected(tmp_path, monkeypatch, value):
    monkeypatch.setenv("CODIFY_DATA_ROOT", value)
    with pytest.raises(ValueError, match="existing absolute directory"):
        data_dir(str(tmp_path / "codify/jurisdictions.py"), "jurisdictions")


@pytest.mark.parametrize("is_file", [False, True])
def test_missing_or_file_root_is_rejected(tmp_path, monkeypatch, is_file):
    root = tmp_path / "data"
    if is_file:
        root.write_text("not a directory")
    monkeypatch.setenv("CODIFY_DATA_ROOT", str(root))
    with pytest.raises(ValueError, match="existing absolute directory"):
        data_dir(str(tmp_path / "codify/jurisdictions.py"), "jurisdictions")


def test_missing_leaf_does_not_fall_back_to_bundled_data(tmp_path, monkeypatch):
    package = tmp_path / "codify"
    (package / "data/jurisdictions").mkdir(parents=True)
    root = tmp_path / "operator"
    root.mkdir()
    monkeypatch.setenv("CODIFY_DATA_ROOT", str(root))
    with pytest.raises(ValueError, match="missing the 'jurisdictions' directory"):
        data_dir(str(package / "jurisdictions.py"), "jurisdictions")


@pytest.mark.parametrize("installed", [False, True])
def test_unset_override_preserves_default_layout(tmp_path, monkeypatch, installed):
    monkeypatch.delenv("CODIFY_DATA_ROOT", raising=False)
    package = tmp_path / "checkout/codify"
    default = package / "data" if installed else package.parent / "data"
    (default / "jurisdictions").mkdir(parents=True)
    assert data_dir(str(package / "jurisdictions.py"), "jurisdictions") == default / "jurisdictions"


@pytest.mark.parametrize("installed", [False, True])
def test_real_loaders_use_operator_data_in_a_fresh_process(tmp_path, installed):
    source = Path(__file__).resolve().parents[1]
    operator = tmp_path / "operator"
    (operator / "jurisdictions/xz").mkdir(parents=True)
    (operator / "frameworks").mkdir()
    config = json.loads((source / "data/jurisdictions/xz/config.json").read_text())
    config["name"] = "Operator Zerzura"
    (operator / "jurisdictions/xz/config.json").write_text(json.dumps(config))
    import_root = source
    if installed:
        import_root = tmp_path / "vendor"
        shutil.copytree(
            source / "codify", import_root / "codify", ignore=shutil.ignore_patterns("__pycache__")
        )
        shutil.copytree(source / "data", import_root / "codify/data")
    program = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from codify.jurisdictions import JURISDICTIONS_DIR, JurisdictionDataMissing, load_config
from codify.frameworks.acquis import FRAMEWORKS_DIR
assert JURISDICTIONS_DIR == Path(sys.argv[2]) / 'jurisdictions'
assert FRAMEWORKS_DIR == Path(sys.argv[2]) / 'frameworks'
assert load_config('xz').name == 'Operator Zerzura'
try:
    load_config('xa')
except JurisdictionDataMissing:
    pass
else:
    raise AssertionError('bundled-only profile leaked into operator dataset')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program, str(import_root), str(operator)],
        cwd=tmp_path,
        env={**os.environ, "CODIFY_DATA_ROOT": str(operator)},
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
