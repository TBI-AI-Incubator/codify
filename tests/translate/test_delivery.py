"""Grade-and-annotate delivery classification."""

from __future__ import annotations

from codify.translate.delivery import (
    DeliveryFinding,
    delivery_findings,
    has_review,
    review_finding,
)


def test_eid_maps_and_count_keys_become_findings() -> None:
    audit = {
        "money_missing_by_eid": {"art_5": ["1000"]},
        "body_alien_script_hits_by_eid": {"art_3": 1, "art_9": 2},
        "front_matter_source_retained": 2,
    }
    findings = delivery_findings(audit)
    by = {(f.category, f.eid): f for f in findings}
    # money is review-tier; body-untranslated is advisory; front-matter is a count.
    assert by[("money_missing", "art_5")].severity == "review"
    assert by[("body_untranslated", "art_3")].severity == "advisory"
    assert by[("body_untranslated", "art_9")].severity == "advisory"
    assert by[("front_matter_retained", "")].severity == "advisory"
    assert "2:" in by[("front_matter_retained", "")].detail


def test_has_review_only_when_a_silent_defect_is_present() -> None:
    visible_only = {"body_alien_script_hits_by_eid": {"art_1": 1}}
    assert has_review(delivery_findings(visible_only)) is False
    with_money = {"money_duplicated_by_eid": {"art_1": ["50000"]}}
    assert has_review(delivery_findings(with_money)) is True


def test_clean_audit_yields_no_findings() -> None:
    assert delivery_findings({}) == []
    assert delivery_findings({"body_alien_script_hits_by_eid": {}}) == []


def test_count_exceeding_map_emits_an_unnamed_finding() -> None:
    # A <p> still in the source script with no eId ancestor increments the count
    # but not the map; the remainder must not vanish (else it grades clean).
    audit = {"body_alien_script_hits": 3, "body_alien_script_hits_by_eid": {"art_3": 1}}
    findings = delivery_findings(audit)
    named = [f for f in findings if f.eid == "art_3"]
    unnamed = [f for f in findings if f.category == "body_untranslated" and f.eid == ""]
    assert len(named) == 1
    assert len(unnamed) == 1 and unnamed[0].detail.startswith("2:")  # 3 counted - 1 named


def test_count_only_no_map_still_flags() -> None:
    # Count present, map absent entirely: still one unnamed finding, not zero.
    assert [f.eid for f in delivery_findings({"letter_spaced_runs": 4})] == [""]


def test_count_not_exceeding_map_adds_no_remainder() -> None:
    audit = {"body_alien_script_hits": 1, "body_alien_script_hits_by_eid": {"art_3": 1}}
    assert [f.eid for f in delivery_findings(audit)] == ["art_3"]


def test_remainder_counts_occurrences_not_eids() -> None:
    # body_alien map values are int counts; two hits under one eId must not leave
    # a spurious unnamed remainder (3 counted = 1 + 2 named).
    audit = {"body_alien_script_hits": 3, "body_alien_script_hits_by_eid": {"art_3": 1, "art_9": 2}}
    findings = delivery_findings(audit)
    assert sorted(f.eid for f in findings) == ["art_3", "art_9"]  # no eid="" remainder
    # letter_spaced map values are lists; len is the occurrence count.
    audit = {"letter_spaced_runs": 2, "letter_spaced_runs_by_eid": {"art_1": ["a b", "c d"]}}
    assert [f.eid for f in delivery_findings(audit)] == ["art_1"]


def test_review_finding_helper_for_out_of_audit_signals() -> None:
    f = review_finding("structural_loss", eid="art_7", detail="art_7 lost")
    assert isinstance(f, DeliveryFinding)
    assert f.severity == "review" and f.eid == "art_7"
