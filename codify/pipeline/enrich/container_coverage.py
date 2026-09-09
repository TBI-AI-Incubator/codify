"""Do the source's grouping headings (Bab/Fasl, Book/Chapter) survive structuring as
containers, or flatten into the article run?

``container_coverage`` is the raw corpus-sweep measurement, lifted verbatim from
``corpus_scan.scan_text``. ``container_coverage_probe`` is the guarded signal, counting
only line-initial, ordinal-followed headings outside quoted amendments. Both read the
union of grouping keywords any class in the jurisdiction declares
(``_jurisdiction_grouping_aliases``), so a config-keyed
check cannot inherit the blind spot this guards.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from codify.jurisdictions import JurisdictionConfig
from codify.pipeline.enrich.anchors import (
    _kind_for_mangled_keyword,
    _kind_from_match,
    _quote_mask,
    cached_regex,
    keyword_aliases,
)


def _census_aliases(
    aliases: dict[str, tuple[str, ...]], regex: re.Pattern[str]
) -> dict[str, tuple[str, ...]]:
    """Aliases regrouped under the kind the scan regex actually resolves them to. Overlapping
    alternations let a config file a term under one kind while the regex answers with
    another (`gb` declares `SCHEDULE` as `schedule`, the pattern reports `hcontainer`), so
    counting by the config's grouping would show a level as present-and-unfound when it
    was found under another name.
    """
    regrouped: dict[str, list[str]] = {}
    for kind, terms in aliases.items():
        for term in terms:
            match = regex.search(f"\n{term} 5\n")
            resolved = _kind_from_match(match) if match else None
            regrouped.setdefault(resolved or kind, []).append(term)
    return {kind: tuple(dict.fromkeys(terms)) for kind, terms in regrouped.items()}


def _keyword_line_re(aliases: tuple[str, ...]) -> re.Pattern[str] | None:
    """Line-initial keyword plus the rest of its line. Looser than the scan
    regex on purpose: it must find the markers the scan refuses."""
    if not aliases:
        return None
    alts = "|".join(sorted((re.escape(a) for a in aliases), key=len, reverse=True))
    # Allow column gaps so the marker form remains measurable.
    return re.compile(rf"(?m)^[^\S\n]*(?:{alts})([^\n]{{0,40}})")


def _line_of(text: str, offset: int) -> int:
    """The 1-based line the first non-whitespace at or after `offset` sits on."""
    while offset < len(text) and text[offset] in " \t\r\n":
        offset += 1
    return text.count("\n", 0, offset) + 1


def _jurisdiction_grouping_aliases(
    config: JurisdictionConfig | None,
) -> dict[str, tuple[str, ...]]:
    """Grouping aliases declared by any document class (config-independent of
    the specific doctype), so a level one doctype omits is still detectable."""
    if config is None:
        return {}
    merged: dict[str, list[str]] = {}
    for name in config.document_classes:
        doc_class = config.get_document_class(name)
        if not doc_class:
            continue
        grouping = {e.akn_element for e in doc_class.hierarchy if e.level == "higher"}
        for kind, alias in keyword_aliases(config, name).items():
            if kind in grouping:
                merged.setdefault(kind, []).extend(alias)
    return {kind: tuple(dict.fromkeys(alias)) for kind, alias in merged.items()}


def _grouping_context(
    scan_result: Any, config: JurisdictionConfig | None, country: str, doctype: str
) -> tuple[Any, dict[str, tuple[str, ...]], set[str]]:
    """Shared prelude for the two coverage functions: the scan anchors, aliases
    regrouped under the kind the scan regex resolves them to, and the doctype's
    declared grouping (`higher`) levels."""
    anchors = scan_result.anchors
    regex = cached_regex(country, doctype)
    aliases = _census_aliases(keyword_aliases(config, doctype), regex)
    doc_class = config.get_document_class(doctype) if config else None
    grouping: set[str] = (
        {e.akn_element for e in doc_class.hierarchy if e.level == "higher"} if doc_class else set()
    )
    return anchors, aliases, grouping


def container_coverage(
    normalised_text: str,
    scan_result: Any,
    config: JurisdictionConfig | None,
    country: str,
    doctype: str,
) -> dict[str, Any]:
    """Raw grouping-coverage measurement, the corpus-sweep numbers. Behaviour-preserving lift
    of the block that lived inline in ``corpus_scan.scan_text``; ``scan_result`` is the
    ``scan_anchors_with_ambiguity`` output over ``normalised_text``.
    """
    anchors, aliases, grouping = _grouping_context(scan_result, config, country, doctype)
    kinds = Counter(a.kind for a in anchors)

    # Grouping levels only: subdivision markers are usually mid-line, so a
    # line-initial count of them reports a rate above 100%.
    recall: dict[str, list[int]] = {}
    for kind in grouping:
        line_re = _keyword_line_re(aliases.get(kind, ()))
        if line_re is None:
            continue
        present = 0
        found = 0
        kind_lines = {_line_of(normalised_text, a.char_offset) for a in anchors if a.kind == kind}
        for match in line_re.finditer(normalised_text):
            present += 1
            if _line_of(normalised_text, match.start()) in kind_lines:
                found += 1
        # Credited by line, so an anchor the scan found off-column cannot push
        # recall above the lines it was measured against.
        if present or kinds.get(kind):
            recall[kind] = [present, found]

    # A level the class never declares cannot be found at all; counting it
    # separately stops a config gap reading as perfect recall.
    undeclared: dict[str, int] = {}
    for other, other_aliases in _jurisdiction_grouping_aliases(config).items():
        if other in grouping:
            continue
        other_re = _keyword_line_re(other_aliases)
        if other_re is None:
            continue
        seen = len(other_re.findall(normalised_text))
        if seen:
            undeclared[other] = seen

    # `_BARE_MARKER_LINE_RE` also fires on well-formed markers an upstream pass
    # dropped, and those are already in the census. Count only the ones no alias
    # can reach, or the denominator double-counts them.
    mangled = sum(
        1
        for span in scan_result.ambiguity
        if span.detail.get("reason") == "marker_shaped_line_unclaimed"
        and _kind_for_mangled_keyword(str(span.detail.get("keyword", "")), aliases) is None
    )

    return {
        "container_recall": recall,
        "undeclared_grouping_lines": undeclared,
        "mangled_marker_lines": mangled,
        "grouping_declared": bool(grouping),
    }


# A grouping heading names its ordinal ("الفصل الأول"). Requiring one separates a real
# heading from prose that merely opens with the word, which is line-initial but not a
# heading. Latin roman-numeral headings ("Chapter I") are a deliberate miss, a missed
# warning being safer than a false one.
_ORDINAL_TAIL_RE = re.compile(
    r"^[\s:؛.,،ـ()\-]*"
    r"(?:[0-9٠-٩۰-۹]"
    r"|ال(?:أول|أولى|ثاني|ثانية|ثالث|ثالثة|رابع|رابعة|خامس|خامسة"
    r"|سادس|سادسة|سابع|سابعة|ثامن|ثامنة|تاسع|تاسعة|عاشر|عاشرة|حادي))"
)


def container_coverage_probe(
    normalised_text: str,
    scan_result: Any,
    config: JurisdictionConfig | None,
    country: str,
    doctype: str,
) -> dict[str, Any]:
    """Guarded heading-vs-container count for the ``container_coverage`` finding. A heading
    counts as ``present`` only when line-initial, ordinal-followed and outside a quoted
    amendment, and ``found`` when that line also produced a non-quoted container anchor.
    Totalled across every grouping kind the jurisdiction knows, declared by this doctype or
    not, so a config gap reads as present-with-nothing-found. The caller applies the floor.
    """
    anchors, aliases, grouping = _grouping_context(scan_result, config, country, doctype)

    # Union of grouping aliases: this doctype's declared levels plus any grouping
    # keyword a sibling doctype declares (the config-gap case).
    juris = _jurisdiction_grouping_aliases(config)
    grouping_aliases: dict[str, tuple[str, ...]] = {}
    for kind in grouping:
        if aliases.get(kind):
            grouping_aliases[kind] = aliases[kind]
    for other, alias in juris.items():
        if other not in grouping_aliases and alias:
            grouping_aliases[other] = alias
    if not grouping_aliases:
        return {"present": 0, "found": 0, "grouping_declared": bool(grouping)}

    # A heading a document only quotes, an amendment reproducing another law's
    # structure, is not its own. Quote membership comes from the source mask rather
    # than `inside_quoted_text` spans: those exist only for markers the doctype's
    # regex matched, and the target case is an undeclared keyword it cannot match, so
    # it would carry no span and slip through.
    quote_mask = _quote_mask(normalised_text)
    container_lines = {
        _line_of(normalised_text, a.char_offset)
        for a in anchors
        if a.kind in grouping_aliases and not a.quoted_amendment
    }

    present = 0
    found = 0
    seen_lines: set[int] = set()
    for alias in grouping_aliases.values():
        line_re = _keyword_line_re(alias)
        if line_re is None:
            continue
        for match in line_re.finditer(normalised_text):
            if not _ORDINAL_TAIL_RE.match(match.group(1)):  # guard 2
                continue
            if quote_mask[match.start()]:  # guard 1: inside a quoted amendment
                continue
            line = _line_of(normalised_text, match.start())
            if line in seen_lines:  # dedupe aliases overlapping on one line
                continue
            seen_lines.add(line)
            present += 1
            if line in container_lines:
                found += 1
    return {"present": present, "found": found, "grouping_declared": bool(grouping)}


# Fire only when capture falls below this fraction of the headings present, so a
# single missed container in a well-structured document is not a finding.
CONTAINER_COVERAGE_FLOOR = 0.5


def container_coverage_below_floor(probe: dict[str, Any]) -> bool:
    """The probe warrants a finding: headings are present and most produced no
    container. Guards 3-5 (total not per-kind, ratio floor, heading-presence)."""
    present = int(probe.get("present") or 0)
    found = int(probe.get("found") or 0)
    if present == 0:
        return False
    return (found / present) < CONTAINER_COVERAGE_FLOOR
