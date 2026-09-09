"""Registry of available lenses.

Applications can register lenses directly; plugins are discovered via
the ``codify.lenses`` entry-point group.
"""

from __future__ import annotations

from importlib.metadata import entry_points

import structlog

from codify.lenses.types import Lens

_log = structlog.get_logger()

_registry: dict[str, Lens] = {}
_discovered = False


def register(lens: Lens) -> None:
    if lens.name in _registry:
        raise ValueError(f"lens {lens.name!r} already registered")
    _registry[lens.name] = lens


def _discover() -> None:
    """Load lenses from the ``codify.lenses`` entry-point group once. A broken
    or malformed plugin is logged and skipped, never fatal."""
    global _discovered
    if _discovered:
        return
    for ep in entry_points(group="codify.lenses"):
        if ep.name in _registry:
            continue
        try:
            lens = ep.load()()
        except Exception:
            _log.exception("lens.discovery.load_failed", entry_point=ep.name, target=ep.value)
            continue
        name = getattr(lens, "name", None)
        if not isinstance(name, str) or not hasattr(lens, "scan"):
            _log.error("lens.discovery.invalid_lens", entry_point=ep.name, target=ep.value)
            continue
        _registry.setdefault(name, lens)
    _discovered = True


def get(name: str) -> Lens:
    _discover()
    if name not in _registry:
        raise KeyError(f"lens {name!r} not registered")
    return _registry[name]


def list_names() -> list[str]:
    _discover()
    return sorted(_registry.keys())


def _reset_for_tests() -> None:
    global _discovered
    _registry.clear()
    _discovered = False


__all__ = ["get", "list_names", "register"]
