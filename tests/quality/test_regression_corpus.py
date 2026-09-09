"""Synthetic containment records distinguish unread documents from zero firings."""

from __future__ import annotations

import json

from codify.quality.regression_corpus import (
    MANIFEST,
    check_containment,
    doc_id_of,
    load_manifest,
)

# The passes put up for deletion. The manifest must answer for each.
AUDITED = frozenset(
    {
        "recover_container_twin",
        "promote_orphan_children",
        "numbered_heading_fallback",
        "declare_toc_without_body",
    }
)

# Synthetic readable record with the character count pinned by the manifest.
READ_SAMPLE = {"C90-101": 1200}


def _broken(tmp_path, mutate):
    """A manifest with one claim rewritten, so a defect can be exercised without
    breaking the shipped one."""
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


class TestTheManifest:
    def test_it_answers_for_every_pass_up_for_deletion(self) -> None:
        _, passes = load_manifest()
        assert set(passes) == AUDITED

    def test_every_named_document_exists(self) -> None:
        documents, passes = load_manifest()
        named = {d for claim in passes.values() for d in claim.motivated_by}
        assert named <= set(documents)

    def test_a_pass_with_no_motivating_document_says_so_rather_than_staying_silent(
        self,
    ) -> None:
        """`declare_toc_without_body` raises a declaration rather than recovering
        a document, so nobody could name one. Recording that is the point: an
        empty `motivated_by` and an explicit "none exists" read identically to a
        deletion script and mean opposite things."""
        _, passes = load_manifest()
        claim = passes["declare_toc_without_body"]
        assert claim.motivated_by == ()
        assert len(claim.no_motivating_document) > 40

    def test_a_deleted_pass_keeps_its_evidence_and_stops_being_demanded(self) -> None:
        """`recover_container_twin` is gone from the scanner. Its entry stays so
        the evidence survives the code, but the gate must not ask for it again:
        a permanently red gate is one somebody switches off."""
        report = check_containment({})
        assert "recover_container_twin" in report.deleted
        assert "recover_container_twin" not in report.unevidenced
        assert report.ok is True


class TestContainment:
    def test_a_sweep_that_read_the_document_may_judge_its_pass(self, tmp_path) -> None:
        undeleted = _broken(
            tmp_path,
            lambda raw: raw["passes"]["recover_container_twin"].pop("deleted"),
        )
        report = check_containment(READ_SAMPLE, manifest=undeleted)
        assert report.may_delete("recover_container_twin")

    def test_a_sweep_that_missed_it_may_not(self, tmp_path) -> None:
        """Zero firings cannot certify a pass whose motivating record was omitted."""
        undeleted = _broken(
            tmp_path,
            lambda raw: raw["passes"]["recover_container_twin"].pop("deleted"),
        )
        report = check_containment({}, manifest=undeleted)
        assert not report.may_delete("recover_container_twin")
        assert "C90-101" in report.unevidenced["recover_container_twin"]
        assert report.ok is False

    def test_a_filename_is_not_a_document(self, tmp_path) -> None:
        """An empty stub sitting at the right path is not the document."""
        undeleted = _broken(
            tmp_path,
            lambda raw: raw["passes"]["recover_container_twin"].pop("deleted"),
        )
        report = check_containment({"C90-101": 0}, manifest=undeleted)
        assert not report.may_delete("recover_container_twin")

    def test_a_truncated_extraction_is_not_the_document_either(self, tmp_path) -> None:
        """Any positive character count used to certify. A parser that returns
        the cover page and stops reads as a full document, and the pass it was
        meant to be judged against never saw the body."""
        undeleted = _broken(
            tmp_path,
            lambda raw: raw["passes"]["recover_container_twin"].pop("deleted"),
        )
        half = check_containment({"C90-101": 500}, manifest=undeleted)
        assert not half.may_delete("recover_container_twin")
        assert "truncated, 500 of 1200" in half.unevidenced["recover_container_twin"]
        # Extraction wobbles a little between parser versions; the floor is
        # completeness, not equality.
        near = check_containment({"C90-101": 1100}, manifest=undeleted)
        assert near.may_delete("recover_container_twin")

    def test_reading_one_of_two_motivating_documents_is_not_enough(self, tmp_path) -> None:
        """`numbered_heading_fallback` names two. Judging it on whichever one the
        sweep happened to reach hides the other, and the hidden one is the one
        nobody looked at."""
        both = _broken(
            tmp_path,
            lambda raw: (
                raw["documents"].append(
                    {
                        "doc_id": "C90-105",
                        "title": "a second motivating document",
                        "year": 2042,
                        "era": "synthetic",
                        "source_sha256": "f" * 64,
                        "text_status": "text_layer",
                        "source_chars": 1000,
                    }
                )
                or raw["passes"].update(
                    {"promote_orphan_children": {"motivated_by": ["C90-101", "C90-105"]}}
                )
            ),
        )
        report = check_containment(READ_SAMPLE, manifest=both)
        assert not report.may_delete("promote_orphan_children")
        assert "C90-105" in report.unevidenced["promote_orphan_children"]
        assert "C90-101" not in report.unevidenced["promote_orphan_children"]

    def test_a_document_no_sweep_can_reach_is_not_the_sweeps_fault(self) -> None:
        """Both documents for `numbered_heading_fallback` are image-only scans,
        one of them absent from the bundle entirely. No run of any corpus will
        change that, so blaming the sweep would leave the gate red forever and
        teach its reader to pass it."""
        report = check_containment(READ_SAMPLE)
        assert "numbered_heading_fallback" in report.unevidenceable
        assert "numbered_heading_fallback" not in report.unevidenced
        assert not report.may_delete("numbered_heading_fallback")
        assert report.ok is True

    def test_a_pass_the_manifest_never_heard_of_is_named(self) -> None:
        """The gate's own blind spot. Two passes fire zero times corpus-wide and
        are absent from the manifest; without this they are certified by silence
        and the next deletion repeats the mistake one pass over."""
        report = check_containment(READ_SAMPLE, zero_firing_passes=("ordinal_list_fallback",))
        assert "ordinal_list_fallback" in report.unanswered
        assert report.ok is False

    def test_a_pass_nobody_can_name_a_document_for_is_not_reported_as_evidenced(
        self,
    ) -> None:
        report = check_containment(READ_SAMPLE)
        assert "declare_toc_without_body" in report.undocumented
        assert not report.may_delete("declare_toc_without_body")

    def test_an_incoherent_manifest_fails_rather_than_certifying_nothing(self, tmp_path) -> None:
        """A pass that claims neither a document nor a reason would otherwise
        vanish from every bucket and read as "no objection"."""
        broken = _broken(
            tmp_path, lambda raw: raw["passes"].update({"promote_orphan_children": {}})
        )
        report = check_containment(READ_SAMPLE, manifest=broken)
        assert report.ok is False
        assert any("promote_orphan_children" in defect for defect in report.incoherent)

    def test_a_document_id_the_manifest_does_not_define_is_a_defect_not_a_miss(
        self, tmp_path
    ) -> None:
        """A typo in `motivated_by` would otherwise degrade quietly into "not
        scanned", sending someone to re-run a sweep that can never satisfy it."""
        broken = _broken(
            tmp_path,
            lambda raw: raw["passes"].update(
                {"promote_orphan_children": {"motivated_by": ["C99-998"]}}
            ),
        )
        report = check_containment(READ_SAMPLE, manifest=broken)
        assert any("C99-998" in defect for defect in report.incoherent)
        assert "promote_orphan_children" not in report.unevidenced


class TestDocIds:
    def test_it_reads_the_id_off_a_bundle_filename(self) -> None:
        assert doc_id_of("C90_samples/C90-101 - synthetic-observatory.pdf") == "C90-101"

    def test_a_file_outside_the_bundle_naming_scheme_has_no_id(self) -> None:
        """Returning "" rather than guessing keeps an unrecognised file out of
        the read set, so it can never silently satisfy a containment claim."""
        assert doc_id_of("scratch/notes.txt") == ""
        assert doc_id_of("Chapter One.txt") == ""


class TestPdfExtraction:
    """A sweep that reads PDFs has three outcomes, and they are not the same."""

    def test_a_file_the_parser_rejects_is_unreadable_not_image_only(self, tmp_path) -> None:
        """Folding a parse failure into the image-only count hides a corpus
        defect among documents nothing was ever going to read."""
        from codify.quality.corpus_scan import _pdf_text

        broken = tmp_path / "truncated.pdf"
        broken.write_bytes(b"%PDF-1.4\nnot actually a pdf")
        assert _pdf_text(broken) is None

    def test_an_image_only_scan_reads_as_empty_rather_than_broken(self, tmp_path) -> None:
        from pypdf import PdfWriter

        from codify.quality.corpus_scan import _pdf_text

        blank = tmp_path / "scan.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with blank.open("wb") as handle:
            writer.write(handle)
        assert _pdf_text(blank) == ""
