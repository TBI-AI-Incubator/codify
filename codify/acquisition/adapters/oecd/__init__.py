"""OECD adapters: `OecdCompendiumAcquirer` for the Compendium of OECD Legal Instruments."""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters.oecd.compendium import (
    OecdCompendiumAcquirer,
    OecdInstrumentMissing,
)

register_adapter("oecd_compendium", OecdCompendiumAcquirer.from_config)

__all__ = ["OecdCompendiumAcquirer", "OecdInstrumentMissing"]
