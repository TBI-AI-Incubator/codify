"""Independently authored minimal configurations for portable behavior tests."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import codify.jurisdictions as jurisdictions


@contextmanager
def isolated_configs(
    monkeypatch: pytest.MonkeyPatch, root: Path, configs: dict[str, dict[str, Any]]
) -> Iterator[Path]:
    shutil.copytree(jurisdictions.JURISDICTIONS_DIR, root)
    for code, fields in configs.items():
        path = root / code / "config.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "code": code,
                    "name": "Synthetic configuration",
                    "tradition": ["civil_law"],
                    "languages": ["eng"],
                    **fields,
                }
            )
        )
    with monkeypatch.context() as patch:
        patch.setattr(jurisdictions, "JURISDICTIONS_DIR", root)
        jurisdictions.try_load_config.cache_clear()
        try:
            yield root
        finally:
            jurisdictions.try_load_config.cache_clear()
