"""Per-jurisdiction Acquirer implementations. Importing this package
registers all built-in adapters with codify.acquisition.
"""

from __future__ import annotations

from collections.abc import Callable

from codify.acquisition import register_adapter
from codify.acquisition.adapters import (
    ee,  # noqa: F401  (registers bulk_xml_archive for ee)
    es,  # noqa: F401  (registers gazette_xml)
    eu,  # noqa: F401  (registers eurlex_cellar)
    oecd,  # noqa: F401  (registers oecd_compendium)
    rs,  # noqa: F401  (registers paragraf_propisi)
    ua,  # noqa: F401  (registers html_portal)
)
from codify.acquisition.adapters.akn_native import AknNativeAcquirer
from codify.acquisition.adapters.al import QbzAcquirer
from codify.acquisition.adapters.dom import GacetaAcquirer
from codify.acquisition.adapters.pt import DiarioAcquirer
from codify.acquisition.adapters.th import OcsAcquirer
from codify.acquisition.base import BaseAcquirer

_BY_JURISDICTION: dict[str, dict[str, type[BaseAcquirer]]] = {
    "pdf_gazette": {"al": QbzAcquirer, "do": GacetaAcquirer},
    "json_portal": {"pt": DiarioAcquirer, "th": OcsAcquirer},
}


def _by_jurisdiction(kind: str) -> Callable[[str, object], BaseAcquirer]:
    def factory(jurisdiction_code: str, adapter: object) -> BaseAcquirer:
        acquirer = _BY_JURISDICTION[kind].get(jurisdiction_code)
        if acquirer is None:
            raise ValueError(f"no {kind} acquirer for {jurisdiction_code!r}")
        return acquirer.from_config(jurisdiction_code, adapter)

    return factory


register_adapter("akn_native", AknNativeAcquirer.from_config)
register_adapter("pdf_gazette", _by_jurisdiction("pdf_gazette"))
register_adapter("json_portal", _by_jurisdiction("json_portal"))
