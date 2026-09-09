"""PS subordinate instruments (cabinet/ministerial decisions, presidential
decrees, orders, instructions) group articles into Fusul (فصل) and Abwab (باب).
Their config hierarchies once carried no container level, so the anchor scan
had no pattern to match and flattened every such document to article-only on
structuring, even when the headings were transcribed faithfully. This locks the
container level in: removing it again would resurface the flattening."""

from __future__ import annotations

import pytest

from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import build_anchor_regex, scan_anchors

# Cabinet-decision 182/2004 shape: seven Fusul, each opening an article run.
_THREE_FUSUL = """\
الفصل الأول
المادة 1
Body prose for the first provision.

الفصل الثاني
المادة 2
Body prose for the second provision.

الفصل الثالث
المادة 3
Body prose for the third provision.
"""

_SUBORDINATE_DOCTYPES = [
    "qarar_majlis_wuzara",
    "marsoum",
    "qarar_wazir",
    "qarar_rais",
    "qarar",
    "taalimat",
    "amr",
]


@pytest.mark.parametrize("doctype", _SUBORDINATE_DOCTYPES)
def test_fasl_headings_detected_as_chapters(doctype: str) -> None:
    config = load_config("ps")
    assert config is not None
    anchors = scan_anchors(_THREE_FUSUL, build_anchor_regex(config, doctype))
    chapters = [a for a in anchors if a.kind == "chapter"]
    assert len(chapters) == 3, f"{doctype}: {[a.kind for a in anchors]}"


# Two-level grouping: Bab (باب → part) above Fasl (فصل → chapter), the shape of
# the larger regulatory instruments (e.g. cabinet-decision 393/2005, 9 Abwab
# over 50 Fusul). Guards the Bab half of the fix, which the Fasl-only sample
# above would pass even if the part level were dropped or misnamed.
_BAB_OVER_FASL = """\
الباب الأول
الفصل الأول
المادة 1
Body prose one.

الفصل الثاني
المادة 2
Body prose two.

الباب الثاني
الفصل الثالث
المادة 3
Body prose three.
"""


@pytest.mark.parametrize("doctype", _SUBORDINATE_DOCTYPES)
def test_bab_headings_detected_as_parts_above_chapters(doctype: str) -> None:
    config = load_config("ps")
    assert config is not None
    anchors = scan_anchors(_BAB_OVER_FASL, build_anchor_regex(config, doctype))
    kinds = [a.kind for a in anchors]
    assert kinds.count("part") == 2, f"{doctype}: {kinds}"
    assert kinds.count("chapter") == 3, f"{doctype}: {kinds}"
