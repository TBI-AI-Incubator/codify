"""EU adapters: `eurlex_cellar` for the live portal, `bulk_xml_archive` for the OP dump."""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters.eu.cellar import EuCellarAcquirer
from codify.acquisition.adapters.eu.datadump import EuDatadumpAcquirer

register_adapter("eurlex_cellar", EuCellarAcquirer.from_config)
register_adapter("bulk_xml_archive", EuDatadumpAcquirer.from_config)

__all__ = ["EuCellarAcquirer", "EuDatadumpAcquirer"]
