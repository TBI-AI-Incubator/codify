"""The history builder over synthetic works: the worked cases, the same-day rule, identity."""

from __future__ import annotations

import itertools
import struct
from dataclasses import replace
from typing import Any

import pytest

from codify.history.build import MOVED_TEXT_CONFIDENCE, SourceText, Unit, WorkBuild
from tests.history._support import (
    HARBOUR,
    JURISDICTION,
    LIGHTS,
    PILOT,
    T0,
    T1,
    T2,
    WORK,
    active,
    build,
    d,
    in_force,
    sec,
    spans,
    text,
)

START = d("2021-04-01")
TYPED = 'The master\'s "berthing" fee is set out in the harbour-dues schedule for every vessel.'


def _reissues() -> list[SourceText]:
    kw = {"units": (sec(1, HARBOUR),)}
    return [
        text(START, ref="a", published_on=d("2020-02-11"), valid_to=d("2021-09-30"), **kw),
        text(START, ref="b", published_on=d("2020-09-03"), **kw),
        text(START, ref="c", published_on=d("2020-08-19"), **kw),
        text(START, ref="d", published_on=d("2020-03-27"), **kw),
    ]


class TestSameDay:
    def test_a_text_that_ended_wins_over_a_later_publication(self) -> None:
        built = build(_reissues())
        assert [e.source_ref for e in built.expressions] == ["a"]
        only = built.expressions[0]
        assert (only.valid_from, only.valid_to) == (START, d("2021-10-01"))
        assert (only.derivation, only.official, only.gap_reason) == ("publisher_text", True, None)

    def test_without_an_ended_text_the_latest_publication_wins(self) -> None:
        texts = [replace(t, valid_to=None) for t in _reissues()]
        assert [e.source_ref for e in build(texts).expressions] == ["b"]

    def test_the_choice_does_not_depend_on_input_order(self) -> None:
        texts = [replace(t, published_on=d("2020-02-11")) for t in _reissues()]
        texts = [replace(t, valid_to=None) for t in texts]
        chosen = {build(p).expressions[0].source_ref for p in itertools.permutations(texts)}
        assert chosen == {"d"}

    def test_a_loser_leaves_no_row_and_the_winner_does_not_hide_a_neighbour(self) -> None:
        texts = [*_reissues(), text(d("2021-10-01"), [sec(1, PILOT)], ref="next")]
        built = build(texts)
        assert [(e.source_ref, e.valid_to) for e in built.expressions] == [
            ("a", d("2021-10-01")),
            ("next", None),
        ]

    def test_two_languages_keep_a_text_each_for_one_day(self) -> None:
        texts = [
            text(START, [sec(1, HARBOUR)], ref="en"),
            text(START, [sec(1, "Skip te Hafen")], ref="de", language="deu"),
        ]
        built = build(texts)
        assert {(e.language, e.source_ref) for e in built.expressions} == {
            ("eng", "en"),
            ("deu", "de"),
        }


class TestWorkedCases:
    def test_a_retroactive_text_fills_the_days_before_a_later_text(self) -> None:
        later = text(d("2022-03-10"), [sec(1, HARBOUR)], ref="later", published_on=d("2022-03-12"))
        first = build([later], now=T0)
        retro = text(
            d("2022-03-01"),
            [sec(1, HARBOUR)],
            ref="retro",
            valid_to=d("2022-03-09"),
            published_on=d("2022-11-05"),
        )
        built = build([later, retro], now=T1, previous=first)
        assert [(e.source_ref, e.valid_from, e.valid_to) for e in built.expressions] == [
            ("retro", d("2022-03-01"), d("2022-03-10")),
            ("later", d("2022-03-10"), None),
        ]
        stamps = {e.source_ref: e.recorded_at for e in built.expressions}
        assert stamps == {"retro": T1, "later": T0}

    def test_a_retroactive_text_moves_a_start_and_logs_the_removed_row(self) -> None:
        later = text(d("2022-03-10"), [sec(1, HARBOUR)], ref="later")
        first = build([later], now=T0)
        retro = text(d("2022-03-01"), [sec(1, HARBOUR)], ref="retro", valid_to=d("2022-03-09"))
        built = build([later, retro], now=T1, previous=first)
        assert spans(built, "chp_1__sec_1") == [(d("2022-03-01"), None)]
        assert [
            (w.row_kind, w.akn_eid, w.valid_from, w.withdrawn_at) for w in built.withdrawals
        ] == [("interval", "chp_1__sec_1", d("2022-03-10"), T1)]

    def test_a_future_dated_text_is_stored_and_an_earlier_date_never_reaches_it(self) -> None:
        texts = [
            text(d("2028-02-01"), [sec(1, HARBOUR)], ref="y", valid_to=d("2028-07-31")),
            text(d("2028-08-01"), [sec(1, PILOT)], ref="x1", published_on=d("2027-03-14")),
            text(d("2028-08-01"), [sec(1, LIGHTS)], ref="x2", published_on=d("2027-04-22")),
            text(d("2028-08-01"), [sec(1, HARBOUR)], ref="x3", published_on=d("2027-01-09")),
        ]
        built = build(texts)
        assert [(e.source_ref, e.valid_from, e.valid_to) for e in built.expressions] == [
            ("y", d("2028-02-01"), d("2028-08-01")),
            ("x2", d("2028-08-01"), None),
        ]
        assert in_force(built, d("2028-03-15")) == {"chp_1__sec_1": HARBOUR}
        assert in_force(built, d("2028-08-02")) == {"chp_1__sec_1": LIGHTS}
        assert in_force(built, d("2028-01-31")) == {}

    def test_undated_and_never_in_force_texts_leave_no_span(self) -> None:
        texts = [
            text(None, [sec(1, PILOT)], ref="prospective"),
            text(d("2024-05-02"), [sec(1, LIGHTS)], ref="backwards", valid_to=d("2024-05-01")),
            text(d("2024-01-01"), [sec(1, HARBOUR)], ref="ok"),
        ]
        built = build(texts)
        assert [e.source_ref for e in built.expressions] == ["ok"]
        assert {i.text for i in built.intervals} == {HARBOUR}

    def test_a_span_made_by_applying_effects_says_so_and_is_not_merged_into_the_text_before(
        self,
    ) -> None:
        base = text(d("2018-10-10"), [sec(1, HARBOUR)], ref="base")
        derived = SourceText(
            language="eng",
            valid_from=d("2023-11-01"),
            akn_xml="<akomaNtoso/>",
            units=(sec(1, HARBOUR, opened_by="notice 12"),),
            derivation="applied_effects",
            official=False,
            confidence=0.7,
            derived_from={"base": "2018-10-10", "effects": ["x1", "x2"]},
            converter_rev="c1",
        )
        built = build([base, derived])
        expression = built.expressions[1]
        assert (expression.source_ref, expression.source_sha256) == (None, None)
        assert (expression.derivation, expression.official, expression.confidence) == (
            "applied_effects",
            False,
            0.7,
        )
        assert expression.derived_from == {"base": "2018-10-10", "effects": ["x1", "x2"]}
        first, second = built.intervals
        assert (first.derivation, first.official, first.valid_to) == (
            "publisher_text",
            True,
            d("2023-11-01"),
        )
        assert (second.derivation, second.official, second.confidence, second.opened_by) == (
            "applied_effects",
            False,
            0.7,
            "notice 12",
        )

    def test_two_territorial_variants_of_one_provision_are_kept_apart(self) -> None:
        both = text(
            d("2022-09-01"),
            [
                sec(5, "Rule for area A.", variant="extent:a"),
                sec(5, "Rule for area B.", variant="extent:b"),
            ],
            ref="w5",
        )
        only_a = text(d("2023-04-20"), [sec(5, "Rule for area A.", variant="extent:a")], ref="w5b")
        built = build([both, only_a])
        eid = "chp_1__sec_5"
        assert spans(built, eid, "extent:a") == [(d("2022-09-01"), None)]
        assert spans(built, eid, "extent:b") == [(d("2022-09-01"), d("2023-04-20"))]
        assert in_force(built, d("2023-01-15"), "extent:b") == {eid: "Rule for area B."}
        assert {e.variant for e in built.expressions} == {""}

    def test_an_unofficial_consolidation_is_labelled_on_its_span_and_provisions(self) -> None:
        consolidated = text(
            d("2023-06-12"),
            [sec(1, HARBOUR)],
            ref="consolidated",
            derivation="unofficial_consolidation",
            official=False,
        )
        built = build([consolidated])
        assert (built.expressions[0].official, built.expressions[0].confidence) == (False, None)
        assert built.expressions[0].derivation == "unofficial_consolidation"
        assert [(i.official, i.derivation) for i in built.intervals] == [
            (False, "unofficial_consolidation")
        ]

    def test_a_text_that_fails_to_convert_is_a_gap_that_closes_every_open_span(self) -> None:
        texts = [
            text(d("2024-01-01"), [sec(1, HARBOUR), sec(2, PILOT)], ref="one"),
            text(d("2024-06-01"), ref="two", akn_xml=None),
            text(d("2024-09-01"), [sec(1, HARBOUR), sec(2, PILOT)], ref="three"),
        ]
        built = build(texts)
        gap = built.expressions[1]
        assert (gap.akn_xml, gap.gap_reason, gap.source_ref) == (None, "conversion_failed", "two")
        assert spans(built, "chp_1__sec_1") == [
            (d("2024-01-01"), d("2024-06-01")),
            (d("2024-09-01"), None),
        ]
        assert in_force(built, d("2024-07-01")) == {}
        assert built.links == ()


class TestIdentity:
    def test_a_renamed_container_keeps_one_history_through_a_same_number_link(self) -> None:
        one = text(d("2024-01-01"), [sec(5, HARBOUR, chapter=1)], ref="one")
        two = text(d("2024-06-01"), [sec(5, HARBOUR, chapter=2)], ref="two")
        built = build([one, two])
        (link,) = built.links
        assert (link.from_eid, link.to_eid, link.rule, link.confidence) == (
            "chp_1__sec_5",
            "chp_2__sec_5",
            "same_number",
            None,
        )
        old, new = built.intervals
        assert old.text_sha256 == new.text_sha256 and old.valid_to == new.valid_from

    def test_identical_text_under_a_new_number_is_a_hint_and_not_a_merge(self) -> None:
        one = text(d("2024-01-01"), [sec(5, HARBOUR)], ref="one")
        two = text(d("2024-06-01"), [sec(9, HARBOUR)], ref="two")
        built = build([one, two])
        (link,) = built.links
        assert (link.from_eid, link.to_eid, link.rule) == (
            "chp_1__sec_5",
            "chp_1__sec_9",
            "moved_text",
        )
        assert link.confidence == MOVED_TEXT_CONFIDENCE
        assert spans(built, "chp_1__sec_5") == [(d("2024-01-01"), d("2024-06-01"))]
        assert spans(built, "chp_1__sec_9") == [(d("2024-06-01"), None)]

    def test_identical_text_that_could_come_from_two_places_gives_no_hint(self) -> None:
        one = text(d("2024-01-01"), [sec(5, HARBOUR), sec(6, HARBOUR)], ref="one")
        two = text(d("2024-06-01"), [sec(9, HARBOUR)], ref="two")
        assert build([one, two]).links == ()

    def test_swapped_wordings_keep_each_number_s_history(self) -> None:
        one = text(d("2024-01-01"), [sec(5, HARBOUR), sec(6, PILOT)], ref="one")
        two = text(d("2024-06-01"), [sec(5, PILOT), sec(6, HARBOUR)], ref="two")
        built = build([one, two])
        assert built.links == ()
        assert spans(built, "chp_1__sec_5") == [
            (d("2024-01-01"), d("2024-06-01")),
            (d("2024-06-01"), None),
        ]

    def test_a_same_number_link_with_little_shared_wording_carries_its_similarity(self) -> None:
        one = text(d("2024-01-01"), [sec(5, HARBOUR, chapter=1)], ref="one")
        two = text(
            d("2024-06-01"), [sec(5, "Zzzzz qqqq xxxx jjjj kkkk vvvv.", chapter=2)], ref="two"
        )
        (link,) = build([one, two]).links
        assert link.rule == "same_number"
        assert link.confidence is not None and 0 <= link.confidence < 0.3

    def test_labels_never_join_another_eid_but_an_unchanged_label_continues(self) -> None:
        label = "[wording of subsection 2 until 1 July]"
        one = text(
            d("2024-01-01"),
            [sec(5, label, chapter=1, kind="label"), sec(6, label, chapter=1, kind="label")],
            ref="one",
        )
        two = text(
            d("2024-06-01"),
            [sec(5, HARBOUR, chapter=2), sec(6, label, chapter=1, kind="label")],
            ref="two",
        )
        built = build([one, two])
        assert built.links == ()
        assert spans(built, "chp_1__sec_6") == [(d("2024-01-01"), None)]

    def test_a_converter_duplicate_suffix_does_not_change_the_number(self) -> None:
        one = text(d("2024-01-01"), [sec(5, HARBOUR)], ref="one")
        dup = Unit(eid="chp_2__sec_5_dup2", akn_type="section", position=1, text=HARBOUR)
        built = build([one, text(d("2024-06-01"), [dup], ref="two")])
        assert [(k.from_eid, k.to_eid, k.rule) for k in built.links] == [
            ("chp_1__sec_5", "chp_2__sec_5_dup2", "same_number")
        ]

    def test_several_wordings_under_one_number_pair_by_identical_text(self) -> None:
        def pair(chapter: int, first: str, second: str) -> list[Unit]:
            return [
                Unit(eid=f"chp_{chapter}__sec_5", akn_type="section", position=1, text=first),
                Unit(eid=f"chp_{chapter}__sec_5_dup2", akn_type="section", position=2, text=second),
            ]

        one = text(d("2024-01-01"), pair(1, HARBOUR, PILOT), ref="one")
        two = text(d("2024-06-01"), pair(2, PILOT, HARBOUR), ref="two")
        built = build([one, two])
        assert {(k.from_eid, k.to_eid, k.rule) for k in built.links} == {
            ("chp_1__sec_5", "chp_2__sec_5_dup2", "same_number_text"),
            ("chp_1__sec_5_dup2", "chp_2__sec_5", "same_number_text"),
        }

    def test_the_number_path_starts_at_the_basic_unit_below_a_grouping_level(self) -> None:
        def article(eid: str) -> Unit:
            return Unit(eid=eid, akn_type="article", position=1, text=HARBOUR)

        one = text(d("2024-01-01"), [article("chp_1__art_3")], ref="one")
        two = text(d("2024-06-01"), [article("chp_1__sec_1__art_3")], ref="two")
        for prefixes in (("sec", "art"), ("art",)):
            (link,) = build([one, two], unit_prefixes=prefixes).links
            assert (link.from_eid, link.to_eid, link.rule) == (
                "chp_1__art_3",
                "chp_1__sec_1__art_3",
                "same_number",
            )

    def test_a_grouping_level_named_as_the_basic_unit_changes_what_is_a_number(self) -> None:
        def article(eid: str) -> Unit:
            return Unit(eid=eid, akn_type="article", position=1, text=HARBOUR)

        one = text(d("2024-01-01"), [article("chp_1__art_3")], ref="one")
        two = text(d("2024-06-01"), [article("chp_1__sec_1__art_3")], ref="two")
        assert [k.rule for k in build([one, two], unit_prefixes=("sec",)).links] == ["moved_text"]

    def test_the_same_number_of_another_type_is_another_provision(self) -> None:
        one = text(d("2024-01-01"), [sec(5, HARBOUR)], ref="one")
        other = Unit(eid="chp_2__sec_5", akn_type="article", position=1, text=HARBOUR)
        assert [k.rule for k in build([one, text(d("2024-06-01"), [other], ref="two")]).links] == [
            "moved_text"
        ]

    def test_two_arrivals_for_one_number_are_not_guessed(self) -> None:
        one = text(d("2024-01-01"), [sec(5, HARBOUR, chapter=1)], ref="one")
        two = text(
            d("2024-06-01"), [sec(5, PILOT, chapter=2), sec(5, LIGHTS, chapter=3)], ref="two"
        )
        assert build([one, two]).links == ()


class TestHashes:
    def _pair(self, before: str, after: str, *, same_path: bool = True) -> WorkBuild:
        chapter = 1 if same_path else 2
        one = text(d("2024-01-01"), [sec(5, before)], ref="one")
        two = text(d("2024-06-01"), [sec(5, after, chapter=chapter)], ref="two")
        return build([one, two])

    def test_a_path_only_change_leaves_both_hashes_equal(self) -> None:
        old, new = self._pair(HARBOUR, HARBOUR, same_path=False).intervals
        assert old.akn_eid != new.akn_eid
        assert old.text_sha256 == new.text_sha256
        assert old.text_norm_sha256 == new.text_norm_sha256

    @pytest.mark.parametrize(
        "after",
        [
            TYPED.replace(" ", "\u00a0"),
            TYPED.upper(),
            TYPED.replace('"', "\u201c", 1).replace('"', "\u201d", 1).replace("'", "\u2019"),
            TYPED.replace("-", "\u2013"),
            TYPED + "\n",
        ],
    )
    def test_a_typographic_change_splits_the_span_but_not_the_normalised_hash(
        self, after: str
    ) -> None:
        old, new = self._pair(TYPED, after).intervals
        assert old.text_sha256 != new.text_sha256
        assert old.text_norm_sha256 == new.text_norm_sha256

    def test_a_change_of_wording_changes_both_hashes(self) -> None:
        old, new = self._pair(HARBOUR, HARBOUR.replace("berthing", "mooring")).intervals
        assert old.text_sha256 != new.text_sha256
        assert old.text_norm_sha256 != new.text_norm_sha256

    def test_an_unchanged_text_is_one_span_across_texts(self) -> None:
        texts = [text(d(f"2024-0{m}-01"), [sec(1, HARBOUR)], ref=str(m)) for m in (1, 2, 3)]
        assert spans(build(texts), "chp_1__sec_1") == [(d("2024-01-01"), None)]


class TestRebuild:
    def test_a_rebuild_that_drops_a_text_removes_its_rows_and_logs_them(self) -> None:
        one = text(d("2024-01-01"), [sec(1, HARBOUR), sec(2, PILOT)], ref="one")
        two = text(d("2024-06-01"), [sec(1, HARBOUR), sec(3, LIGHTS)], ref="two")
        first = build([one, two], now=T0)
        again = build([one], now=T2, previous=first)
        assert [(e.source_ref, e.valid_to) for e in again.expressions] == [("one", None)]
        removed = [(w.row_kind, w.akn_eid, w.valid_from) for w in again.withdrawals]
        assert removed == [
            ("expression", None, d("2024-06-01")),
            ("interval", "chp_1__sec_3", d("2024-06-01")),
        ]
        assert spans(again, "chp_1__sec_2") == [(d("2024-01-01"), None)]
        assert all(w.withdrawn_at == T2 and w.work_id == WORK for w in again.withdrawals)
        assert all(w.jurisdiction_id == JURISDICTION for w in again.withdrawals)

    def test_an_unchanged_row_keeps_its_recorded_time_and_a_changed_one_does_not(self) -> None:
        one = text(d("2024-01-01"), [sec(1, HARBOUR), sec(2, PILOT)], ref="one")
        two = text(d("2024-06-01"), [sec(1, HARBOUR), sec(2, LIGHTS)], ref="two")
        first = build([one], now=T0)
        built = build([one, two], now=T1, previous=first)
        by_eid = {(i.akn_eid, i.valid_from): i.recorded_at for i in built.intervals}
        assert by_eid[("chp_1__sec_1", d("2024-01-01"))] == T0
        assert by_eid[("chp_1__sec_2", d("2024-01-01"))] == T1
        assert by_eid[("chp_1__sec_2", d("2024-06-01"))] == T1
        expression = {e.source_ref: e.recorded_at for e in built.expressions}
        assert expression == {"one": T1, "two": T1}

    def test_a_reissue_for_a_stored_date_flips_the_choice_and_withdraws_nothing(self) -> None:
        old = text(START, [sec(1, HARBOUR)], ref="old", published_on=d("2020-02-11"))
        first = build([old], now=T0)
        reissue = text(START, [sec(1, PILOT)], ref="new", published_on=d("2020-09-03"))
        built = build([old, reissue], now=T1, previous=first)
        (expression,) = built.expressions
        assert (expression.source_ref, expression.recorded_at) == ("new", T1)
        assert built.withdrawals == ()
        assert in_force(built, START) == {"chp_1__sec_1": PILOT}

    def test_rebuilding_unchanged_texts_changes_nothing(self) -> None:
        texts = [text(d("2024-01-01"), [sec(1, HARBOUR)], ref="one")]
        first = build(texts, now=T0)
        again = build(texts, now=T2, previous=first)
        assert again == first

    def test_a_row_replaced_under_the_same_key_is_not_a_withdrawal(self) -> None:
        first = build([text(d("2024-01-01"), [sec(1, HARBOUR)], ref="one")], now=T0)
        again = build([text(d("2024-01-01"), [sec(1, PILOT)], ref="one")], now=T1, previous=first)
        assert again.withdrawals == ()
        assert again.intervals[0].recorded_at == T1


class TestInputChecks:
    def test_a_reference_without_a_hash_is_refused(self) -> None:
        with pytest.raises(ValueError, match="together"):
            build([text(d("2024-01-01"), [sec(1, HARBOUR)], source_sha256=None)])

    def test_a_confidence_outside_zero_to_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="between"):
            build([text(d("2024-01-01"), [sec(1, HARBOUR)], confidence=1.5)])

    def test_two_chains_cannot_claim_one_provision_variant(self) -> None:
        plain = text(d("2024-01-01"), [sec(1, HARBOUR, variant="extent:a")], ref="a")
        named = text(d("2024-01-01"), [sec(1, HARBOUR)], ref="b", variant="extent:a")
        with pytest.raises(ValueError, match="share a key"):
            build([plain, named])

    def test_every_row_is_stamped_with_the_work_and_revisions(self) -> None:
        built = build([text(d("2024-01-01"), [sec(1, HARBOUR)], ref="one")])
        rows = [*built.expressions, *built.intervals]
        assert all(
            r.work_id == WORK and r.builder_rev == "2" and r.converter_rev == "c1" for r in rows
        )
        assert [r for r in active(built.intervals, d("2024-01-02"))] == list(built.intervals)


def test_expiry_breaks_provision_continuity() -> None:
    unit = sec(1, HARBOUR)
    later = d("2022-01-01")
    built = build([text(START, [unit], valid_to=START), text(later, [unit])])
    assert spans(built, unit.eid) == [(START, d("2021-04-02")), (later, None)]


def test_chains_with_different_starts_cannot_overlap() -> None:
    unit = sec(1, HARBOUR)
    plain = text(START, [replace(unit, variant="extent:a")])
    named = text(d("2022-01-01"), [unit], variant="extent:a")
    with pytest.raises(ValueError, match="overlap"):
        build([plain, named])


@pytest.mark.parametrize("change", [{"position": 3}, {"akn_type": "article"}, {"opened_by": "act"}])
def test_unchanged_wording_with_new_metadata_opens_an_interval(change: dict[str, Any]) -> None:
    unit = sec(1, HARBOUR)
    updated = replace(unit, **change)
    built = build([text(START, [unit]), text(d("2022-01-01"), [updated])])
    before, after = built.intervals
    assert before.valid_to == after.valid_from
    assert all(getattr(after, k) == v for k, v in change.items())


def test_changed_converter_opens_an_interval() -> None:
    unit = sec(1, HARBOUR)
    built = build([text(START, [unit]), text(d("2022-01-01"), [unit], converter_rev="next")])
    assert [i.converter_rev for i in built.intervals] == ["c1", "next"]


def test_successful_reconversion_advances_recording_time() -> None:
    source = text(START)
    previous = build([replace(source, akn_xml=None)])
    assert build([source], now=T1, previous=previous).expressions[0].recorded_at == T1


def test_withdrawn_link_names_both_endpoints() -> None:
    early = text(START, [sec(1, HARBOUR)])
    late = text(d("2022-01-01"), [sec(2, HARBOUR)])
    previous = build([early, late])
    changed = build([replace(early, units=[sec(3, HARBOUR)]), late], previous=previous)
    (removed,) = [w for w in changed.withdrawals if w.row_kind == "link"]
    assert (removed.from_eid, removed.akn_eid) == (early.units[0].eid, late.units[0].eid)


def test_latest_kind_controls_matching_after_an_unchanged_interval() -> None:
    unit = sec(1, HARBOUR)
    built = build(
        [
            text(START, [unit]),
            text(d("2022-01-01"), [replace(unit, kind="label")]),
            text(d("2023-01-01"), [sec(1, HARBOUR, chapter=2)]),
        ]
    )
    assert built.links == ()
    assert spans(built, unit.eid) == [(START, d("2023-01-01"))]


def test_rebuild_keeps_stamps_after_real_confidence_round_trip() -> None:
    texts = [
        text(START, [sec(1, HARBOUR)], confidence=0.7),
        text(d("2022-01-01"), [sec(2, HARBOUR)], confidence=0.7),
    ]
    first = build(texts)

    def stored(confidence: float | None) -> float | None:
        return (
            struct.unpack("!f", struct.pack("!f", confidence))[0]
            if confidence is not None
            else None
        )

    loaded = WorkBuild(
        expressions=tuple(replace(r, confidence=stored(r.confidence)) for r in first.expressions),
        intervals=tuple(replace(r, confidence=stored(r.confidence)) for r in first.intervals),
        links=tuple(replace(r, confidence=stored(r.confidence)) for r in first.links),
    )
    assert loaded != first
    assert build(texts, now=T1, previous=loaded) == first


@pytest.mark.parametrize(
    "before, after",
    [
        ("att_1__sec_1", "att_2__sec_1"),
        ("sec_1", "att_1__sec_1"),
        ("att_1__sec_1", "sec_1"),
        ("att_1__att_2__sec_1", "att_3__att_2__sec_1"),
        ("att_1__att_2__sec_1", "att_1__att_3__sec_1"),
    ],
)
def test_independently_numbered_attachments_do_not_match(before: str, after: str) -> None:
    first = replace(sec(1, HARBOUR), eid=before)
    second = replace(sec(1, LIGHTS), eid=after)
    assert build([text(START, [first]), text(d("2022-01-01"), [second])]).links == ()


def test_grouping_changes_within_an_attachment_keep_the_number_path() -> None:
    first = replace(sec(1, HARBOUR), eid="chp_1__att_1__chp_2__sec_1")
    second = replace(first, eid="chp_3__att_1__chp_4__sec_1")
    (link,) = build([text(START, [first]), text(d("2022-01-01"), [second])]).links
    assert link.rule == "same_number"
