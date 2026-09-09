"""Define which documents provide evidence for removable scan passes."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST = Path(__file__).with_name("regression_corpus.json")

# A text-layer sweep cannot evidence image-only documents handled by OCR.
READABLE = "text_layer"

# Allow minor parser differences while rejecting truncated extraction.
MIN_EXTRACTION_RATIO = 0.9


@dataclass(frozen=True)
class CorpusDocument:
    """One document the manifest pins, by hash rather than by content."""

    doc_id: str
    title: str
    year: int | None
    era: str
    # Hash of the source as the corpus holds it. Empty when the manifest pins
    # only a stored version, which is a different manifestation of the same law.
    source_sha256: str = ""
    # Optional stored-version identity when supplied by a private corpus manifest.
    version_id: str = ""
    version_source_sha256: str = ""
    text_status: str = READABLE
    # Characters the pinned extraction yielded. A sweep that observes far fewer
    # read a truncated document, which is not the document.
    source_chars: int = 0
    note: str = ""

    @property
    def readable(self) -> bool:
        return self.text_status == READABLE

    @property
    def reachable(self) -> bool:
        """Whether a text-layer sweep can read this document."""
        return bool(self.source_sha256) and self.readable


@dataclass(frozen=True)
class PassEvidence:
    """What the manifest claims about one pass's motivating documents."""

    motivated_by: tuple[str, ...] = ()
    # Why no document is named. Present and non-empty is a complete answer;
    # absent with no `motivated_by` is an unanswered question.
    no_motivating_document: str = ""
    # The evidence a pass was removed on. A deleted pass keeps its entry so the record
    # survives but stops being demanded of the scanner, since the gate would otherwise
    # go permanently red for a pass that is gone, and a permanently red gate gets
    # switched off.
    deleted: str = ""


@dataclass(frozen=True)
class ContainmentReport:
    """Which passes a sweep is entitled to draw a conclusion about."""

    # Pass -> the motivating documents the sweep actually read.
    evidenced: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Pass -> why the sweep failed to read a document it could have read.
    unevidenced: dict[str, str] = field(default_factory=dict)
    # Pass -> why no sweep of this corpus can reach its documents. Recorded, not
    # blamed on the sweep: the answer is the same however carefully it is run.
    unevidenceable: dict[str, str] = field(default_factory=dict)
    # Pass -> the recorded reason no document motivates it.
    undocumented: dict[str, str] = field(default_factory=dict)
    # Pass -> the evidence it was already deleted on.
    deleted: dict[str, str] = field(default_factory=dict)
    # Passes the sweep measured that the manifest says nothing about. The gate's
    # own blind spot: it can only speak for what it was told to cover, and a pass
    # outside it reports zero firings with nothing objecting.
    unanswered: tuple[str, ...] = ()
    # Manifest defects: a pass with neither claim, or a dangling doc id.
    incoherent: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when no pass was mis-measured: the manifest is coherent, covers everything the
        sweep measured, and every pass it speaks for had its document read.

        Not "everything may be deleted". A pass in `undocumented` is decided and the answer is
        no, permanently, so folding that into a failure would leave the gate red forever;
        `may_delete` is the per-pass authority.
        """
        return not self.incoherent and not self.unevidenced and not self.unanswered

    def may_delete(self, pass_name: str) -> bool:
        """Whether a zero firing count for this pass is evidence of anything."""
        return pass_name in self.evidenced


def load_manifest(
    path: Path | None = None,
) -> tuple[dict[str, CorpusDocument], dict[str, PassEvidence]]:
    """The pinned documents by id, and the per-pass evidence claims."""
    raw: dict[str, Any] = json.loads((path or MANIFEST).read_text(encoding="utf-8"))
    documents = {
        d["doc_id"]: CorpusDocument(
            doc_id=d["doc_id"],
            title=d["title"],
            year=d.get("year"),
            era=d.get("era", "unknown"),
            source_sha256=d.get("source_sha256", ""),
            version_id=d.get("version_id", ""),
            version_source_sha256=d.get("version_source_sha256", ""),
            text_status=d.get("text_status", READABLE),
            source_chars=int(d.get("source_chars", 0)),
            note=d.get("note", ""),
        )
        for d in raw["documents"]
    }
    passes = {
        name: PassEvidence(
            motivated_by=tuple(claim.get("motivated_by", ())),
            no_motivating_document=claim.get("no_motivating_document", ""),
            deleted=claim.get("deleted", ""),
        )
        for name, claim in raw["passes"].items()
    }
    return documents, passes


def _was_read(doc: CorpusDocument, documents_read: Mapping[str, int]) -> bool:
    """Whether the sweep read enough text to judge the document."""
    if not doc.reachable:
        return False
    observed = documents_read.get(doc.doc_id, 0)
    if doc.source_chars:
        return observed >= doc.source_chars * MIN_EXTRACTION_RATIO
    return observed > 0


def _why_unread(doc: CorpusDocument, documents_read: Mapping[str, int]) -> str:
    """Explain why a motivating document did not count."""
    if not doc.source_sha256:
        return "not in the bundle"
    if not doc.readable:
        return doc.text_status
    observed = documents_read.get(doc.doc_id)
    if observed is None:
        return "not scanned"
    if not observed:
        return "scanned but empty"
    return f"truncated, {observed} of {doc.source_chars} characters"


def check_containment(
    documents_read: Mapping[str, int],
    *,
    zero_firing_passes: Collection[str] = (),
    manifest: Path | None = None,
) -> ContainmentReport:
    """Sort every pass by whether the sweep is entitled to judge it.

    ``documents_read`` maps each corpus id the sweep scanned to the characters observed in
    it. Characters rather than presence, because a filename is not evidence: a truncated
    extraction, an empty stub and a page of OCR ruin all sit at the right path and none of
    them read the document.

    ``zero_firing_passes`` is what this sweep measured at zero, the only population a
    deletion is argued from. Anything in it the manifest does not answer for is reported,
    since a gate speaking only for a hand-kept subset certifies the rest by silence.
    """
    documents, passes = load_manifest(manifest)
    evidenced: dict[str, tuple[str, ...]] = {}
    unevidenced: dict[str, str] = {}
    undocumented: dict[str, str] = {}
    unevidenceable: dict[str, str] = {}
    deleted: dict[str, str] = {}
    incoherent: list[str] = []

    for name, claim in sorted(passes.items()):
        if claim.deleted:
            deleted[name] = claim.deleted
            continue
        if claim.no_motivating_document:
            if claim.motivated_by:
                incoherent.append(f"{name}: claims both a motivating document and none")
                continue
            undocumented[name] = claim.no_motivating_document
            continue
        if not claim.motivated_by:
            incoherent.append(f"{name}: names no motivating document and gives no reason")
            continue

        dangling = [d for d in claim.motivated_by if d not in documents]
        if dangling:
            incoherent.append(
                f"{name}: names documents absent from the manifest: {', '.join(dangling)}"
            )
            continue

        # Every motivating document, not any one of them. A pass motivated by
        # two documents and judged on one is judged on half its evidence, and
        # the half that was missed is the half nobody looked at.
        unread = tuple(d for d in claim.motivated_by if not _was_read(documents[d], documents_read))
        if not unread:
            evidenced[name] = claim.motivated_by
            continue
        why = "; ".join(f"{d} {_why_unread(documents[d], documents_read)}" for d in unread)
        # A sweep that fell short can be re-run; a document no sweep can reach
        # cannot. Blaming the second on the sweep leaves the gate red on every
        # corpus forever, which is how a gate stops being read.
        if any(documents[d].reachable for d in unread):
            unevidenced[name] = why
        else:
            unevidenceable[name] = why

    unanswered = tuple(sorted(set(zero_firing_passes) - set(passes)))

    return ContainmentReport(
        evidenced=evidenced,
        unevidenced=unevidenced,
        unevidenceable=unevidenceable,
        undocumented=undocumented,
        deleted=deleted,
        unanswered=unanswered,
        incoherent=tuple(incoherent),
    )


def doc_id_of(path: str) -> str:
    """The corpus id a source filename carries, else "". The bundle names every file
    `<cluster>-<number> - <title>.<ext>`, and that prefix is the only stable handle: titles
    carry orthographic variants and the directory layout differs between the PDF and
    derived-text halves.
    """
    stem = Path(path).name
    head = stem.split(" ", 1)[0]
    if len(head) > 3 and head[0] == "C" and "-" in head and head[1:].replace("-", "").isdigit():
        return head
    return ""


__all__ = [
    "MANIFEST",
    "READABLE",
    "ContainmentReport",
    "CorpusDocument",
    "PassEvidence",
    "check_containment",
    "doc_id_of",
    "load_manifest",
]
