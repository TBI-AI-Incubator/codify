"""Grade-and-annotate: classify a translation's audit into per-provision findings, so
an imperfect translation ships flagged rather than discarded by the persist gate.
`review` marks a silent legal-meaning risk, shipped but held out of the clean
tier; `advisory` marks what the reader can see. Both derive from the `translate_document` audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["review", "advisory"]

# category -> (severity, human label)
_POLICY: dict[str, tuple[Severity, str]] = {
    "money_missing": ("review", "monetary amount dropped in translation"),
    "money_duplicated": ("review", "monetary amount double-emitted"),
    "stray_sentinel": ("review", "numeric token did not round-trip"),
    "structural_loss": ("review", "provision lost or re-nested"),
    "lost_correction": ("review", "an established correction was dropped"),
    "body_untranslated": ("advisory", "paragraph left in the source script"),
    "front_matter_retained": ("advisory", "front-matter slot still in the source"),
    "front_matter_empty": ("advisory", "front-matter slot blank where the source has text"),
    "front_matter_duplicate": ("advisory", "front-matter slot repeats a neighbour"),
    "untranslated_marker": ("advisory", "untranslated placeholder survived"),
    "letter_spaced": ("advisory", "letter-spaced word (OCR artefact)"),
    "missing_title": ("advisory", "container title absent"),
}

# audit per-eid maps -> category. Every occurrence is keyed by eId, so the map
# size is the finding count (no unnamed remainder).
_EID_MAPS: dict[str, str] = {
    "money_missing_by_eid": "money_missing",
    "money_duplicated_by_eid": "money_duplicated",
    "stray_sentinels_by_eid": "stray_sentinel",
    "missing_container_titles_by_eid": "missing_title",
}
# category -> (count_key, map_key). The count increments even without an eId
# ancestor, so it can exceed the map: name what the map names and emit an unnamed
# finding for the rest, or a source-script `<p>` with no eId grades clean.
_COUNTED_MAPS: dict[str, tuple[str, str]] = {
    "body_untranslated": ("body_alien_script_hits", "body_alien_script_hits_by_eid"),
    "letter_spaced": ("letter_spaced_runs", "letter_spaced_runs_by_eid"),
    "front_matter_empty": ("front_matter_empty_slots", "front_matter_empty_slots_by_eid"),
}
# count-only audit keys (no map at all) -> category
_COUNT_KEYS: dict[str, str] = {
    "front_matter_source_retained": "front_matter_retained",
    "front_matter_duplicate_slots": "front_matter_duplicate",
    "untranslated_marker_hits": "untranslated_marker",
}


@dataclass(frozen=True)
class DeliveryFinding:
    eid: str  # "" when the audit could not name a location
    category: str
    severity: Severity
    detail: str


def delivery_findings(audit: dict[str, Any]) -> list[DeliveryFinding]:
    """Per-provision findings from the audit's defect maps. Structural-loss and
    lost-correction findings arrive from the persist step's own gates (they are
    not in the audit dict); merge them via ``extra``-style appends there."""
    out: list[DeliveryFinding] = []
    for key, cat in _EID_MAPS.items():
        sev, label = _POLICY[cat]
        for eid in sorted(audit.get(key) or {}):
            out.append(DeliveryFinding(eid=eid, category=cat, severity=sev, detail=label))
    for cat, (count_key, map_key) in _COUNTED_MAPS.items():
        sev, label = _POLICY[cat]
        by_eid = audit.get(map_key) or {}
        for eid in sorted(by_eid):
            out.append(DeliveryFinding(eid=eid, category=cat, severity=sev, detail=label))
        named = sum(_occurrences(by_eid[eid]) for eid in by_eid)
        unnamed = int(audit.get(count_key) or 0) - named
        if unnamed > 0:
            out.append(
                DeliveryFinding(eid="", category=cat, severity=sev, detail=f"{unnamed}: {label}")
            )
    for key, cat in _COUNT_KEYS.items():
        n = int(audit.get(key) or 0)
        if n:
            sev, label = _POLICY[cat]
            out.append(DeliveryFinding(eid="", category=cat, severity=sev, detail=f"{n}: {label}"))
    return out


def review_finding(category: str, eid: str = "", detail: str | None = None) -> DeliveryFinding:
    """Build a review-tier finding for a signal outside the audit dict
    (structural loss, a dropped correction)."""
    sev, label = _POLICY[category]
    return DeliveryFinding(eid=eid, category=category, severity=sev, detail=detail or label)


def has_review(findings: list[DeliveryFinding]) -> bool:
    return any(f.severity == "review" for f in findings)


def _occurrences(value: Any) -> int:
    """How many hits a per-eId map value represents. The maps disagree on shape:
    ``body_alien`` stores an int count, ``letter_spaced`` a list of runs,
    ``front_matter_empty`` a single source string (one slot). Count them the same
    way the count key does, so the unnamed remainder is not overstated."""
    if isinstance(value, bool):  # bool is an int subclass; a flag is one hit
        return 1
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1
