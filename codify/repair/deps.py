"""The per-finding workspace both the agent and its tools depend on."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codify.repair.edit_ops import SourceEvidence


@dataclass
class RepairDeps:
    """Serialisable per-finding workspace. Carries the current document and its
    combined source text, the same payloads the workflow already moves through
    durable steps, so a recovered run resumes with identical evidence."""

    finding: dict[str, object]
    eid: str
    subtree_xml: str  # the flagged element's AKN fragment
    source_text: str  # OCR'd text of the element's source page
    page_no: int | None
    object_key: str
    country: str
    doctype: str = "act"
    version_id: str = ""
    akn_xml: str = ""  # the whole current document (finding-start snapshot)
    doc_text: str = ""  # combined source text, page spans in `spans`
    rival_text: str = ""  # the other engine's read of the focus page
    spans: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    # Every page the dossier has evidence for, including empty-text reads whose
    # only evidence is the scan itself. The bound for page tools.
    page_nos: list[int] = field(default_factory=list)
    source_sha256: str = ""  # the dossier's combined-text pin
    source_text_mismatch: bool = False
    closing_phrases: list[str] = field(default_factory=list)
    # The ONE field tools may write. preview_plan records its last accepted
    # plan here ("accepted_plan_json") so the loop can salvage proven work when
    # the usage budget dies mid-run. Lost on DBOS recovery (deps deserialise to
    # a fresh copy), which only costs the salvage, never correctness.
    preview_state: dict[str, Any] = field(default_factory=dict)

    def source_evidence(self) -> SourceEvidence:
        return SourceEvidence(
            text=self.doc_text,
            sha256=self.source_sha256,
            closing_phrases=list(self.closing_phrases),
        )

    def evidence_window(self) -> str:
        """Grounding evidence: the focus page plus its neighbours, and the rival
        read. Wide enough for text continuing overleaf, narrow enough that the
        fabrication floor still distinguishes this provision's source from words
        occurring anywhere in the act. Whole document when no page is mapped."""
        if self.page_no is None or not self.spans:
            return f"{self.doc_text or self.source_text}\n{self.rival_text}"
        want = {self.page_no - 1, self.page_no, self.page_no + 1}
        parts = [self.doc_text[s["start"] : s["end"]] for s in self.spans if s.get("page") in want]
        return "\n".join([*parts, self.rival_text])
