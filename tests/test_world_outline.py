"""Offline topology and small-geometry checks for the map builder."""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "world_outline", _ROOT / "scripts/build_world_outline.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rectangle(x: float, y: float, width: float, height: float) -> list:
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height], [x, y]]


def _feature(code: str, geometry: dict) -> dict:
    return {"type": "Feature", "properties": {"stele_code": code}, "geometry": geometry}


def test_reassignment_preserves_both_mainlands_and_moves_peninsula(
    builder: ModuleType,
) -> None:
    # Invented rectangles exercise the bounding-box policy, not real cartography.
    peninsula = _rectangle(34, 44.5, 1, 1)
    mainland = _rectangle(37, 47, 4, 3)
    west = _rectangle(30, 45.5, 5, 3)
    features = [
        _feature("ru", {"type": "MultiPolygon", "coordinates": [[mainland], [peninsula]]}),
        _feature("ua", {"type": "Polygon", "coordinates": [west]}),
    ]
    builder._reassign_crimea(features)
    assert features[0]["geometry"]["coordinates"] == [[mainland]]
    from shapely.geometry import shape

    resulting = shape(features[1]["geometry"])
    assert resulting.is_valid
    assert resulting.area == pytest.approx(16)
    assert resulting.bounds == (30, 44.5, 35, 48.5)
    assert resulting.geom_type == "Polygon"  # Shared seam was dissolved.


def test_small_geometry_preserved_exactly(builder: ModuleType) -> None:
    ring = [[math.cos(i / 20 * math.tau), math.sin(i / 20 * math.tau)] for i in range(20)]
    ring.append(ring[0])
    geometry = {"type": "Polygon", "coordinates": [ring]}
    assert builder._adaptive_simplify(geometry) == geometry
    assert builder._vertex_count(geometry) == 21


def test_large_geometry_simplifies_without_losing_topology(builder: ModuleType) -> None:
    from shapely.geometry import shape

    ring = [[math.cos(i / 1000 * math.tau), math.sin(i / 1000 * math.tau)] for i in range(1000)]
    ring.append(ring[0])
    geometry = {"type": "Polygon", "coordinates": [ring]}
    simplified = builder._adaptive_simplify(geometry)
    assert 3 < builder._vertex_count(simplified) < builder._vertex_count(geometry)
    assert shape(simplified).is_valid
    assert not shape(simplified).is_empty


def test_explicit_local_source_and_output(
    builder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(builder, "_build_stele_lookups", lambda: ({"999": "xx"}, {}, {"xx": 3}))

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("local source must not download")

    monkeypatch.setattr(builder.urllib.request, "urlopen", no_network)
    source = tmp_path / "source.json"
    geometry = {"type": "Polygon", "coordinates": [_rectangle(1, 1, 1, 1)]}
    source.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "properties": {"ISO_N3": "999", "NAME": "Example"},
                        "geometry": geometry,
                    }
                ]
            }
        )
    )
    output = tmp_path / "nested" / "world.geojson"
    builder.main(["--source", str(source), "--output", str(output)])
    feature = json.loads(output.read_text())["features"][0]
    assert feature["properties"] == {"name": "Example", "stele_code": "xx", "tier": 3}
    assert feature["geometry"] == geometry


def test_output_argument_required_before_any_download(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("missing output must not download")

    monkeypatch.setattr(builder.urllib.request, "urlopen", no_network)
    with pytest.raises(SystemExit) as error:
        builder.main([])
    assert error.value.code == 2


def test_registry_matches_configs() -> None:
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts/build_registry.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
