"""The actual build hook refuses incomplete standalone data trees."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def hook_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    project = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "standalone_build_hook", project / "hatch_build.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "project"
    (root / "codify").mkdir(parents=True)
    shutil.copyfile(project / "codify/open_wheel.py", root / "codify/open_wheel.py")
    data = root / "data"
    jurisdictions = data / "jurisdictions"
    (jurisdictions / "xa").mkdir(parents=True)
    (jurisdictions / "xa/config.json").write_text(json.dumps({"code": "xa", "synthetic": True}))
    (jurisdictions / "registry.json").write_text(
        json.dumps({"jurisdictions": [{"code": "xa"}, {"code": "unselected"}]})
    )
    (jurisdictions / "supranational_bodies.json").write_text("{}")
    (data / "frameworks").mkdir()
    (data / "frameworks/example.yaml").write_text("name: Example\n")
    for key, value in {
        "_PROJECT": root,
        "_DATA": data,
        "_JURISDICTIONS": jurisdictions,
        "_FRAMEWORKS": data / "frameworks",
        "_REGISTRY": jurisdictions / "registry.json",
        "_SUPRANATIONAL": jurisdictions / "supranational_bodies.json",
    }.items():
        monkeypatch.setattr(module, key, value)
    return module


def _hook(module: ModuleType, target: str = "wheel") -> Any:
    return module.DataBuildHook(str(module._PROJECT), {}, MagicMock(), MagicMock(), "dist", target)


@pytest.mark.parametrize("target,prefix", [("wheel", "codify/data"), ("sdist", "data")])
def test_selected_data_and_filtered_registry_travel(
    hook_module: ModuleType, target: str, prefix: str
) -> None:
    hook = _hook(hook_module, target)
    build_data: dict[str, Any] = {}
    try:
        hook.initialize("standard", build_data)
        included = build_data["force_include"]
        assert f"{prefix}/jurisdictions/xa/config.json" in included.values()
        assert f"{prefix}/frameworks/example.yaml" in included.values()
        assert f"{prefix}/jurisdictions/supranational_bodies.json" in included.values()
        registry = next(
            Path(source) for source, dest in included.items() if dest.endswith("/registry.json")
        )
        assert json.loads(registry.read_text())["jurisdictions"] == [{"code": "xa"}]
    finally:
        hook.finalize("standard", build_data, "unused")
    assert not registry.exists()


def test_frameworks_do_not_hide_zero_selected_configs(hook_module: ModuleType) -> None:
    (hook_module._JURISDICTIONS / "xa/config.json").write_text('{"code":"xa"}')
    assert (hook_module._FRAMEWORKS / "example.yaml").is_file()
    with pytest.raises(RuntimeError, match="no configs"):
        _hook(hook_module).initialize("standard", {})


def test_absent_jurisdiction_directory_is_refused(hook_module: ModuleType) -> None:
    shutil.rmtree(hook_module._JURISDICTIONS)
    with pytest.raises(RuntimeError, match="no configs"):
        _hook(hook_module).initialize("standard", {})


@pytest.mark.parametrize("missing", ["registry.json", "supranational_bodies.json"])
def test_required_metadata_cannot_be_omitted(hook_module: ModuleType, missing: str) -> None:
    (hook_module._JURISDICTIONS / missing).unlink()
    with pytest.raises(RuntimeError, match="required jurisdiction data is missing"):
        _hook(hook_module).initialize("standard", {})
