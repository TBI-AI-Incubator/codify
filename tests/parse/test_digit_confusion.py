"""Synthetic digit sequences exercise Arabic-Indic substitution repair."""

from __future__ import annotations

from structlog.testing import capture_logs

from codify.pipeline.enrich.digit_confusion import (
    DigitRepair,
    repair_number_sequence,
)


class TestSyntheticSequence:
    """A damaged label in a consecutive synthetic sequence is recoverable."""

    def test_six_read_as_one(self) -> None:
        # Sequence: 4, 5, 1 (should be 6), 7, 8
        repaired, corrections = repair_number_sequence(["4", "5", "1", "7", "8"])
        assert repaired == ["4", "5", "6", "7", "8"]
        assert len(corrections) == 1
        assert corrections[0] == DigitRepair(
            index=2, from_num="1", to_num="6", reason=corrections[0].reason
        )
        assert "monotonic prior" in corrections[0].reason


class TestConfusionMatrix:
    """Each pair the matrix defines gets a shape test so a regression that
    drops one pair fails obviously."""

    def test_one_six_pair_multi_digit(self) -> None:
        # 62 is really 12 (1↔6 at position 0): 60, 61, 12, 63 → repair 12 → 62.
        repaired, corrections = repair_number_sequence(["60", "61", "12", "63"])
        assert repaired == ["60", "61", "62", "63"]
        assert corrections[0].to_num == "62"

    def test_zero_five_pair(self) -> None:
        # Sequence 4, 0, 6 → 5 fits between 4 and 6 via 0↔5.
        repaired, corrections = repair_number_sequence(["4", "0", "6"])
        assert repaired == ["4", "5", "6"]
        assert corrections[0].to_num == "5"

    def test_eight_nine_pair(self) -> None:
        # 8, 8, 10, middle 8 is really 9 (top-hook lost from 9).
        # The duplicate at i=1 (8 == prev) trips the anomaly gate; the
        # 9-candidate fits prev+1 and next-1.
        repaired, corrections = repair_number_sequence(["8", "8", "10"])
        assert repaired == ["8", "9", "10"]
        assert corrections[0].to_num == "9"

    def test_two_three_pair(self) -> None:
        # 2, 2, 4, middle 2 is really 3 (Naskh 2↔3). Duplicate breaks
        # monotonicity at i=1; 3 fits prev+1 and next-1.
        repaired, corrections = repair_number_sequence(["2", "2", "4"])
        assert repaired == ["2", "3", "4"]
        assert corrections[0].to_num == "3"


class TestMultiDigit:
    """Multi-digit article numbers (PP Amendment: cover 11, body 10) test
    the substitution across each digit position."""

    def test_single_position_multi_digit(self) -> None:
        # 61 sits between 10 and 12; 11 fits via 6↔1 at position 0.
        repaired, corrections = repair_number_sequence(["10", "61", "12"])
        assert repaired == ["10", "11", "12"]
        assert corrections[0].to_num == "11"

    def test_two_digit_leading_confusion(self) -> None:
        # Sequence 17, 68, 19 → 68 should be 18 (leading 1↔6).
        repaired, corrections = repair_number_sequence(["17", "68", "19"])
        assert repaired == ["17", "18", "19"]
        assert corrections[0].to_num == "18"


class TestCleanSequence:
    """A monotonic well-formed sequence must not be touched.
    Silent-modification of a clean sequence is worse than missing a repair."""

    def test_clean_short_sequence_untouched(self) -> None:
        repaired, corrections = repair_number_sequence(["1", "2", "3", "4", "5"])
        assert repaired == ["1", "2", "3", "4", "5"]
        assert corrections == []

    def test_clean_multi_digit_untouched(self) -> None:
        repaired, corrections = repair_number_sequence(["8", "9", "10", "11", "12"])
        assert repaired == ["8", "9", "10", "11", "12"]
        assert corrections == []

    def test_gap_of_two_untouched(self) -> None:
        """A gap of 2 is legitimate on renumbered statutes (article was
        deleted or reserved). Do not silently fill it."""
        repaired, corrections = repair_number_sequence(["5", "7", "8"])
        assert repaired == ["5", "7", "8"]
        assert corrections == []


class TestNonIntegerAnchors:
    """Roman numerals, ordinal words, and single-letter anchors must be
    left alone; the heuristic only handles digit strings."""

    def test_roman_numeral_skipped(self) -> None:
        # Chapter labelled roman IV sits between article 5 and article 7;
        # the repair anchor is the last accepted digit-only prior.
        repaired, corrections = repair_number_sequence(["5", "IV", "7"])
        assert repaired == ["5", "IV", "7"]
        assert corrections == []

    def test_ordinal_word_skipped(self) -> None:
        repaired, corrections = repair_number_sequence(["1", "الأول", "3"])
        assert repaired == ["1", "الأول", "3"]
        assert corrections == []

    def test_letter_suffix_number_skipped(self) -> None:
        """`5A` doesn't parse as base-10; leave it alone even if
        neighbouring numbers look broken."""
        repaired, corrections = repair_number_sequence(["4", "5A", "6"])
        assert repaired == ["4", "5A", "6"]
        assert corrections == []

    def test_none_entries_skipped(self) -> None:
        repaired, corrections = repair_number_sequence(["4", None, "1", "7"])
        assert repaired[1] is None
        # The 1 sits after 4 with next 7; repair to 6 or nothing.
        assert repaired[2] in {"1", "6"}


class TestArabicIndicInput:
    """Scoring runs on the ASCII fold, but the write-back matches each
    item's own source script; eId ASCII-purity is guaranteed downstream
    by `_normalise_number` and `normalise_eid_digits`."""

    def test_arabic_indic_folds_before_scoring(self) -> None:
        # Arabic-Indic 1 (١) after Latin 5 → the repair still fires, and
        # the corrected number keeps the item's Arabic-Indic script.
        repaired, corrections = repair_number_sequence(["4", "5", "١", "7"])
        assert repaired[2] == "٦"
        assert corrections[0].from_num == "١"
        assert corrections[0].to_num == "٦"


class TestConfidenceMargin:
    """The repair only fires when a candidate beats the observed number by
    a comfortable score margin; a marginal case does not silently rewrite."""

    def test_gap_of_two_leaves_sequence_untouched(self) -> None:
        """Prev+2 sits in the +3 score bucket; no single-digit neighbour
        can beat it by the margin threshold. A clean gap of 2 (reserved
        or renumbered article) must therefore pass through unchanged."""
        input_seq = ["5", "7", "8"]
        repaired, corrections = repair_number_sequence(input_seq)
        assert repaired == input_seq
        assert corrections == []

    def test_gap_of_six_in_valid_sequence_untouched(self) -> None:
        """Copilot flagged the false-positive case: `10, 16, 18` is a
        legitimate increasing sequence (reserved articles 11-15), but a
        naive score check would trigger repair to `10, 11, 18` via the
        1↔6 pair since candidate `11` scores 15 vs `16`'s score of 3.
        The anomaly predicate must gate on strict non-monotonicity or a
        clear over-step, not on any sub-optimal score."""
        input_seq = ["10", "16", "18"]
        repaired, corrections = repair_number_sequence(input_seq)
        assert repaired == input_seq
        assert corrections == []

    def test_a_forward_jump_past_the_ceiling_is_considered_then_declined(self) -> None:
        """A jump past the anomaly ceiling (default 20) is suspicious enough
        to cost a candidate search, and the search can still come back empty.

        `68` where `18` belongs is a synthetic digit confusion, but the
        neighbour here is `9`: the only substitutions of `68` are `18` and
        `69`, both of which overshoot `9` and so score at the monotonicity
        floor, the same as `68` itself. Nothing beats the observed value and
        nothing is rewritten. Reaching `8` would mean dropping a digit, which
        is not a substitution and so is never in the candidate set.

        An untouched sequence is also what a detector that never fired
        returns, so the sequence alone cannot show the anomaly was noticed.
        The log line is the only evidence of that, so it is what is asserted.
        """
        with capture_logs() as logs:
            repaired, corrections = repair_number_sequence(["7", "68", "9"])
        assert repaired == ["7", "68", "9"]
        assert corrections == []
        assert any(
            log["event"] == "digit_run_anomaly_unrepaired" and log["number"] == "68" for log in logs
        )


def test_repair_preserves_arabic_indic_script() -> None:
    """A repaired Arabic-Indic number stays Arabic-Indic: ArbReg 39/2004's
    OCR emitted مادة (١) between ٥ and ٧, and the repair must write ٦, not
    a lone ASCII 6 inside an Arabic sequence."""
    nums = ["٥", "١", "٧"]
    repaired, corrections = repair_number_sequence(nums)
    assert repaired == ["٥", "٦", "٧"], repaired
    assert corrections and corrections[0].to_num == "٦"


class TestShiftedRunRepair:
    """A consistently-misread run (٦١..٦٩ → ١١..١٩) defeats the
    per-anchor pass; the run-level hypothesis pass repairs it whole."""

    def test_arb_reg_shifted_run_repaired(self) -> None:
        nums = [str(n) for n in (58, 59, 60)] + [str(n) for n in range(11, 20)] + ["70"]
        repaired, corrections = repair_number_sequence(nums)
        assert repaired == [str(n) for n in range(58, 71)]
        assert all("run shift" in c.reason for c in corrections)
        assert len(corrections) == 9

    def test_run_repair_preserves_source_script(self) -> None:
        repaired, _ = repair_number_sequence(["٥٩", "٦٠", "١١", "١٢", "١٣", "٧٠"])
        assert repaired == ["٥٩", "٦٠", "٦١", "٦٢", "٦٣", "٧٠"]

    def test_run_to_document_end_repaired(self) -> None:
        repaired, _ = repair_number_sequence(["58", "59", "60", "11", "12"])
        assert repaired == ["58", "59", "60", "61", "62"]

    def test_clean_sequence_zero_false_repairs(self) -> None:
        clean = [str(n) for n in range(1, 40)]
        repaired, corrections = repair_number_sequence(clean)
        assert repaired == clean
        assert corrections == []

    def test_unreachable_restart_left_alone(self) -> None:
        # A legitimate renumbering restart with no confusion-matrix path
        # from expected (61) to observed (21): 2<->6 is not a pair.
        nums = ["58", "59", "60", "21", "22", "23"]
        repaired, corrections = repair_number_sequence(nums)
        assert repaired == nums
        assert corrections == []

    def test_trailing_trusted_anchor_not_swallowed(self) -> None:
        # 70 ascends past 19 but does not carry the hypothesised digit;
        # it must close the run, not join it.
        nums = ["60", "11", "12", "70"]
        repaired, _ = repair_number_sequence(nums)
        assert repaired == ["60", "61", "62", "70"]


class TestShiftedRunHoles:
    """The run pass works through an index indirection over parseable numbers;
    holes (None, ordinal words) must not shift which anchor gets rewritten."""

    def test_none_hole_inside_run(self) -> None:
        nums = ["58", "59", "60", "11", None, "12", "13", "70"]
        repaired, corrections = repair_number_sequence(nums)
        assert repaired == ["58", "59", "60", "61", None, "62", "63", "70"]
        assert [c.index for c in corrections] == [3, 5, 6]

    def test_ordinal_word_hole_inside_run(self) -> None:
        nums = ["59", "60", "11", "الأول", "12", "70"]
        repaired, _ = repair_number_sequence(nums)
        assert repaired == ["59", "60", "61", "الأول", "62", "70"]


class TestAnomalyIsNotEvidence:
    """A number the pass declined to repair must not become the prior.

    Fitting what follows to the number it breaks reads the one damaged heading
    as the truth and the run correcting it as the damage. That failure is
    silent: articles keep their text and change their numbers, so a citation
    resolves to the wrong provision. Measured on the corpus at 140 articles
    across 8 versions before this guard.
    """

    def test_a_declined_anomaly_does_not_renumber_the_run_after_it(self) -> None:
        # One corpus statute renumbered 133 of its 621 articles this way.
        repaired, _ = repair_number_sequence(["358", "359", "260", "361", "362", "363"])

        # The damaged 260 is corrected to 360; the correct run is left alone.
        assert repaired == ["358", "359", "360", "361", "362", "363"]

    def test_a_run_bracketed_by_two_trusted_numbers_is_still_repaired(self) -> None:
        """The case the pass exists for, which the guard must not cost."""
        repaired, corrections = repair_number_sequence(["59", "60", "11", "12", "13", "14", "70"])

        assert repaired == ["59", "60", "61", "62", "63", "64", "70"]
        assert len(corrections) == 4

    def test_a_substitution_must_land_where_the_sequence_expects_it(self) -> None:
        """Clearing the floor is not evidence.

        Scoring credits any candidate below the next number, so in a document
        numbered 10 to 16 a gazette number could be rewritten to another number
        the document does not hold, purely for sitting lower. Four corpus
        versions carried `401 -> 301` on that reasoning.
        """
        numbers = ["10", "11", "12", "401", "401", "13", "14"]

        repaired, corrections = repair_number_sequence(numbers)

        assert repaired == numbers
        assert corrections == []

    def test_a_repair_still_fires_where_the_candidate_continues_the_prior(self) -> None:
        repaired, corrections = repair_number_sequence(["10", "11", "13", "13"])

        assert repaired == ["10", "11", "13", "14"]
        assert len(corrections) == 1

    def test_a_declined_number_is_not_the_prior_for_the_next_one_either(self) -> None:
        """The per-anchor walk carries the same rule as the run pass.

        In a descending run every number is anomalous, so each would be fitted
        to the one before it: `11` becomes `16` to follow a `12` the pass had
        already refused to trust. Reading backwards is a reason to repair
        nothing, not licence to invent a number the document does not hold.
        """
        numbers = ["13", "12", "11"]

        repaired, corrections = repair_number_sequence(numbers)

        assert repaired == numbers
        assert corrections == []

    def test_a_tail_error_after_a_declined_number_is_left_alone(self) -> None:
        """The cost of the guard, pinned so it is not quietly undone.

        `150` is a real legislative skip the pass rightly declines, and `651` is
        a genuine `1`/`6` flip of `151` with nothing after it to confirm a
        candidate. Repairing it would mean trusting the declined number, which
        is the reasoning that renumbered 133 articles of one statute. Nothing
        distinguishes "declined because sound" from "declined because damaged",
        so both are refused: this pass may now miss a tail error, and it no
        longer invents one.
        """
        numbers = ["99", "100", "150", "651"]

        repaired, corrections = repair_number_sequence(numbers)

        assert repaired == numbers
        assert corrections == []

    def test_an_untrusted_prior_does_not_license_repairing_a_sound_number(self) -> None:
        """Abstaining is not the same as removing the veto.

        With no trusted prior the scoring's prior branch contributes nothing, so
        the observed value scores at the floor and any candidate continuing the
        corrupted neighbour ahead of it wins. That rewrites the sound number
        rather than leaving it: here the second `60`, which the run after it
        agrees with, would become `10`.
        """
        numbers = ["12", "60", "60", "11", "12", "13"]

        repaired, corrections = repair_number_sequence(numbers)

        assert repaired == numbers
        assert corrections == []

    def test_no_repair_may_emit_a_descending_pair(self) -> None:
        """The same hole, reached from the other side.

        A repeated number followed by nothing to confirm a candidate would take
        `13` down to `12`, emitting a descent the pass exists to remove.
        """
        numbers = ["70", "13", "13", "13"]

        repaired, _ = repair_number_sequence(numbers)

        assert repaired == numbers
