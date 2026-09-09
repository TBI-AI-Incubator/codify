"""Per-jurisdiction acquisition. Resolve adapters by SourceAdapter.kind."""

from __future__ import annotations

from collections.abc import Callable
from typing import get_args

from codify.acquisition.base import (
    AcquiredDocument,
    Acquirer,
    Body,
    CorpusManifest,
    DiscoveryQuery,
    DocumentRef,
    GazetteRef,
    PolitenessProfile,
    UnknownAdapterError,
)
from codify.acquisition.manifest import load_manifest
from codify.acquisition.politeness import make_polite_client
from codify.jurisdictions import SourceAdapter, SourceAdapterKind, load_config

_REGISTRY: dict[SourceAdapterKind, Callable[[str, SourceAdapter], Acquirer]] = {}


def register_adapter(
    kind: SourceAdapterKind,
    factory: Callable[[str, SourceAdapter], Acquirer],
) -> None:
    if kind not in get_args(SourceAdapterKind):
        raise ValueError(
            f"register_adapter: {kind!r} is not in SourceAdapterKind "
            f"({get_args(SourceAdapterKind)!r}). Add it to the Literal first."
        )
    _REGISTRY[kind] = factory


def registered_kinds() -> frozenset[SourceAdapterKind]:
    return frozenset(_REGISTRY.keys())


def selected_adapter(
    jurisdiction_code: str, *, kind: SourceAdapterKind | None = None
) -> SourceAdapter:
    """The adapter row `get_acquirer` would build from (primary, or by kind)."""
    cfg = load_config(jurisdiction_code)
    if not cfg.source_adapters:
        raise UnknownAdapterError(f"no source_adapters configured for {jurisdiction_code!r}")

    if kind is not None:
        adapters = [a for a in cfg.source_adapters if a.kind == kind]
    else:
        adapters = list(cfg.source_adapters)
    if not adapters:
        raise UnknownAdapterError(f"no source_adapter of kind={kind!r} for {jurisdiction_code!r}")
    return adapters[0]


def get_acquirer(jurisdiction_code: str, *, kind: SourceAdapterKind | None = None) -> Acquirer:
    """Resolve a jurisdiction's primary adapter (or one of a given kind)."""
    chosen = selected_adapter(jurisdiction_code, kind=kind)
    factory = _REGISTRY.get(chosen.kind)
    if factory is None:
        raise UnknownAdapterError(
            f"no Acquirer registered for kind={chosen.kind!r} "
            f"({jurisdiction_code!r}); register it in codify.acquisition.adapters"
        )
    return factory(jurisdiction_code, chosen)


__all__ = [
    "Acquirer",
    "AcquiredDocument",
    "Body",
    "CorpusManifest",
    "DiscoveryQuery",
    "DocumentRef",
    "GazetteRef",
    "PolitenessProfile",
    "UnknownAdapterError",
    "get_acquirer",
    "load_manifest",
    "make_polite_client",
    "register_adapter",
    "registered_kinds",
]
