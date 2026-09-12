"""Per-jurisdiction Acquirer implementations. Importing this package
registers all built-in adapters with codify.acquisition.
"""

from __future__ import annotations

from codify.acquisition import register_adapter
from codify.acquisition.adapters import (
    al,  # noqa: F401  (registers pdf_gazette)
    ee,  # noqa: F401  (registers bulk_xml_archive for ee)
    eu,  # noqa: F401  (registers eurlex_cellar)
    rs,  # noqa: F401  (registers paragraf_propisi)
    ua,  # noqa: F401  (registers html_portal)
)
from codify.acquisition.adapters.akn_native import AknNativeAcquirer

register_adapter("akn_native", AknNativeAcquirer.from_config)
