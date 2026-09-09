"""Fixture-driven tests for clause-parity segmentation and gap detection."""

from __future__ import annotations

from codify.translate.clause_parity import check_clause_parity, count_clauses


class TestCountClauses:
    def test_english_semicolon_split(self) -> None:
        text = "The Minister shall publish; the operator shall pay; the fund shall verify."
        assert count_clauses(text, "eng") == 3

    def test_english_full_stops(self) -> None:
        text = (
            "The Minister publishes the report. The operator pays the fee. "
            "The fund verifies the record."
        )
        assert count_clauses(text, "eng") == 3

    def test_arabic_full_stop_and_semicolon(self) -> None:
        text = "يجب على الوزير نشر التقرير؛ على المشغل دفع الرسوم. على الصندوق التحقق من السجل."
        # Three clauses (two semicolon + two full stops in AR punctuation).
        assert count_clauses(text, "ara") >= 2

    def test_legal_abbrev_does_not_over_split(self) -> None:
        text = (
            "The Minister publishes the report (e.g. Article 3). "
            "The operator pays the fee. The fund verifies."
        )
        # "e.g." must not split; three clauses only.
        assert count_clauses(text, "eng") == 3

    def test_empty_text_zero(self) -> None:
        assert count_clauses("", "eng") == 0
        assert count_clauses("   ", "eng") == 0

    def test_unregistered_language_falls_back_to_english(self) -> None:
        text = "The Minister publishes; the operator pays; the fund verifies."
        assert count_clauses(text, "fra") == 3


class TestCheckClauseParity:
    def test_equal_counts_pass(self) -> None:
        source = "A; B; C; D; E."
        target = "A; B; C; D; E."
        # Both count as 1 since each segment is < 2 words; the count
        # falls to 1 both sides so ratio is 1.0.
        _, gap = check_clause_parity(source, target, language="eng")
        assert not gap

    def test_gap_fires_below_threshold(self) -> None:
        source = (
            "The Minister publishes the report. The operator pays the fee. "
            "The fund verifies the record. The court records the fine. "
            "The auditor signs the report."
        )
        # Target drops two clauses (2/5 = 40%, well below 80%).
        target = "The Minister publishes the report. The operator pays the fee."
        ratio, gap = check_clause_parity(source, target, language="eng")
        assert gap is True
        assert ratio < 0.80

    def test_at_threshold_no_gap(self) -> None:
        # 4/5 = 80%; equals threshold, no gap (strict less-than).
        source = (
            "The Minister publishes the report. The operator pays the fee. "
            "The fund verifies the record. The court records the fine. "
            "The auditor signs the report."
        )
        target = (
            "The Minister publishes the report. The operator pays the fee. "
            "The fund verifies the record. The court records the fine."
        )
        ratio, gap = check_clause_parity(source, target, language="eng")
        assert ratio == 0.80
        assert gap is False

    def test_restructure_merge_not_a_gap(self) -> None:
        # Two source clauses merged into one target clause with "and".
        # Absolute deficit is 1 which is below CLAUSE_PARITY_MIN_DEFICIT=2,
        # so the check clears legitimate 2-into-1 merges.
        source = "The Minister publishes the report. The operator pays the fee."
        target = "The Minister publishes the report and the operator pays the fee."
        _, gap = check_clause_parity(source, target, language="eng")
        assert gap is False

    def test_empty_source_no_gap(self) -> None:
        ratio, gap = check_clause_parity("", "anything", language="eng")
        assert ratio == 1.0
        assert gap is False

    def test_boundary_ratio_0_75_with_two_deficit_fires(self) -> None:
        # 6 clauses in source, 4 in target; ratio 0.667, deficit 2.
        # Below the 0.80 ratio floor AND at the min-deficit floor.
        source = (
            "Clause one is a full statement. Clause two is another statement. "
            "Clause three is yet another. Clause four is fourth in the list. "
            "Clause five is number five. Clause six is number six."
        )
        target = (
            "Clause one is a full statement. Clause two is another statement. "
            "Clause three is yet another. Clause four is fourth in the list."
        )
        ratio, gap = check_clause_parity(source, target, language="eng")
        assert ratio < 0.80
        assert gap is True

    def test_single_clause_deficit_does_not_fire(self) -> None:
        # 3 in source, 2 in target: ratio 0.67 (below threshold) but
        # deficit only 1, legitimate 2-into-1 merge, no flag.
        source = (
            "Clause one is a full statement. Clause two is another statement. "
            "Clause three is yet another."
        )
        target = "Clause one is a full statement. Clause two is another statement."
        _, gap = check_clause_parity(source, target, language="eng")
        assert gap is False
