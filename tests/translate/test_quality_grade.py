"""Threshold-branch tests for the composite quality-grade composer."""

from __future__ import annotations

from codify.translate.quality_grade import translation_quality_grade


def _clean_audit() -> dict:
    """Baseline audit that would grade A."""
    return {
        "numeric_slot_recall": 1.0,
        "bodies_fallback_to_source": 0,
        "units_total": 100,
        "notes_status": "complete",
        "register_drifted": False,
        "clause_gaps_by_eid": {},
        "header_bleed_hits": {},
        "judge_verdicts": {},
        "judge_sample_size": 0,
        "judge_failed_count": 0,
        "exemplar_pool_active": True,
        "judge_enabled": False,
    }


class TestCleanA:
    def test_baseline_audit_grades_a(self) -> None:
        r = translation_quality_grade(_clean_audit(), [])
        assert r.grade == "A"
        assert r.reason == ""
        assert r.rubric_version == 1

    def test_ungraded_on_none_audit(self) -> None:
        r = translation_quality_grade(None, [])
        assert r.grade == "ungraded"

    def test_ungraded_on_empty_audit(self) -> None:
        r = translation_quality_grade({}, [])
        assert r.grade == "ungraded"

    def test_ungraded_on_audit_shape_drift(self) -> None:
        audit = _clean_audit()
        del audit["numeric_slot_recall"]
        r = translation_quality_grade(audit, [])
        assert r.grade == "ungraded"
        assert "shape drift" in r.reason


class TestHardFailsCollapseToC:
    def test_fallback_ratio_over_5_percent_fails(self) -> None:
        audit = _clean_audit()
        audit["bodies_fallback_to_source"] = 10
        audit["units_total"] = 100  # 10 / 100 = 10% > 5%
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"
        assert "fallback" in r.reason.lower()

    def test_notes_status_failed_fails(self) -> None:
        audit = _clean_audit()
        audit["notes_status"] = "failed"
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"
        assert "notes" in r.reason.lower()

    def test_numeric_recall_below_floor_fails(self) -> None:
        audit = _clean_audit()
        audit["numeric_slot_recall"] = 0.985
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"
        assert "numeric_slot_recall" in r.reason

    def test_judge_fail_verdict_fails(self) -> None:
        audit = _clean_audit()
        audit["judge_verdicts"] = {"art_1": {"overall": "fail", "coverage": 4}}
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"
        assert "judge" in r.reason.lower()

    def test_judge_llm_failed_over_third_fails(self) -> None:
        audit = _clean_audit()
        audit["judge_sample_size"] = 4
        audit["judge_failed_count"] = 3  # 3/(3+4) ≈ 42% > 33%
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"

    def test_heading_alien_script_hits_fails(self) -> None:
        """Untranslated Arabic surviving in a <heading> element mirrors the
        existing body-alien predicate."""
        audit = _clean_audit()
        audit["heading_alien_script_hits"] = 2
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"
        assert "heading" in r.reason.lower()

    def test_stray_sentinels_by_eid_fails(self) -> None:
        """An undecoded `⟨N001⟩` in the target is a pipeline bug."""
        audit = _clean_audit()
        audit["stray_sentinels_by_eid"] = {"art_5": ["⟨N003⟩"]}
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"
        assert "sentinel" in r.reason.lower()
        assert "art_5" in r.reason

    def test_money_missing_by_eid_fails(self) -> None:
        """A dropped penalty amount reaches C-grade even if the raise is
        bypassed (defence-in-depth)."""
        audit = _clean_audit()
        audit["money_missing_by_eid"] = {"art_7": ["1,000"]}
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"
        assert "monetary" in r.reason.lower() or "money" in r.reason.lower()
        assert "art_7" in r.reason

    def test_money_duplicated_by_eid_fails(self) -> None:
        """Duplicated monetary amounts downgrade the quality grade."""
        audit = _clean_audit()
        audit["money_duplicated_by_eid"] = {"art_8": ["50,000"]}
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"
        assert "art_8" in r.reason

    def test_missing_new_keys_do_not_falsely_downgrade(self) -> None:
        """An older audit dict with none of the new keys present grades
        cleanly; defends against shape drift on historical rows."""
        audit = _clean_audit()
        # Explicitly do NOT add heading_alien_script_hits, stray_sentinels_by_eid,
        # money_missing_by_eid, money_duplicated_by_eid.
        r = translation_quality_grade(audit, [])
        assert r.grade == "A"


class TestSoftWarnsCollapseToB:
    def test_register_drifted_soft_warn(self) -> None:
        audit = _clean_audit()
        audit["register_drifted"] = True
        r = translation_quality_grade(audit, [])
        assert r.grade == "B"

    def test_notes_partial_soft_warn(self) -> None:
        audit = _clean_audit()
        audit["notes_status"] = "partial"
        r = translation_quality_grade(audit, [])
        assert r.grade == "B"

    def test_clause_gap_soft_warn(self) -> None:
        audit = _clean_audit()
        audit["clause_gaps_by_eid"] = {"art_1": 0.6}
        r = translation_quality_grade(audit, [])
        assert r.grade == "B"

    def test_header_bleed_soft_warn(self) -> None:
        audit = _clean_audit()
        audit["header_bleed_hits"] = {"pat": ["match"]}
        r = translation_quality_grade(audit, [])
        assert r.grade == "B"

    def test_judge_warn_verdict_soft_warn(self) -> None:
        audit = _clean_audit()
        audit["judge_verdicts"] = {"art_1": {"overall": "warn", "register": 3}}
        r = translation_quality_grade(audit, [])
        assert r.grade == "B"

    def test_exemplar_pool_inactive_and_judge_on_soft_warn(self) -> None:
        audit = _clean_audit()
        audit["exemplar_pool_active"] = False
        audit["judge_enabled"] = True
        r = translation_quality_grade(audit, [])
        assert r.grade == "B"


class TestHardFailBeatsSoftWarn:
    def test_hard_and_soft_together_returns_c(self) -> None:
        audit = _clean_audit()
        audit["register_drifted"] = True  # soft
        audit["numeric_slot_recall"] = 0.95  # hard
        r = translation_quality_grade(audit, [])
        assert r.grade == "C"


class TestFlagOnlySignal:
    def test_translate_clause_gap_flag_without_audit_key_still_soft_warn(self) -> None:
        audit = _clean_audit()
        flags = [{"code": "translate_clause_gap", "location": "art_1", "issue": "gap"}]
        r = translation_quality_grade(audit, flags)
        assert r.grade == "B"

    def test_judge_flag_without_audit_key_still_soft_warn(self) -> None:
        audit = _clean_audit()
        flags = [{"code": "judge_coverage", "location": "art_1", "issue": "gap"}]
        r = translation_quality_grade(audit, flags)
        assert r.grade == "B"
