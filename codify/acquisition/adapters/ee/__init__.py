"""Estonian adapters: `EeDatadumpAcquirer` for Riigi Teataja bulk XML archives."""

from __future__ import annotations

from codify.acquisition.adapters.ee.datadump import (
    EeDatadumpAcquirer,
    EeDatadumpIndexMissing,
    EeDocNotInDump,
)

__all__ = [
    "EeDatadumpAcquirer",
    "EeDatadumpIndexMissing",
    "EeDocNotInDump",
]
