"""Deterministic, label-free structural checks for a document corpus."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from lxml import etree

from codify.akn._schema import parse_xml
from codify.akn.structure_diff import StructureNode, structure_of
from codify.akn.vocabulary import (
    BORROWED_ANCESTORS,
    PROVISION_ELEMENTS,
    is_generic_container,
    local_name,
)
from codify.jurisdictions import JurisdictionConfig
from codify.lang import normalise_digits
from codify.quality.invariants import order_key

CHECKS: tuple[str, ...] = (
    "akn_parseable",
    "body_units_present",
    "eid_unique",
    "numbering_monotonic_basic_unit",
    "numbering_monotonic_children",
    "toc_entries_have_bodies",
    "anchor_coverage",
    "container_coverage",
    "translation_structure_preserved",
)

# `unmatched_marker` also covers deliberate rejections (a citation in prose, a
# quoted amendment); only these two are markers the scan wanted and could not read.
_UNCLAIMED_MARKER_REASONS = frozenset({"marker_shaped_line_unclaimed", "no_kind_resolved"})


@dataclass(frozen=True)
class Finding:
    """One check's verdict on one version."""

    check: str
    failed: bool | None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ran(self) -> bool:
        return self.failed is not None


def era_of(config: JurisdictionConfig | None, year: int | None) -> str:
    """The declared era containing ``year``, else why not: "no_config", "unknown"
    (no year), "no_eras_declared", "outside_declared_eras". Matched by year, so a
    transition year belongs to the earlier era."""
    if config is None:
        return "no_config"
    if year is None:
        return "unknown"
    if not config.legal_eras:
        return "no_eras_declared"
    for era in config.legal_eras:
        first, last = era.year_range()
        if (first is None or year >= first) and (last is None or year <= last):
            return era.id
    return "outside_declared_eras"


def _basic_unit(config: JurisdictionConfig | None, doctype: str) -> str | None:
    """The element carrying this doctype's numbered provisions. Not any declared
    element: the no-anchor fallback emits a `<section>`, which is declared."""
    from codify.pipeline.enrich.structure import basic_unit_kind

    return basic_unit_kind(config, doctype)


def _borrowed(el: etree._Element) -> bool:
    """Inside `<meta>` or a quoted amendment. By ancestry including self: the
    lifter gives the quote wrapper its own eId."""
    return any(local_name(e.tag) in BORROWED_ANCESTORS for e in (el, *el.iterancestors()))


def _provisions(root: etree._Element) -> list[etree._Element]:
    """Every provision in the subtree, by positive vocabulary: "eId-bearing and
    not prose" also admits `authorialNote`, `mod`, `attachment` and TLC entries."""
    return [
        el
        for el in root.iter()
        if isinstance(el.tag, str)
        and el.get("eId")
        and local_name(el.tag) in PROVISION_ELEMENTS
        and not _borrowed(el)
    ]


def _bodies(root: etree._Element) -> list[etree._Element]:
    """The act's own `<body>`, excluding attachment `<mainBody>`: an annex article
    is a provision of the annex, not of the body."""
    return [el for el in root.iter() if isinstance(el.tag, str) and local_name(el.tag) == "body"]


def _attachment_provisions(root: etree._Element) -> int:
    mains = [
        el for el in root.iter() if isinstance(el.tag, str) and local_name(el.tag) == "mainBody"
    ]
    return sum(len(_provisions(m)) for m in mains)


def _check_body_units(
    root: etree._Element, config: JurisdictionConfig | None, doctype: str
) -> Finding:
    """Whether the body carries the basic unit its config declares.

    Blind where that unit is `section`: the no-anchor fallback blob is one
    `<section>`, so it passes. Needs ingest to record the fallback lane.
    """
    kind = _basic_unit(config, doctype)
    if kind is None:
        return Finding("body_units_present", None, {"reason": "no_basic_unit_declared"})
    present: Counter[str] = Counter()
    for body in _bodies(root):
        present.update(local_name(el.tag) for el in _provisions(body))
    return Finding(
        "body_units_present",
        not present.get(kind),
        {
            "basic_unit": kind,
            "count": present.get(kind, 0),
            "other_body_kinds": {k: n for k, n in present.items() if k != kind},
            # Recorded, never counted.
            "attachment_provisions": _attachment_provisions(root),
        },
    )


def _check_eid_unique(root: etree._Element) -> Finding:
    provisions = _provisions(root)
    if not provisions:
        # "No duplicates" would put the most-broken versions in the passed bucket.
        return Finding("eid_unique", None, {"reason": "no_provisions"})
    counts = Counter(el.get("eId") for el in provisions)
    duplicates = {eid: n for eid, n in counts.items() if eid and n > 1}
    return Finding(
        "eid_unique",
        bool(duplicates),
        {
            "provisions": len(provisions),
            "duplicate_count": len(duplicates),
            "examples": sorted(duplicates)[:5],
        },
    )


def _numbering_findings(
    root: etree._Element, config: JurisdictionConfig | None, doctype: str
) -> list[Finding]:
    """Two findings: the basic unit's numbering and its children's. One combined
    verdict reported whichever kind happened to be comparable. Each abstains when
    its own kind contributed no sequence."""
    basic = _basic_unit(config, doctype)
    breaks: Counter[str] = Counter()
    compared: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}

    for parent in root.iter():
        if not isinstance(parent.tag, str) or _borrowed(parent):
            continue
        by_kind: dict[str, list[tuple[tuple[int, int, int], str]]] = {}
        for child in parent:
            eid = child.get("eId")
            if not isinstance(child.tag, str) or not eid:
                continue
            if local_name(child.tag) not in PROVISION_ELEMENTS:
                continue
            key = order_key(eid)
            if key is None:
                continue
            by_kind.setdefault(local_name(child.tag), []).append((key, eid))
        for kind, seq in by_kind.items():
            # A collision the eId assigner already found: counts even alone.
            for key, eid in seq:
                if key[2]:
                    breaks[kind] += 1
                    examples.setdefault(kind, []).append(eid)
            if len(seq) < 2:
                continue
            compared[kind] += 1
            for (a, _), (b, eid) in zip(seq, seq[1:], strict=False):
                if b <= a:
                    breaks[kind] += 1
                    examples.setdefault(kind, []).append(eid)

    def finding(name: str, kinds: set[str]) -> Finding:
        seen = {k: compared[k] for k in kinds if compared[k]}
        broke = {k: breaks[k] for k in kinds if breaks[k]}
        if not seen and not broke:
            return Finding(name, None, {"reason": "numbering_unparseable"})
        return Finding(
            name,
            bool(broke),
            {
                "sequences_compared": seen,
                "breaks_by_kind": broke,
                "examples": [e for k in broke for e in examples.get(k, [])][:5],
            },
        )

    kinds_seen = set(compared) | set(breaks)
    basic_kinds = {basic} & kinds_seen if basic else set()
    return [
        finding("numbering_monotonic_basic_unit", basic_kinds or ({basic} if basic else set())),
        finding("numbering_monotonic_children", kinds_seen - basic_kinds),
    ]


def _num_text(el: etree._Element) -> str | None:
    for child in el:
        if isinstance(child.tag, str) and local_name(child.tag) == "num":
            return " ".join("".join(child.itertext()).split()) or None
    return None


def _as_int(num: str | None) -> int | None:
    """The integer a `<num>` denotes, or None. ``isdecimal`` not ``isdigit``: the
    latter accepts the superscript footnote markers OCR leaves behind."""
    if not num:
        return None
    digits = "".join(c for c in num if c.isdecimal())
    return int(digits) if digits else None


def _check_toc(source_text: str | None, root: etree._Element) -> Finding:
    """Whether every article the cover or contents lists has a body. The listing
    is in the source text, which most versions do not retain, so usually None."""
    if not source_text:
        return Finding("toc_entries_have_bodies", None, {"reason": "no_source_text"})
    from codify.pipeline.enrich.cover_reconciliation import extract_cover_article_numbers

    listed = extract_cover_article_numbers(source_text)
    if not listed:
        return Finding("toc_entries_have_bodies", None, {"reason": "no_cover_listing"})
    present = {
        _as_int(_num_text(el))
        for body in _bodies(root)
        for el in _provisions(body)
        if local_name(el.tag) in {"article", "section"}
    }
    missing = sorted(n for n in listed if n not in present)
    return Finding(
        "toc_entries_have_bodies",
        bool(missing),
        {"listed": len(listed), "missing_count": len(missing), "examples": missing[:5]},
    )


def _check_anchor_coverage(
    source_text: str | None, config: JurisdictionConfig | None, doctype: str
) -> Finding:
    """Anchors captured against markers the source presents, plus the marker-shaped
    lines it could not claim. The ratio alone cannot fail: its denominator is built
    from the keywords the scan matched, so an unreadable keyword leaves both sides."""
    if not source_text:
        return Finding("anchor_coverage", None, {"reason": "no_source_text"})
    from codify.pipeline.enrich.anchors import (
        anchor_coverage,
        cached_regex,
        scan_anchors_with_ambiguity,
    )

    kind = _basic_unit(config, doctype)
    if kind is None or config is None:
        return Finding("anchor_coverage", None, {"reason": "no_basic_unit_kind"})
    scan = scan_anchors_with_ambiguity(
        source_text, cached_regex(config.code, doctype), country=config.code, doctype=doctype
    )
    unclaimed = sum(
        1 for s in scan.ambiguity if s.detail.get("reason") in _UNCLAIMED_MARKER_REASONS
    )
    coverage = anchor_coverage(source_text, scan.anchors, config, doctype, kind, with_masked=True)
    threshold = config.min_anchor_coverage or 0.0
    detail: dict[str, Any] = {
        "kind": kind,
        "captured": len(coverage.captured),
        "expected": len(coverage.expected),
        "missing": sorted(coverage.missing)[:5],
        "unclaimed_markers": unclaimed,
        "masked_markers": coverage.masked,
        "threshold": threshold,
    }
    if coverage.ratio is None:
        if unclaimed:
            return Finding("anchor_coverage", True, {**detail, "reason": "all_markers_unclaimed"})
        return Finding("anchor_coverage", None, {**detail, "reason": "no_markers_in_source"})
    detail["ratio"] = round(coverage.ratio, 4)
    if threshold <= 0:
        if unclaimed:
            return Finding("anchor_coverage", True, {**detail, "reason": "markers_unclaimed"})
        return Finding("anchor_coverage", None, {**detail, "reason": "no_threshold_declared"})
    if unclaimed:
        return Finding("anchor_coverage", True, {**detail, "reason": "markers_unclaimed"})
    if coverage.ratio < threshold:
        return Finding("anchor_coverage", True, {**detail, "reason": "below_threshold"})
    # `masked` counts only markers a span nothing closed hid. A balanced quote
    # masks the amendment it encloses, which is the point and not a failure.
    if coverage.masked:
        return Finding("anchor_coverage", True, {**detail, "reason": "markers_masked"})
    return Finding("anchor_coverage", False, detail)


# AKN grouping containers a heading should produce. `section` is omitted: in some
# hierarchies it is the basic unit, and counting it would forgive a flatten.
_AKN_CONTAINER_KINDS = frozenset(
    {
        "book",
        "tome",
        "part",
        "subpart",
        "title",
        "subtitle",
        "chapter",
        "subchapter",
        "division",
        "subdivision",
    }
)


def _akn_container_count(root: etree._Element) -> int:
    """Non-borrowed grouping containers actually in the AKN, so a quoted
    amendment's own chapters do not read as this document's structure."""
    return sum(
        1
        for el in root.iter()
        if isinstance(el.tag, str)
        and local_name(el.tag) in _AKN_CONTAINER_KINDS
        and not _borrowed(el)
    )


def _check_container_coverage(
    source_text: str | None, config: JurisdictionConfig | None, doctype: str, root: etree._Element
) -> Finding:
    """Grouping headings the source presents against the containers actually in
    the stored AKN. Config-independent: a `فصل` heading is measured even where the
    doctype omits it. Counting real AKN containers (not a fresh scan's anchors)
    catches a dedicated parser that flattened them, which a re-scan would still
    find in the source."""
    if not source_text or config is None:
        return Finding("container_coverage", None, {"reason": "no_source_text"})
    from codify.pipeline.enrich.anchors import cached_regex, scan_anchors_with_ambiguity
    from codify.pipeline.enrich.container_coverage import (
        container_coverage_below_floor,
        container_coverage_probe,
    )

    scan = scan_anchors_with_ambiguity(
        source_text, cached_regex(config.code, doctype), country=config.code, doctype=doctype
    )
    probe = container_coverage_probe(source_text, scan, config, config.code, doctype)
    present = int(probe["present"])
    # `found` is what the AKN kept, not what a re-scan finds: a structured parser
    # can emit zero chapters while the source still carries every heading.
    found = min(present, _akn_container_count(root))
    detail: dict[str, Any] = {
        "present": present,
        "found": found,
        "grouping_declared": probe["grouping_declared"],
    }
    if present == 0:
        # No grouping headings in the source: nothing to lose, not a miss.
        return Finding("container_coverage", None, {**detail, "reason": "no_grouping_headings"})
    detail["ratio"] = round(found / present, 4)
    return Finding("container_coverage", container_coverage_below_floor(detail), detail)


def _shape(nodes: tuple[StructureNode, ...]) -> list[tuple[str, str]]:
    """Document-order (name, nearest non-generic parent name).

    Wrappers are excluded from both halves: Bluebell re-derives them per parse, so
    leaving one as a parent turns `(point, hcontainer)` into `(point, article)` and
    reports an unchanged point as lost.
    """
    by_eid = {n.eid: n for n in nodes}

    def generic(node: StructureNode) -> bool:
        return is_generic_container(node.tag, node.name)

    def parent_tag(node: StructureNode) -> str:
        seen: set[str] = set()
        current = node.parent_eid
        while current in by_eid and current not in seen:
            seen.add(current)
            candidate = by_eid[current]
            if not generic(candidate):
                return candidate.name or candidate.tag
            current = candidate.parent_eid
        return ""

    return [
        (n.name or n.tag, parent_tag(n))
        for n in nodes
        if n.tag in PROVISION_ELEMENTS and not generic(n)
    ]


def _identities(nodes: tuple[StructureNode, ...], kind: str | None) -> set[str]:
    """Digit-normalised eIds of the basic unit, since shape alone cannot see a
    substitution: dropping article 2 and splitting article 3 leaves the tag
    sequence unchanged. Basic unit only, where numbering is numeric either side."""
    if kind is None:
        return set()
    return {normalise_digits(n.eid) for n in nodes if n.tag == kind}


def _deletions(expected: list[tuple[str, str]], found: list[tuple[str, str]]) -> dict[str, int]:
    """Provisions in the source and not the translation. An opcode diff, not a
    count difference, which let a loss cancel a split. Insertions stay exempt: a
    translation may split a provision the source ran together."""
    lost: Counter[str] = Counter()
    for op, i1, i2, _, _ in SequenceMatcher(a=expected, b=found, autojunk=False).get_opcodes():
        if op not in {"delete", "replace"}:
            continue
        for tag, parent in expected[i1:i2]:
            lost[f"{parent}/{tag}" if parent else tag] += 1
    return dict(lost)


def _insertions(expected: list[tuple[str, str]], found: list[tuple[str, str]]) -> int:
    matcher = SequenceMatcher(a=expected, b=found, autojunk=False)
    return sum(j2 - j1 for op, _, _, j1, j2 in matcher.get_opcodes() if op == "insert")


def _check_translations(
    akn_xml: str, translations: dict[str, str], basic_unit: str | None
) -> Finding:
    """Whether each translation kept the source's skeleton. On shape, not eIds: a
    translation transliterates those by design, so matching them diverges always."""
    if not translations:
        return Finding("translation_structure_preserved", None, {"reason": "no_translations"})
    source = structure_of(akn_xml)
    if source is None:
        return Finding("translation_structure_preserved", None, {"reason": "source_unparseable"})
    expected = _shape(source)
    if not expected:
        # Nothing to preserve; `body_units_present` covers the source's own state.
        return Finding(
            "translation_structure_preserved", None, {"reason": "source_has_no_provisions"}
        )
    expected_ids = _identities(source, basic_unit)

    lost: dict[str, dict[str, int]] = {}
    split: dict[str, int] = {}
    unreadable: list[str] = []
    for label, translated in translations.items():
        nodes = structure_of(translated)
        if nodes is None:
            unreadable.append(label)
            continue
        found = _shape(nodes)
        deletions = _deletions(expected, found)
        missing_ids = expected_ids - _identities(nodes, basic_unit)
        if missing_ids and basic_unit:
            deletions[basic_unit] = max(deletions.get(basic_unit, 0), len(missing_ids))
        if deletions:
            lost[label] = deletions
        added = _insertions(expected, found)
        if added:
            split[label] = added
    if unreadable and not lost:
        return Finding(
            "translation_structure_preserved", None, {"reason": "unparseable", "which": unreadable}
        )
    return Finding(
        "translation_structure_preserved",
        bool(lost),
        {
            "compared": len(translations),
            "diverged": lost,
            # Recorded, never counted: a loss and a split must stay separable.
            "split": split,
            "unreadable": unreadable,
        },
    )


def scan_version(
    akn_xml: str,
    *,
    config: JurisdictionConfig | None,
    doctype: str,
    source_text: str | None = None,
    translations: dict[str, str] | None = None,
) -> list[Finding]:
    """Every check's verdict on one version, in `CHECKS` order."""
    if source_text:
        # The structurer scanned the normalised text, so this must too.
        from codify.pipeline.enrich.structure import normalise_rtl_extract

        source_text = normalise_rtl_extract(source_text)
    if not akn_xml.strip():
        # No AKN stored, which is not the same defect as a malformed one.
        return [Finding(c, None, {"reason": "no_akn_stored"}) for c in CHECKS]
    try:
        root = parse_xml(akn_xml)
    except etree.XMLSyntaxError as exc:
        return [
            Finding("akn_parseable", True, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}),
            *(Finding(c, None, {"reason": "unparseable"}) for c in CHECKS[1:]),
        ]

    return [
        Finding("akn_parseable", False),
        _check_body_units(root, config, doctype),
        _check_eid_unique(root),
        *_numbering_findings(root, config, doctype),
        _check_toc(source_text, root),
        _check_anchor_coverage(source_text, config, doctype),
        _check_container_coverage(source_text, config, doctype, root),
        _check_translations(akn_xml, translations or {}, _basic_unit(config, doctype)),
    ]


__all__ = ["CHECKS", "Finding", "era_of", "scan_version"]
