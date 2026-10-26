"""Per-jurisdiction Acquirer implementations. Importing this package
registers all built-in adapters with codify.acquisition.
"""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters import (
    ee,  # noqa: F401  (registers bulk_xml_archive for ee)
    es,  # noqa: F401  (registers gazette_xml)
    eu,  # noqa: F401  (registers eurlex_cellar)
    oecd,  # noqa: F401  (registers oecd_compendium)
    rs,  # noqa: F401  (registers paragraf_propisi)
    th,  # noqa: F401  (registers json_portal)
    ua,  # noqa: F401  (registers html_portal)
)
from codify.acquisition.adapters.akn_native import AknNativeAcquirer
from codify.acquisition.adapters.al import QbzAcquirer
from codify.acquisition.adapters.dom import GacetaAcquirer
from codify.acquisition.base import BaseAcquirer

_PDF_GAZETTES: dict[str, type[BaseAcquirer]] = {"al": QbzAcquirer, "do": GacetaAcquirer}


def _pdf_gazette_factory(jurisdiction_code: str, adapter: object) -> BaseAcquirer:
    acquirer = _PDF_GAZETTES.get(jurisdiction_code)
    if acquirer is None:
        raise ValueError(f"no pdf_gazette acquirer for {jurisdiction_code!r}")
    return acquirer.from_config(jurisdiction_code, adapter)


register_adapter("akn_native", AknNativeAcquirer.from_config)
register_adapter("pdf_gazette", _pdf_gazette_factory)
