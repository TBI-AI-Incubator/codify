"""Measure anchor scanning against raw source files before ingestion."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codify.jurisdictions import JurisdictionConfig, load_config
from codify.pipeline.enrich.anchors import (
    cached_regex,
    keyword_aliases,
    scan_anchors_with_ambiguity,
)
from codify.pipeline.enrich.container_coverage import (
    _census_aliases,
    _keyword_line_re,
    _line_of,
    container_coverage,
)
from codify.pipeline.enrich.structure import basic_unit_kind, normalise_rtl_extract
from codify.pipeline.stages import resolve_doctype
from codify.quality.legibility import BANDS, band_for, function_word_rate, text_verdict
from codify.quality.lexicons import extend, words_for
from codify.quality.structural_scan import era_of

# Language-suffixed exports: `<stem>.ar/.en/.he`.
_DERIVED_SUFFIX_RE = re.compile(r"\.(?:ar|en|he)$", re.IGNORECASE)

# What follows a keyword: where the separator either admits or refuses the line.
_FORM_TESTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("balanced_parens", re.compile(r"^\(\s*[^\s)]+\s*\)")),
    ("mirrored_parens", re.compile(r"^\)\s*[^\s(]+\s*\(")),
    ("unbalanced_close", re.compile(r"^\)\s*\S")),
    ("dash", re.compile(r"^[-–—]+\s*\S")),
    ("bare", re.compile(r"^[0-9٠-٩۰-۹]")),
)


def is_derived_export(path: Path) -> bool:
    """True for a file this pipeline produced, returned to us in a corpus."""
    return bool(_DERIVED_SUFFIX_RE.search(path.stem))


def marker_form(tail: str) -> str:
    """Name the shape of what follows a keyword, so a refused shape is countable."""
    stripped = tail.strip()
    if not stripped:
        return "nothing_follows"
    for name, test in _FORM_TESTS:
        if test.match(stripped):
            return name
    return "word_or_prose"


@dataclass(frozen=True)
class CorpusSweep:
    """Sweep results, including excluded and unreadable files."""

    scans: list[DocumentScan]
    excluded_derived: int = 0
    unreadable: int = 0
    # Image-only PDFs are outside a text-layer sweep, not unreadable files.
    no_text_layer: int = 0


@dataclass(frozen=True)
class DocumentScan:
    """One document's measurement. Counts only; no text is retained."""

    path: str
    doctype: str
    chars: int
    anchors: int
    basic_units: int
    by_kind: dict[str, int] = field(default_factory=dict)
    by_pass: dict[str, int] = field(default_factory=dict)
    # form -> [keyword lines present, lines an anchor claimed]
    marker_forms: dict[str, list[int]] = field(default_factory=dict)
    # grouping kind -> [keyword lines present, anchors produced]
    container_recall: dict[str, list[int]] = field(default_factory=dict)
    # grouping keyword the document uses but its class never declares
    undeclared_grouping_lines: dict[str, int] = field(default_factory=dict)
    spans: dict[str, int] = field(default_factory=dict)
    span_reasons: dict[str, int] = field(default_factory=dict)
    # The pass that declared each span. Separates passes sharing a span kind.
    spans_by_pass: dict[str, int] = field(default_factory=dict)
    # Anchors each pass added or removed, so a pass that only deletes is visible.
    fires: dict[str, int] = field(default_factory=dict)
    blocking: int = 0
    # Marker-shaped lines with no matching alias; disjoint from marker_forms.
    mangled_marker_lines: int = 0
    # A missing declared level is not a miss and must not inflate the denominator.
    measurable: bool = True
    grouping_declared: bool = True
    # Function words per 1,000 tokens; None when the text cannot be judged.
    legibility: float | None = None
    # prose / not_prose / damaged, or None; bands alone do not distinguish these.
    verdict: str | None = None
    # Declared era containing the document year, or ``unknown``.
    era: str = "unknown"

    @property
    def has_container(self) -> bool:
        return any(found for _, found in self.container_recall.values())


def scan_text(
    text: str,
    *,
    config: JurisdictionConfig | None,
    country: str,
    doctype: str,
    path: str = "",
    era: str = "unknown",
) -> DocumentScan:
    """Measure one document after normalisation."""
    normalised = normalise_rtl_extract(text)
    # Legibility keys on the language; the pack still carries the script.
    words = (
        extend(words_for(config.authoritative_language), config.lexicon_words) if config else None
    )
    regex = cached_regex(country, doctype)
    result = scan_anchors_with_ambiguity(normalised, regex, country=country, doctype=doctype)
    anchors = result.anchors
    kinds = Counter(a.kind for a in anchors)
    # None when the class declares no provision level (`ohada/avis`, `sadc/*`).
    # Defaulting to "article" would score those as a corpus-wide miss.
    basic = basic_unit_kind(config, doctype)

    aliases = _census_aliases(keyword_aliases(config, doctype), regex)
    forms: dict[str, list[int]] = {}
    basic_line_re = _keyword_line_re(aliases.get(basic, ())) if basic is not None else None
    if basic_line_re is not None:
        # Credit by line: an anchor's offset starts at the whitespace it
        # consumed, so walk to the keyword before counting.
        anchor_lines = {_line_of(normalised, a.char_offset) for a in anchors if a.kind == basic}
        for match in basic_line_re.finditer(normalised):
            form = marker_form(match.group(1))
            row = forms.setdefault(form, [0, 0])
            row[0] += 1
            if _line_of(normalised, match.start()) in anchor_lines:
                row[1] += 1

    # Grouping-level recall, undeclared-grouping lines, and mangled markers, all
    # from the same source scan. Shared with the ingest probe and the per-version
    # structural scan; see `pipeline.enrich.container_coverage`.
    cov = container_coverage(normalised, result, config, country, doctype)

    return DocumentScan(
        path=path,
        doctype=doctype,
        chars=len(normalised),
        anchors=len(anchors),
        basic_units=kinds.get(basic, 0) if basic else 0,
        by_kind=dict(sorted(kinds.items())),
        by_pass=dict(sorted(Counter(a.source_pass or "unattributed" for a in anchors).items())),
        marker_forms=forms,
        container_recall=cov["container_recall"],
        undeclared_grouping_lines=cov["undeclared_grouping_lines"],
        spans=dict(sorted(Counter(s.kind for s in result.ambiguity).items())),
        mangled_marker_lines=cov["mangled_marker_lines"],
        measurable=basic_line_re is not None,
        grouping_declared=cov["grouping_declared"],
        span_reasons=dict(
            sorted(
                Counter(
                    s.detail.get("reason") or s.detail.get("reads_as") or s.kind
                    for s in result.ambiguity
                ).items()
            )
        ),
        spans_by_pass=dict(
            sorted(Counter(s.emitted_by or "unattributed" for s in result.ambiguity).items())
        ),
        fires=dict(sorted(result.fires.items())),
        blocking=sum(1 for s in result.ambiguity if s.blocking),
        legibility=function_word_rate(normalised, words),
        verdict=text_verdict(normalised, words),
        era=era,
    )


def scan_file(path: Path, *, config: JurisdictionConfig | None, country: str) -> DocumentScan:
    """Measure one text file, classifying its doctype the way an ingest would."""
    text = path.read_text(encoding="utf-8", errors="replace")
    year = _year_from_name(path.stem)
    doctype = resolve_doctype(config, title=path.stem, raw_date="", year=year, source=text)
    return scan_text(
        text,
        config=config,
        country=country,
        doctype=doctype,
        path=path.name,
        era=era_of(config, int(year) if year.isdigit() else None),
    )


_YEAR_RE = re.compile(r"(1[89]\d{2}|20\d{2})")


def _year_from_name(stem: str) -> str:
    """The Gregorian year in a filename, for the date-bounded classification
    rules. Empty when absent, which leaves the rules to decide on title alone."""
    found = _YEAR_RE.findall(stem)
    return found[-1] if found else ""


# A page-worth of characters. Below this a PDF has furniture rather than a text
# layer, and scanning it would report a structuring failure for a file nothing
# could have structured.
_MIN_PDF_CHARS = 200


def _pdf_text(path: Path) -> str | None:
    """The PDF's text layer, "" when it holds none, or None when it would not parse.

    Three states, because they call for three different things. Text is scanned.
    An image-only scan reaches the structurer through OCR, which a sweep does not
    run, so it is counted and skipped. A file the parser rejects is a corpus
    defect to chase, and folding it into the image-only count would hide it among
    documents nothing was ever going to read.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # noqa: BLE001 - any parser or I/O failure, reported as such
        return None
    return text if len(text.strip()) >= _MIN_PDF_CHARS else ""


def scan_corpus(
    root: Path,
    *,
    jurisdiction: str,
    include_derived: bool = False,
) -> CorpusSweep:
    """Scan the `.txt` and `.pdf` sources under `root`, skipping our own exports
    unless `include_derived`, and anything that will not decode.

    PDFs are read because a corpus is not the subset of itself somebody
    converted to text. On the bundle this was measured against, the derived text
    files were just over a third of the PDFs and all predated the most recent era, so a
    text-only sweep silently excluded that whole era and reported the passes
    written for it as never firing.

    Excluded, unreadable and text-layerless files are counted on the returned
    sweep rather than dropped. Path order, for a stable diff."""
    # Refuses on an absent config: every document would score zero against
    # builtin English aliases, reading as a corpus-wide collapse, not a typo.
    config = load_config(jurisdiction)
    scans: list[DocumentScan] = []
    excluded = unreadable = no_text_layer = 0
    for path in sorted(p for p in root.rglob("*") if p.suffix.lower() in (".txt", ".pdf")):
        if not include_derived and is_derived_export(path):
            excluded += 1
            continue
        if path.suffix.lower() == ".pdf":
            extracted = _pdf_text(path)
            if extracted is None:
                unreadable += 1
                continue
            if not extracted:
                no_text_layer += 1
                continue
            text = extracted
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # A wrong-codec file scans to zero anchors, which would read as a
                # structuring failure rather than a file we could not read.
                unreadable += 1
                continue
        scans.append(
            scan_text(
                text,
                config=config,
                country=jurisdiction,
                doctype=resolve_doctype(
                    config,
                    title=path.stem,
                    raw_date="",
                    year=_year_from_name(path.stem),
                    source=text,
                ),
                path=path.name,
                era=era_of(
                    config,
                    int(y) if (y := _year_from_name(path.stem)).isdigit() else None,
                ),
            )
        )
    return CorpusSweep(
        scans=scans,
        excluded_derived=excluded,
        unreadable=unreadable,
        no_text_layer=no_text_layer,
    )


def aggregate(sweep: CorpusSweep | list[DocumentScan]) -> dict[str, Any]:
    """Corpus totals. Accepts a sweep, or bare scans when the exclusion counts
    are not available."""
    if isinstance(sweep, CorpusSweep):
        scans, excluded, unreadable = sweep.scans, sweep.excluded_derived, sweep.unreadable
        no_text_layer = sweep.no_text_layer
    else:
        scans, excluded, unreadable, no_text_layer = sweep, 0, 0, 0
    forms: dict[str, list[int]] = {}
    recall: dict[str, list[int]] = {}
    kinds: Counter[str] = Counter()
    passes: Counter[str] = Counter()
    spans: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    span_passes: Counter[str] = Counter()
    fires: Counter[str] = Counter()
    fired_in: Counter[str] = Counter()
    ran_in: Counter[str] = Counter()
    undeclared: Counter[str] = Counter()
    for scan in scans:
        for form, (present, claimed) in scan.marker_forms.items():
            row = forms.setdefault(form, [0, 0])
            row[0] += present
            row[1] += claimed
        for kind, (present, found) in scan.container_recall.items():
            row = recall.setdefault(kind, [0, 0])
            row[0] += present
            row[1] += found
        undeclared.update(scan.undeclared_grouping_lines)
        kinds.update(scan.by_kind)
        passes.update(scan.by_pass)
        spans.update(scan.spans)
        reasons.update(scan.span_reasons)
        span_passes.update(scan.spans_by_pass)
        fires.update(scan.fires)
        fired_in.update(k for k, v in scan.fires.items() if v)
        ran_in.update(scan.fires.keys())

    present_total = sum(row[0] for row in forms.values())
    claimed_total = sum(row[1] for row in forms.values())
    # A class with no declared provision level was never censused, and one with
    # no grouping level cannot lose a container. Neither is a miss.
    measurable = [s for s in scans if s.measurable]
    with_units = [s for s in measurable if s.basic_units and s.grouping_declared]
    # A mangled keyword matches no alias, so the census cannot see it and a rate
    # over the census alone flatters the scanner. Counted only where the census
    # ran: an unmeasurable document has no numerator to earn its denominator.
    mangled = sum(s.mangled_marker_lines for s in measurable)
    honest_total = present_total + mangled
    return {
        "documents": len(scans),
        "documents_excluded_as_our_own_export": excluded,
        "documents_unreadable": unreadable,
        "documents_without_text_layer": no_text_layer,
        "characters": sum(s.chars for s in scans),
        "anchors": sum(s.anchors for s in scans),
        "basic_units": sum(s.basic_units for s in scans),
        "documents_measurable": len(measurable),
        "documents_without_basic_unit": sum(1 for s in measurable if not s.basic_units),
        "documents_with_units": len(with_units),
        "documents_with_units_and_no_container": sum(1 for s in with_units if not s.has_container),
        "marker_lines": present_total,
        "marker_lines_claimed": claimed_total,
        # None, not 1.0: a rate over no markers is not a perfect score.
        "capture_rate": (claimed_total / present_total) if present_total else None,
        "mangled_marker_lines": mangled,
        "honest_marker_lines": honest_total,
        # Keyed on the census, not the honest total: mangled lines alone give a
        # denominator with no numerator, and 0% would read as a collapse.
        "honest_capture_rate": (claimed_total / honest_total) if present_total else None,
        "marker_forms": {k: forms[k] for k in sorted(forms)},
        "container_recall": {k: recall[k] for k in sorted(recall)},
        "undeclared_grouping_lines": dict(sorted(undeclared.items())),
        "anchors_by_kind": dict(sorted(kinds.items())),
        "anchors_by_pass": dict(sorted(passes.items())),
        "spans_by_kind": dict(sorted(spans.items())),
        "spans_by_reason": dict(sorted(reasons.items())),
        "spans_by_pass": dict(sorted(span_passes.items())),
        # pass -> [net in that pass's own unit, documents where it did
        # anything, documents where it ran]. `AnchorScan.fires` names the unit
        # per pass; they are not comparable across passes and do not sum. The
        # third number separates a dead pass from a conditional one that was
        # rarely offered the chance.
        "passes": {k: [fires[k], fired_in[k], ran_in[k]] for k in sorted(fires)},
        # era -> pass -> the same triple. A pass dead corpus-wide is a deletion
        # candidate; a pass alive in one era only is a pass with a narrower job
        # than its name suggests, and the two read identically without this.
        "passes_by_era": _passes_by_era(scans),
        "documents_blocked": sum(1 for s in scans if s.blocking),
        # band -> [documents, documents with no basic unit]. Documents too short
        # to judge are absent from every band rather than pooled into the worst.
        "legibility_bands": _legibility_bands(scans),
        # verdict -> [documents, documents with no basic unit]. The bands score
        # one axis, so a clean list and a corrupted page share a band; only the
        # second is a quality problem, and only the split can tell them apart.
        "text_verdicts": _verdicts(scans),
        # Neither the bands nor the verdicts cover a document too short to score,
        # and both denominators exclude them. Reported so a rate over either is
        # read against what it actually measured.
        "documents_unjudged": sum(1 for s in scans if s.verdict is None),
    }


def _passes_by_era(scans: list[DocumentScan]) -> dict[str, dict[str, list[int]]]:
    by_era: dict[str, tuple[Counter[str], Counter[str], Counter[str]]] = {}
    for scan in scans:
        net, did, ran = by_era.setdefault(scan.era, (Counter(), Counter(), Counter()))
        net.update(scan.fires)
        did.update(k for k, v in scan.fires.items() if v)
        ran.update(scan.fires.keys())
    return {
        era: {k: [net[k], did[k], ran[k]] for k in sorted(ran)}
        for era, (net, did, ran) in sorted(by_era.items())
    }


def _verdicts(scans: list[DocumentScan]) -> dict[str, list[int]]:
    """Documents per verdict, and how many yielded no provision. Documents with
    no verdict are absent rather than pooled: too short to judge is not a
    finding about the text."""
    counted: dict[str, list[int]] = {}
    for scan in scans:
        if scan.verdict is None:
            continue
        row = counted.setdefault(scan.verdict, [0, 0])
        row[0] += 1
        # Gated on `measurable`, as the bands are: a class declaring no provision
        # level has no basic unit by construction, and counting that as a failure
        # to produce one would inflate the column the verdict is read for.
        row[1] += scan.measurable and not scan.basic_units
    return dict(sorted(counted.items()))


def _legibility_bands(scans: list[DocumentScan]) -> dict[str, list[int]]:
    banded: dict[str, list[int]] = {}
    for scan in scans:
        band = band_for(scan.legibility)
        if band is None:
            continue
        entry = banded.setdefault(band, [0, 0])
        entry[0] += 1
        if scan.measurable and not scan.basic_units:
            entry[1] += 1
    return {name: banded[name] for name, _ in BANDS if name in banded}


__all__ = [
    "CorpusSweep",
    "DocumentScan",
    "aggregate",
    "is_derived_export",
    "marker_form",
    "scan_corpus",
    "scan_file",
    "scan_text",
]
