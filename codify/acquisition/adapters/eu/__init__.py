"""EU adapters: `eurlex_cellar` for the live portal, `bulk_xml_archive` for the OP dump."""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters.eu.cellar import EuCellarAcquirer
from codify.acquisition.adapters.eu.datadump import EuDatadumpAcquirer


def _bulk_xml_archive_factory(jurisdiction_code: str, adapter: object) -> object:
    if jurisdiction_code == "ee":
        from codify.acquisition.adapters.ee.datadump import EeDatadumpAcquirer
        from codify.jurisdictions import SourceAdapter

        if isinstance(adapter, SourceAdapter):
            return EeDatadumpAcquirer(adapter)
        return EeDatadumpAcquirer.from_config(jurisdiction_code, adapter)
    return EuDatadumpAcquirer.from_config(jurisdiction_code, adapter)


register_adapter("eurlex_cellar", EuCellarAcquirer.from_config)
register_adapter("bulk_xml_archive", _bulk_xml_archive_factory)  # type: ignore[arg-type]

__all__ = ["EuCellarAcquirer", "EuDatadumpAcquirer"]
