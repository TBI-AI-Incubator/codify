"""Recover bounded amendment-opener evidence from a version's stored AKN."""

from __future__ import annotations

from collections import Counter

from lxml import etree

from codify.akn import AKN_NS
from codify.akn._schema import parse_xml
from codify.frbr import parse_source_ref
from codify.pipeline.enrich.inline_markup import _OP_CUES, _SCOPE_OPENER, _scope_container


def scoped_opener_evidence(xml: str) -> dict[tuple[str, str, str], tuple[str, str]]:
    """Map exact operation eId/URI/type to its operation and opener text.

    Require the same nearest container; enrichment also accepts ancestor scope.
    Nested-container inheritance is deliberately refused without stronger evidence.
    """
    try:
        root = parse_xml(xml)
    except (ValueError, etree.XMLSyntaxError):
        return {}
    body = root.find(f".//{{{AKN_NS}}}body")
    if body is None:
        return {}
    counts = Counter(root.xpath("//@eId"))
    scope: etree._Element | None = None
    opener_text = ""
    opener_uri = ""
    evidence: dict[tuple[str, str, str], tuple[str, str]] = {}
    for p in body.iter(f"{{{AKN_NS}}}p"):
        content = p.getparent()
        unit = content.getparent() if content is not None else None
        eid = unit.get("eId", "") if unit is not None else ""
        exact_unit = (
            content is not None
            and content.tag == f"{{{AKN_NS}}}content"
            and len(content) == 1
            and bool(eid)
            and counts[eid] == 1
        )
        text = "".join(p.itertext())
        refs = list(p.iter(f"{{{AKN_NS}}}ref"))
        # Recognise supported forms without rewriting their exact evidence association.
        external = [r for r in refs if parse_source_ref(r.get("href") or "") is not None]
        if _SCOPE_OPENER.search(text):
            scope = None
            opener_uri = ""
            if exact_unit and len(external) == 1:
                candidate_scope = _scope_container(p)
                scope_eid = candidate_scope.get("eId", "") if candidate_scope is not None else ""
                if scope_eid and counts[scope_eid] == 1:
                    scope = candidate_scope
                    opener_uri = external[0].get("href", "")
                    opener_text = text
            continue
        if scope is None or _scope_container(p) is not scope or not exact_unit:
            continue
        for ref in external:
            tokens = (ref.get("class") or "").split()
            if "derived" not in tokens or ref.get("href") != opener_uri or (ref.text or "").strip():
                continue
            for cue, operation in _OP_CUES:
                if f"amendment-{operation}" not in tokens or not cue.search(text):
                    continue
                key = (eid, opener_uri, f"amendment_{operation}")
                if key in evidence:
                    # Repeated operation evidence is ambiguous even under a unique unit.
                    evidence[key] = ("", "")
                else:
                    evidence[key] = (text, opener_text)
    return evidence
