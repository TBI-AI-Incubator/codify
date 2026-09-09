"""The preview sandbox, every gate, offline and pure."""

from __future__ import annotations

import pytest
from lxml import etree

from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.validator import validate_akn
from codify.repair.ops import Delete, EditPlan, RenumberSequence, SetBody, SetNum
from codify.repair.sandbox import grounding_score, own_text, preview_plan


def _fixture() -> str:
    bb = "BODY\n  SECTION 1\n    ARTICLE 1\n      Body of article one.\n    ARTICLE 2\n"
    return parse_to_akn(bb, country="gb", doctype="act", number="1", date="2020-01-01")


def _empty_article(xml: str) -> dict[str, object]:
    return next(f for f in validate_akn(xml) if f["check"] == "empty_article")


def test_grounded_plan_is_accepted_with_diff_and_score() -> None:
    xml = _fixture()
    finding = _empty_article(xml)
    source = "Article 2. The restored body of the second article."
    plan = EditPlan(ops=[SetBody(eid=str(finding["eid"]), bluebell=source.split(". ", 1)[1])])

    report = preview_plan(xml, plan, country="gb", target_finding=finding, source_text=source)

    assert report.ok, report.reasons
    assert report.xml
    assert report.findings_after < report.findings_before
    delta = next(d for d in report.text_deltas if d.eid == finding["eid"])
    assert delta.grounding is not None and delta.grounding > 0.9
    assert "restored body" in delta.after  # the audit carries the new text


def test_fabricated_body_is_rejected_but_scored() -> None:
    xml = _fixture()
    finding = _empty_article(xml)
    plan = EditPlan(
        ops=[SetBody(eid=str(finding["eid"]), bluebell="Entirely invented penalty clauses.")]
    )

    report = preview_plan(
        xml, plan, country="gb", target_finding=finding, source_text="Unrelated source page."
    )

    assert not report.ok
    assert any(r.startswith("insufficient_grounding") for r in report.reasons)
    # The plan WOULD have cleared the finding; grounding rejects it anyway.
    assert report.findings_after < report.findings_before


def test_no_source_evidence_abstains_rather_than_gates() -> None:
    xml = _fixture()
    finding = _empty_article(xml)
    plan = EditPlan(ops=[SetBody(eid=str(finding["eid"]), bluebell="Text read off the image.")])

    report = preview_plan(xml, plan, country="gb", target_finding=finding, source_text="")

    assert report.ok, report.reasons
    delta = next(d for d in report.text_deltas if d.eid == finding["eid"])
    assert delta.grounding is None
    # No text evidence at all: valid, but escalated beyond machine authority.
    assert report.escalated and report.risk == "high"


def test_policy_rejection_reaches_the_report() -> None:
    xml = _fixture()
    finding = _empty_article(xml)
    report = preview_plan(
        xml,
        EditPlan(ops=[Delete(eid=str(finding["eid"]))]),
        country="gb",
        target_finding=finding,
    )
    assert not report.ok
    assert any("not permitted" in r for r in report.reasons)


def test_empty_plan_reports_as_such() -> None:
    report = preview_plan(_fixture(), EditPlan(), country="gb")
    assert not report.ok
    assert report.reasons == ["empty_plan"]


def test_renumber_touches_the_whole_run_without_false_rejection() -> None:
    """The touched-closure must cover the named parent's descendants, or a
    legitimate renumber reads as an unchanged-span violation."""
    bb = (
        "BODY\n  SECTION 1\n"
        "    ARTICLE 3\n      First body text.\n"
        "    ARTICLE 3\n      Second body text.\n"
        "    ARTICLE 3\n      Third body text.\n"
    )
    xml = parse_to_akn(bb, country="gb", doctype="act", number="2", date="2020-01-01")
    finding = next(f for f in validate_akn(xml) if f["check"] == "duplicate_number")
    plan = EditPlan(
        ops=[
            RenumberSequence(parent_eid=str(finding["scope"]), kind="article", nums=["3", "4", "5"])
        ]
    )

    report = preview_plan(xml, plan, country="gb", target_finding=finding)

    assert report.ok, report.reasons
    assert not any(r.startswith("unchanged_span_modified") for r in report.reasons)


def test_meta_mutation_is_rejected_even_without_a_target_finding() -> None:
    """apply_plan's own gates cannot see a meta-only deletion (no findings move,
    no body text shrinks); the sandbox's meta gate is the backstop."""
    xml = _fixture()
    root = etree.fromstring(xml.encode("utf-8"))
    meta_eids = [
        str(el.get("eId"))
        for el in root.iter("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}meta")
        for el in el.iter()
        if el.get("eId")
    ]
    if not meta_eids:
        pytest.skip("fixture meta carries no eIds; the gate is unreachable here")
    report = preview_plan(xml, EditPlan(ops=[Delete(eid=meta_eids[0])]), country="gb")
    assert not report.ok


def test_own_text_does_not_cascade_to_ancestors() -> None:
    xml = "<a eId='p'><t>head</t><b eId='c'><t>child text</t></b>tail</a>"
    root = etree.fromstring(xml)
    assert "child text" not in own_text(root)
    assert "head" in own_text(root)
    assert "tail" in own_text(root)


def test_grounding_score_directionality() -> None:
    assert grounding_score("word another", "word another plus more") == 1.0
    score = grounding_score("word invented", "word only")
    assert score is not None and 0.4 < score < 0.6
    assert grounding_score("anything", "") is None
    assert grounding_score("", "source") is None


def test_touched_closure_is_exactly_target_and_descendants() -> None:
    from codify.repair.sandbox import _touched_eids

    xml = _fixture()
    root = etree.fromstring(xml.encode("utf-8"))
    art_1 = next(
        str(el.get("eId"))
        for el in root.iter()
        if el.get("eId") and str(el.get("eId")).endswith("art_1")
    )
    touched = _touched_eids(root, [SetBody(eid=art_1, bluebell="x")])
    assert art_1 in touched
    all_eids = {str(el.get("eId")) for el in root.iter() if el.get("eId")}
    outside = all_eids - touched
    assert outside, "closure must not swallow the whole document"
    assert not any(e.endswith("art_2") for e in touched), "siblings are not touched"


def test_plan_risk_is_the_max_over_ops_with_escalation() -> None:
    from codify.repair.ops import Delete, Move, plan_risk

    assert plan_risk([Move(eid="a", new_parent_eid="b")]) == "low"
    assert (
        plan_risk([Move(eid="a", new_parent_eid="b"), SetBody(eid="a", bluebell="x")]) == "medium"
    )
    assert plan_risk([Delete(eid="a")]) == "high"
    assert plan_risk([Move(eid="a", new_parent_eid="b")], escalate=True) == "high"
    assert plan_risk([]) == "low"


def test_source_mismatch_escalates_a_grounded_plan() -> None:
    """Drifted evidence raises ANY plan to high risk, however well grounded."""
    xml = _fixture()
    finding = _empty_article(xml)
    source = "Article 2. The restored body of the second article."
    plan = EditPlan(ops=[SetBody(eid=str(finding["eid"]), bluebell=source.split(". ", 1)[1])])
    report = preview_plan(
        xml,
        plan,
        country="gb",
        target_finding=finding,
        source_text=source,
        source_text_mismatch=True,
    )
    assert report.ok
    assert report.escalated and report.risk == "high"


def test_risk_class_covers_every_op() -> None:
    import typing

    from codify.repair.ops import RISK_CLASS, RepairOp

    union = typing.get_args(typing.get_args(RepairOp)[0])
    for op_cls in union:
        discriminator = op_cls.model_fields["op"].default
        assert discriminator in RISK_CLASS, f"{op_cls.__name__} has no declared risk class"


def test_set_body_on_duplicate_number_escalates_to_high() -> None:
    # #702: SetBody rewrites VALID text on a duplicate_number, so it queues even when grounded.
    bb = (
        "BODY\n  SECTION 1\n    ARTICLE 1\n      First article body here.\n"
        "    ARTICLE 1\n      Second article body here.\n"
    )
    xml = parse_to_akn(bb, country="gb", doctype="act", number="1", date="2020-01-01")
    dup = next(f for f in validate_akn(xml) if f["check"] == "duplicate_number")
    grounded = "First article body here restored."
    # SetNum clears the duplicate so the plan applies; the co-bundled SetBody is what escalates.
    plan = EditPlan(
        ops=[
            SetNum(eid=str(dup["eids"][0]), num="2"),
            SetBody(eid=str(dup["eids"][0]), bluebell=grounded),
        ]
    )
    report = preview_plan(
        xml,
        plan,
        country="gb",
        target_finding=dup,
        source_text=grounded + " First article body here.",
    )
    assert report.ok, report.reasons
    assert report.escalated
    assert report.risk == "high"


def test_grounded_set_body_on_empty_article_is_not_escalated() -> None:
    # Control: the same grounded SetBody on an empty target stays medium (no escalation).
    xml = _fixture()
    empty = _empty_article(xml)
    grounded = "The real body of the second article."
    plan = EditPlan(ops=[SetBody(eid=str(empty["eid"]), bluebell=grounded)])
    report = preview_plan(xml, plan, country="gb", target_finding=empty, source_text=grounded)
    assert not report.escalated
