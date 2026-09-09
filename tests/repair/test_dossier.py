"""Dossier assembly, deterministic, offline, and never a model call."""

from __future__ import annotations

from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.ocr import PageResult, combine_page_texts
from codify.repair.dossier import (
    SCHEMA_VERSION,
    DossierInputs,
    PageEvidence,
    PageReadInput,
    assemble_dossier,
    classify_coverage,
    sha256_text,
)


def _akn() -> str:
    bb = "BODY\n  SECTION 1\n    ARTICLE 1\n      Body of article one.\n    ARTICLE 2\n"
    return parse_to_akn(bb, country="gb", doctype="act", number="1", date="2020-01-01")


def _reads() -> list[PageReadInput]:
    return [
        PageReadInput(
            page_number=1,
            engine="mistral_ocr",
            text="SECTION 1\nARTICLE 1\nBody of article one.",
            metrics={"verdict": "accept", "reasons": []},
        ),
        PageReadInput(
            page_number=2,
            engine="vision_ocr",
            text="ARTICLE 2\nThe second article's body.",
            rival_text="ARTICLE 2 The second articles body",
            divergence=0.2,
            metrics={"verdict": "reread", "reasons": ["divergence:high"]},
        ),
    ]


def test_assembly_rebuilds_text_and_spans_from_page_reads() -> None:
    reads = _reads()
    combined = combine_page_texts(
        [PageResult(page_number=r.page_number, text=r.text, method=r.engine) for r in reads]
    )
    dossier, doc_text = assemble_dossier(
        DossierInputs(
            version_id="v1",
            akn_xml=_akn(),
            country="gb",
            page_reads=reads,
            stored_source_text=combined,
            page_evidence_source="page_reads",
        )
    )
    assert doc_text == combined
    assert not dossier.source_text_mismatch
    assert [s["page"] for s in dossier.spans] == [1, 2]
    assert dossier.schema_version == SCHEMA_VERSION
    assert dossier.akn_sha256 == sha256_text(_akn())
    assert dossier.source_text_sha256 == sha256_text(combined)


def test_drifted_stored_text_is_flagged_not_fatal() -> None:
    dossier, _ = assemble_dossier(
        DossierInputs(
            version_id="v1",
            akn_xml=_akn(),
            country="gb",
            page_reads=_reads(),
            stored_source_text="something else entirely",
            page_evidence_source="page_reads",
        )
    )
    assert dossier.source_text_mismatch


def test_outline_findings_and_page_summaries_are_carried() -> None:
    dossier, _ = assemble_dossier(
        DossierInputs(
            version_id="v1",
            akn_xml=_akn(),
            country="gb",
            page_reads=_reads(),
            page_evidence_source="page_reads",
        )
    )
    assert any(n.tag == "article" for n in dossier.outline)
    assert any(f["check"] == "empty_article" for f in dossier.findings)
    page2 = next(p for p in dossier.pages if p.page == 2)
    assert page2.verdict == "reread"
    assert page2.divergence == 0.2


def test_no_page_evidence_uses_fallback_and_records_source() -> None:
    dossier, doc_text = assemble_dossier(
        DossierInputs(
            version_id="v1",
            akn_xml=_akn(),
            country="gb",
            fallback_text="flat text",
            fallback_spans=[{"page": 1, "method": "x", "start": 0, "end": 9}],
            page_evidence_source="artifact",
        )
    )
    assert doc_text == "flat text"
    assert dossier.page_evidence_source == "artifact"
    assert dossier.pages == []


def test_coverage_separates_confirmed_suspected_unresolved() -> None:
    findings = [
        # swallowed_enumerator became actionable when Split was wired.
        {"check": "swallowed_enumerator", "eid": "art_2", "severity": "warning", "message": ""},
        {"check": "ref_resolution", "eid": "art_3", "severity": "info", "message": ""},
        {"check": "section_numbering", "severity": "info", "message": "no eid here"},
    ]
    pages = [
        PageEvidence(page=1, verdict="accept"),
        PageEvidence(page=2, verdict="escalate", reasons=["chars_per_ink:low"]),
    ]
    entries = classify_coverage(findings, pages)
    by_status = {e.status: e for e in entries}
    assert by_status["confirmed"].eid == "art_2"  # actionable op exists
    assert by_status["suspected"].kind in {"ref_resolution", "page_read"}
    assert by_status["unresolved"].reason == "no target element"
    assert any(e.kind == "page_read" and e.page == 2 for e in entries)


def test_summary_excludes_bulk_but_keeps_identity() -> None:
    dossier, _ = assemble_dossier(
        DossierInputs(
            version_id="v1",
            akn_xml=_akn(),
            country="gb",
            page_reads=_reads(),
            page_evidence_source="page_reads",
        )
    )
    summary = dossier.summary()
    assert "spans" not in summary
    assert "eid_page" not in summary
    assert summary["akn_sha256"] == dossier.akn_sha256
    assert summary["coverage"], "assessment must ride the artifact"


def test_defect_class_gap_classifies_suspected_with_the_upstream_reason() -> None:
    findings = [
        {
            "check": "number_gap",
            "eid": "art_1",
            "likely": "defect",
            "severity": "warning",
            "message": "",
        },
        {
            "check": "number_gap",
            "eid": "art_9",
            "likely": "repeal",
            "severity": "info",
            "message": "",
        },
    ]
    entries = classify_coverage(findings, [])
    by_eid = {e.eid: e for e in entries}
    assert by_eid["art_1"].status == "suspected"
    assert "upstream" in by_eid["art_1"].reason
    assert by_eid["art_9"].status == "confirmed"  # repeal-class gaps stay repairable
