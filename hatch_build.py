"""Put the shipping subset of `data/` into the wheel and sdist, selected by the
configs' own flags. It lands under `codify/data/`, which the resolvers look for
last, so a checkout still reads the working tree.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_PROJECT = Path(__file__).resolve().parent

_DATA = _PROJECT / "data"
_JURISDICTIONS = _DATA / "jurisdictions"
_FRAMEWORKS = _DATA / "frameworks"
_REGISTRY = _JURISDICTIONS / "registry.json"
_SUPRANATIONAL = _JURISDICTIONS / "supranational_bodies.json"


def _ships(config: Path) -> bool:
    """`ships_in_open_wheel`, loaded by path: the package is not installed while
    its own wheel is built, and the rule belongs with the configs."""
    spec = importlib.util.spec_from_file_location(
        "_codify_jurisdictions_for_build",
        _PROJECT / "codify" / "open_wheel.py",
    )
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable in a checkout
        raise RuntimeError("cannot load open_wheel.py to decide what the wheel carries")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return bool(module.ships_in_open_wheel(config))


class DataBuildHook(BuildHookInterface):
    """Runs for the wheel and for the sdist, so a wheel built from an unpacked
    sdist finds the same selection."""

    PLUGIN_NAME = "codify-data"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        prefix = "codify/data" if self.target_name == "wheel" else "data"
        include: dict[str, str] = {}
        shipped: list[str] = []
        for config in sorted(_JURISDICTIONS.glob("*/config.json")):
            if not _ships(config):
                continue
            shipped.append(config.parent.name)
            for path in sorted(config.parent.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(_JURISDICTIONS)
                    include[str(path)] = f"{prefix}/jurisdictions/{rel}"
        if not shipped:
            raise RuntimeError(
                f"no jurisdiction under {_JURISDICTIONS} is flagged synthetic or "
                "public_reference; refusing to build a wheel or sdist with no configs"
            )
        for required in (_REGISTRY, _SUPRANATIONAL):
            if not required.is_file():
                raise RuntimeError(f"required jurisdiction data is missing: {required}")
        for path in sorted(_FRAMEWORKS.rglob("*")):
            if path.is_file():
                rel = path.relative_to(_FRAMEWORKS)
                include[str(path)] = f"{prefix}/frameworks/{rel}"
        # These two sit above the per-jurisdiction directories, so the loop
        # above never reaches them and both readers answer empty without them.
        if _SUPRANATIONAL.is_file():
            include[str(_SUPRANATIONAL)] = f"{prefix}/jurisdictions/{_SUPRANATIONAL.name}"
        if _REGISTRY.is_file() and shipped:
            include[str(self._filtered_registry(shipped))] = (
                f"{prefix}/jurisdictions/{_REGISTRY.name}"
            )
        build_data.setdefault("force_include", {}).update(include)

    def _filtered_registry(self, shipped: list[str]) -> Path:
        """The registry cut to the codes the wheel carries. Naming all 257 would
        contradict the selection the rest of this hook makes."""
        data = json.loads(_REGISTRY.read_text(encoding="utf-8"))
        keep = set(shipped)
        data["jurisdictions"] = [j for j in data.get("jurisdictions", []) if j.get("code") in keep]
        self._tmp = self._tmp or Path(tempfile.mkdtemp(prefix="codify-registry-"))
        out = self._tmp / _REGISTRY.name
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return out

    _tmp: Path | None = None

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
