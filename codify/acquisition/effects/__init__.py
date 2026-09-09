"""Amendment-effects feeds, one adapter per publisher.

Two exist: the UK publisher's changes feed and Cellar's amendment annotations.
The registry is here so a third is a new module and a line rather than a rewrite
of the caller.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from codify.acquisition.effects.cellar import CellarEffectsAdapter
from codify.acquisition.effects.types import (
    EffectsAdapter,
    FeedEffect,
    FeedResult,
    FeedTruncated,
)
from codify.acquisition.effects.uk import UkEffectsAdapter

_ADAPTERS: dict[str, Callable[[httpx.AsyncClient], EffectsAdapter]] = {
    UkEffectsAdapter.jurisdiction_code: UkEffectsAdapter,
    CellarEffectsAdapter.jurisdiction_code: CellarEffectsAdapter,
}


def adapter_for(jurisdiction_code: str, client: httpx.AsyncClient) -> EffectsAdapter:
    """The publisher's adapter, or a refusal naming what is supported.

    A missing adapter is not an empty result: the caller would record "no
    effects" for a jurisdiction whose publisher we simply cannot read.
    """
    try:
        return _ADAPTERS[jurisdiction_code.lower()](client)
    except KeyError:
        supported = ", ".join(sorted(_ADAPTERS)) or "none"
        raise LookupError(
            f"no amendment-effects adapter for {jurisdiction_code!r}; supported: {supported}"
        ) from None


__all__ = [
    "CellarEffectsAdapter",
    "EffectsAdapter",
    "FeedEffect",
    "FeedResult",
    "FeedTruncated",
    "UkEffectsAdapter",
    "adapter_for",
]
