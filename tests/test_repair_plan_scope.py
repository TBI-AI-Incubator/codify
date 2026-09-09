"""What a repair plan is allowed to reach.

Ingested text is attacker-influenced and sits in the agent's context, so a
source PDF can carry an instruction beside a defect that reliably raises a
finding. The gate that matters is not what the model can be told, it is what
the applier accepts, and until now nothing compared a plan's targets to the
finding that licensed it.

The accept cases are not invented: each is a shape taken from the 391 repair
proposals on the demo corpus, which the rule was measured against before it
was written.
"""

from __future__ import annotations

from codify.pipeline.enrich.validator import validate_akn
from codify.repair.ops import (
    Annotate,
    Delete,
    EditPlan,
    Merge,
    Move,
    RenumberSequence,
    SetBody,
)
from codify.repair.scope import out_of_scope, permitted_eids

DUPLICATE = {
    "check": "duplicate_number",
    "kind": "point",
    "scope": "art_3",
    "eids": ["art_3__point_1", "art_3__point_1_2"],
    "message": "point number '(1)' appears on 2 distinct eIds within parent 'art_3'",
}
ORPHANS = {"check": "orphan_articles", "count": 3, "eids": ["art_42", "art_43", "art_44"]}
HIERARCHY = {"check": "hierarchy_coherence", "eid": "att_1__para_2"}


def _plan(*ops: object) -> list:
    return EditPlan(ops=list(ops), reasoning="x").ops  # type: ignore[arg-type]


class TestRefusal:
    def test_an_unrelated_element_is_refused(self) -> None:
        """The injection case: a finding on one article, an edit to another."""
        reason = out_of_scope(_plan(SetBody(eid="art_99", bluebell="text")), DUPLICATE)
        assert reason is not None
        assert "art_99" in reason

    def test_the_refusal_names_what_was_permitted(self) -> None:
        """The agent gets this back and has to be able to re-plan from it."""
        reason = out_of_scope(_plan(Delete(eid="art_99")), DUPLICATE)
        assert reason is not None
        assert "art_3" in reason and "delete" in reason

    def test_one_out_of_scope_op_refuses_the_whole_plan(self) -> None:
        """Plans apply atomically, so they are gated atomically."""
        assert out_of_scope(
            _plan(Delete(eid="art_3__point_1_2"), SetBody(eid="art_99", bluebell="t")), DUPLICATE
        )

    def test_an_ancestor_container_cannot_be_destroyed(self) -> None:
        """eIds are fully hierarchical, so every container up to the root is an
        ancestor of the flagged element. Granting ancestors to every op turns a
        finding two levels inside a chapter into a licence to delete the
        chapter: the same injection, aimed upward instead of sideways."""
        deep = {
            "check": "duplicate_number",
            "scope": "chp_1__sec_2__art_3",
            "eids": ["chp_1__sec_2__art_3__para_1", "chp_1__sec_2__art_3__para_1_2"],
        }
        for op in (
            Delete(eid="chp_1"),
            Delete(eid="chp_1__sec_2"),
            Move(eid="chp_1", new_parent_eid="body"),
            SetBody(eid="chp_1__sec_2", bluebell="t"),
        ):
            assert out_of_scope(_plan(op), deep), f"{op.op} on an ancestor must refuse"

    def test_renumber_cannot_climb_to_a_coarser_sequence(self) -> None:
        """Binding ancestors to renumber_sequence alone is not enough: a
        duplicate point inside a chapter would otherwise license renumbering
        every article in that chapter, which clears the finding and passes the
        schema, no-new-findings and prose gates. The renumbered children have
        to be the kind the finding is about."""
        deep = {
            "check": "duplicate_number",
            "kind": "point",
            "scope": "chp_1__art_3",
            "eids": ["chp_1__art_3__point_1", "chp_1__art_3__point_1_2"],
        }
        assert out_of_scope(
            _plan(RenumberSequence(parent_eid="chp_1", kind="article", nums=["1", "2"])), deep
        )
        assert (
            out_of_scope(
                _plan(RenumberSequence(parent_eid="chp_1__art_3", kind="point", nums=["1", "2"])),
                deep,
            )
            is None
        )

    def test_a_finding_that_names_nothing_refuses_rather_than_opens(self) -> None:
        """`None` is the ad-hoc unscoped path. A finding that is present but
        names no element must not disable the gate by being empty."""
        assert out_of_scope(_plan(Delete(eid="art_1")), {"check": "duplicate_number"})
        assert out_of_scope(_plan(Delete(eid="art_1")), {"check": "x", "eids": [], "scope": ""})

    def test_an_op_whose_targets_cannot_be_read_is_refused(self) -> None:
        """A new op class naming its target something other than `eid` must not
        pass by accident. This gate fails closed."""

        class Unknown:
            op = "unknown"

        assert out_of_scope([Unknown()], DUPLICATE)  # type: ignore[list-item]

    def test_a_sibling_the_finding_does_not_name_is_refused(self) -> None:
        """Measured on the corpus: an agent renumbering the neighbouring article
        as well as the flagged one. Real, applied, and more than the finding
        licensed, so the narrowing is deliberate."""
        assert out_of_scope(
            _plan(RenumberSequence(parent_eid="art_2", kind="point", nums=["1", "2"])), DUPLICATE
        )


class TestAcceptance:
    def test_the_flagged_element_itself(self) -> None:
        assert out_of_scope(_plan(Delete(eid="art_3__point_1_2")), DUPLICATE) is None

    def test_a_sibling_the_finding_names(self) -> None:
        """`duplicate_number` is inherently about a pair: the fix edits the other
        half, which is a different eId and is in the finding's `eids`."""
        assert out_of_scope(_plan(Delete(eid="art_3__point_1")), DUPLICATE) is None

    def test_the_parent_the_finding_scopes_to(self) -> None:
        """`renumber_sequence` names the parent that owns the numbering."""
        assert (
            out_of_scope(
                _plan(RenumberSequence(parent_eid="art_3", kind="point", nums=["1", "2"])),
                DUPLICATE,
            )
            is None
        )

    def test_only_renumber_sequence_may_name_an_ancestor(self) -> None:
        """The one op that legitimately does: the element owning a numbering run
        is the parent while the finding sits on a child."""
        deep = {"check": "duplicate_number", "kind": "point", "eid": "art_15__point_x__point_2"}
        assert (
            out_of_scope(
                _plan(RenumberSequence(parent_eid="art_15", kind="point", nums=["1"])), deep
            )
            is None
        )

    def test_a_descendant_of_a_named_element(self) -> None:
        """Against DUPLICATE, not None: passing None takes the unscoped bypass
        and tests nothing."""
        assert (
            out_of_scope(_plan(SetBody(eid="art_3__point_1__p_1", bluebell="t")), DUPLICATE) is None
        )
        assert (
            out_of_scope(_plan(Annotate(eid="art_3__point_1__p_2__i_1", note="n")), DUPLICATE)
            is None
        )
        assert out_of_scope(_plan(Annotate(eid="art_3__point_1", note="n")), DUPLICATE) is None

    def test_every_element_of_a_multi_element_finding(self) -> None:
        ops = _plan(*(Move(eid=e, new_parent_eid="chp_1") for e in ORPHANS["eids"]))  # type: ignore[union-attr]
        assert out_of_scope(ops, ORPHANS) is None

    def test_a_move_destination_is_not_gated(self) -> None:
        """Where an element lands is outside the finding by definition: a move
        exists to put it somewhere it currently is not. Whether the result is
        coherent is the schema validator's job, not this one's."""
        assert (
            out_of_scope(_plan(Move(eid="att_1__para_2", new_parent_eid="chp_7__sec_2")), HIERARCHY)
            is None
        )

    def test_both_halves_of_a_merge_are_checked(self) -> None:
        assert (
            out_of_scope(_plan(Merge(eid_a="art_3__point_1", eid_b="art_3__point_1_2")), DUPLICATE)
            is None
        )
        assert out_of_scope(_plan(Merge(eid_a="art_3__point_1", eid_b="art_99")), DUPLICATE)

    def test_no_finding_means_no_scope_to_enforce(self) -> None:
        """`None` only. The ad-hoc paths pass no finding and are not the
        injection surface; a finding that is present but empty is refused, and
        that case is covered above."""
        assert out_of_scope(_plan(SetBody(eid="anything", bluebell="t")), None) is None


class TestPermittedSet:
    def test_it_gathers_eid_eids_and_scope(self) -> None:
        assert permitted_eids(DUPLICATE) == frozenset(
            {"art_3", "art_3__point_1", "art_3__point_1_2"}
        )

    def test_empty_strings_do_not_widen_it(self) -> None:
        assert permitted_eids({"eid": "", "eids": [], "scope": ""}) == frozenset()


def test_orphan_articles_names_every_orphan_not_a_sample() -> None:
    """The eIds list is machine-read by the scope gate, so truncating it to ten
    refused the tail of a legitimate 13-article fix. The message keeps a short
    sample for humans; this field has to be complete."""
    articles = "".join(
        f'<article eId="art_{i}"><num>{i}</num><content><p>text</p></content></article>'
        for i in range(1, 15)
    )
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><body>"
        '<chapter eId="chp_1"><num>1</num><content><p>x</p></content></chapter>'
        f"{articles}"
        "</body></act></akomaNtoso>"
    )
    orphan = [f for f in validate_akn(xml) if f.get("check") == "orphan_articles"]
    assert orphan, "expected the check to fire"
    assert len(orphan[0]["eids"]) == orphan[0]["count"] == 14
    assert "Sample eIds" in orphan[0]["message"]


def test_the_cleared_check_survives_a_changed_eids_list() -> None:
    """`_finding_key` joins the whole eIds list, so a proposal persisted with a
    truncated list would never match the post-image's complete one and the
    'targeted finding not cleared' guard would stop binding in silence."""
    from codify.repair.edit_ops import _finding_key, _still_present

    stored = {"check": "orphan_articles", "eids": [f"art_{i}" for i in range(1, 11)]}
    complete = {"check": "orphan_articles", "eids": [f"art_{i}" for i in range(1, 14)]}
    assert _finding_key(stored) != _finding_key(complete), "premise: the keys differ"
    assert _still_present(stored, {_finding_key(complete)}), "the finding is still there"
    assert not _still_present(stored, {("orphan_articles", "art_90,art_91")})
    assert not _still_present(stored, set())
