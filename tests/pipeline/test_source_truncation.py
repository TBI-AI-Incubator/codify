"""A source that stops short of its own contents list.

The coverage gate cannot see this: it compares markers captured against markers
the text declares, and both come from the same text, so cutting the tail shrinks
numerator and denominator together and the ratio stays clean. What survives the
cut is the contents listing, whose entries have no body under them, and that
asymmetry is the measurement here.

Synthetic jurisdiction throughout: the shape is the subject, not any corpus.
"""

from __future__ import annotations

import contextlib

import pytest

from codify.pipeline.enrich.anchors import cached_regex, scan_anchors_with_ambiguity

COUNTRY = "xz"
DOCTYPE = "act"


def _document(listed: int, bodied: int) -> str:
    """A contents listing of `listed` units above a body carrying `bodied` of them.

    Shaped so the cover recogniser accepts it: a contents keyword and a dense run of
    line-anchored markers. Without that the check is silent by design, so a fixture
    that skips it would test nothing."""
    lines = ["AN ACT OF THE ASSEMBLY", "", "TABLE OF CONTENTS", ""]
    lines += [f"Article {i}. Heading number {i}" for i in range(1, listed + 1)]
    lines += ["", "BODY", ""]
    for i in range(1, bodied + 1):
        lines += [f"Article {i}. Heading number {i}", f"  Body text for article {i}.", ""]
    return "\n".join(lines) + "\n"


def _tail(text: str) -> dict | None:
    scan = scan_anchors_with_ambiguity(
        text, cached_regex(COUNTRY, DOCTYPE), country=COUNTRY, doctype=DOCTYPE
    )
    return next((sp.detail for sp in scan.ambiguity if sp.kind == "untwinned_tail"), None)


def test_a_truncated_source_declares_the_units_it_lacks() -> None:
    detail = _tail(_document(listed=8, bodied=3))
    assert detail is not None, "the contents entries with no body went unreported"
    # Two paired, because only a contents twin is evidence of a listing; the third
    # section reads as a sequence restatement, which disproves the premise.
    assert detail["twinned"] == 2
    assert detail["present"] == 8
    assert detail["untwinned"] == ["3", "4", "5", "6", "7", "8"]


def test_a_complete_document_declares_nothing() -> None:
    """Every listed unit has a body, so every number is seen twice."""
    assert _tail(_document(listed=8, bodied=8)) is None


def test_a_document_with_no_contents_list_is_not_judged() -> None:
    """Silence, not a pass: with nothing listed there is no evidence either way,
    and treating that as complete would make the check a coin toss on most sources."""
    assert _tail(_document(listed=0, bodied=8)) is None


def test_a_short_complete_instrument_still_passes() -> None:
    """The second DoD line: a one-section decree is whole, and any check keyed on
    absolute length refuses it."""
    assert _tail(_document(listed=1, bodied=1)) is None


def test_a_scattered_gap_is_not_a_truncation() -> None:
    """Only a tail counts. A listing that names units the body never had, in the
    middle rather than at the end, is a partial listing, which is common."""
    text = _document(listed=4, bodied=4).replace(
        "Article 2. Heading number 2\n  Body text for article 2.\n\n", "", 1
    )
    detail = _tail(text)
    assert detail is None, f"a mid-document gap was read as truncation: {detail}"


@pytest.mark.parametrize("bodied", [2, 3, 4])
def test_the_share_absent_tracks_where_the_cut_fell(bodied: int) -> None:
    """The deeper the cut, the more of the listing is left unbodied."""
    detail = _tail(_document(listed=8, bodied=bodied))
    assert detail is not None
    assert detail["untwinned_count"] == 8 - detail["twinned"]
    assert detail["untwinned_count"] >= 8 - bodied


class _NeverCalledLLMClient:
    """Any call is a failure: the gate sits ahead of body-fill, so reaching the
    model means a truncated source was about to be structured."""

    async def chat(self, *a, **k):
        raise AssertionError("gate must fire before the structurer runs")

    async def chat_stream(self, *a, **k):
        raise AssertionError("gate must fire before the structurer runs")

    async def chat_schema(self, *a, **k):
        raise AssertionError("gate must fire before the structurer runs")


def _with_floor(monkeypatch: pytest.MonkeyPatch, floor: float = 0.25) -> None:
    """Arm the gate for this jurisdiction. It ships disabled: the separating value was
    measured on the corpora that declare it, and a corpus that has not measured its own
    cannot borrow one."""
    from codify.jurisdictions import load_config
    from codify.pipeline.enrich import structure as structure_mod

    config = load_config(COUNTRY)
    assert config is not None
    armed = config.model_copy(update={"source_truncation_floor": floor})
    monkeypatch.setattr(structure_mod, "load_config", lambda country: armed)


@pytest.mark.asyncio
async def test_the_gate_is_silent_where_no_floor_is_declared() -> None:
    """Off by default, so shipping this cannot refuse a corpus nobody measured."""
    from codify.pipeline.enrich import structure as structure_mod

    traces: list = []
    with contextlib.suppress(Exception):
        await structure_mod.text_to_bluebell_scaffolded(
            _document(listed=8, bodied=2),
            client=_NeverCalledLLMClient(),
            country=COUNTRY,
            doctype=DOCTYPE,
            halt_policy="land",
            on_scan=traces.append,
        )
    assert traces, "the scan trace never fired"
    assert not [h for h in traces[0].halts if h.gate == "source_truncated"]


@pytest.mark.asyncio
async def test_the_structurer_refuses_a_truncated_source_when_it_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under `fail` there is nothing to land onto, so the run stops before the
    model sees a document that is not all there."""
    from codify.pipeline.enrich import structure as structure_mod

    _with_floor(monkeypatch)
    with pytest.raises(structure_mod.SourceTruncationError) as excinfo:
        await structure_mod.text_to_bluebell_scaffolded(
            _document(listed=8, bodied=2),
            client=_NeverCalledLLMClient(),
            country=COUNTRY,
            doctype=DOCTYPE,
            halt_policy="fail",
        )
    err = excinfo.value
    # Both numbers on the error, so a refusal can be argued with.
    assert err.listed == 8
    assert err.bodied == 1
    assert str(err.listed) in str(err) and str(err.bodied) in str(err)
    assert err.ratio > err.threshold


@pytest.mark.asyncio
async def test_a_truncated_source_lands_blocking_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The copy held is the best one there is, and a blocking version names the gap
    where a failed run leaves nothing to find. The halt rides the same durable column
    as every other, so it cannot re-grade clean and the recovery selector finds it."""
    from codify.pipeline.enrich import structure as structure_mod

    _with_floor(monkeypatch)
    traces: list = []
    with contextlib.suppress(Exception):
        # The body-fill needs a model; the gate runs before it, and the halt is on
        # the trace either way. Suppressing keeps the test on the gate, not the LLM.
        await structure_mod.text_to_bluebell_scaffolded(
            _document(listed=8, bodied=2),
            client=_NeverCalledLLMClient(),
            country=COUNTRY,
            doctype=DOCTYPE,
            halt_policy="land",
            on_scan=traces.append,
        )
    assert traces, "the scan trace never fired, so the gate was not reached"
    truncation = [h for h in traces[0].halts if h.gate == "source_truncated"]
    assert truncation, f"the truncated source did not land a halt: {traces[0].halts}"
    halt = truncation[0]
    assert halt.expected == 8
    assert halt.captured == 1
    assert "8" in halt.detail and "1" in halt.detail


def test_a_listing_mostly_bodied_stays_under_a_measured_floor() -> None:
    """One entry short of a full body is a scanner miss, not a truncated file. The
    floor separates them and lives in jurisdiction config, so a corpus that has not
    measured its own value runs with the check off rather than on someone else's."""
    detail = _tail(_document(listed=12, bodied=11))
    assert detail is not None, "the fixture no longer produces an unbodied entry"
    share = detail["untwinned_count"] / detail["present"]
    assert share < 0.25


def test_numbering_that_restarts_per_container_still_shows_its_tail() -> None:
    """Numbers collapse across containers. Reading the tail from numbers alone let a
    complete chapter pair the numbers a truncated chapter was missing, so a document
    two thirds absent showed nothing. The tail is read per container instead."""
    lines = ["AN ACT OF THE ASSEMBLY", "", "TABLE OF CONTENTS", ""]
    for chapter in (1, 2):
        lines.append(f"Chapter {chapter}. Chapter heading {chapter}")
        lines += [f"Article {i}. Heading {chapter}.{i}" for i in range(1, 6)]
    lines += ["", "BODY", ""]
    # Chapter one complete, chapter two cut after its second section.
    for chapter, last in ((1, 5), (2, 2)):
        lines += ["", f"Chapter {chapter}. Chapter heading {chapter}", ""]
        for i in range(1, last + 1):
            lines += [f"Article {i}. Heading {chapter}.{i}", f"  Body {chapter}.{i}.", ""]

    detail = _tail("\n".join(lines) + "\n")
    assert detail is not None, "a complete chapter paired the numbers a cut one lacks"
    assert detail["untwinned"] == ["2", "3", "4", "5"]


def test_the_tail_is_not_read_from_offsets_alone() -> None:
    """A paired unit survives at its body position and an unpaired one at its contents
    position, so ordering the tail by offset puts the missing units first and finds
    nothing. This is the flat case that ordering would have broken."""
    detail = _tail(_document(listed=8, bodied=3))
    assert detail is not None
    assert detail["untwinned_count"] == 6


def test_an_annex_restarting_at_one_is_not_a_truncated_document() -> None:
    """A duplicate proves a number was seen twice, not that a listing exists. Reading
    the pair alone, a complete act with an annex restarting at 1 reported most of
    itself absent and, landing, sat blocking forever telling the operator to fetch a
    copy that is not missing."""
    lines = ["AN ACT OF THE ASSEMBLY", "", "BODY", ""]
    for i in range(1, 9):
        lines += [f"Article {i}. Heading number {i}", f"  Body text for article {i}.", ""]
    lines += ["", "ANNEX", ""]
    for i in range(1, 4):
        lines += [f"Article {i}. Annex heading {i}", f"  Annex text {i}.", ""]

    assert _tail("\n".join(lines) + "\n") is None


def test_a_restated_first_article_is_not_a_truncated_document() -> None:
    """The same premise from the other direction: one unit restated late in the body
    duplicates a number with nothing listed anywhere."""
    lines = ["AN ACT OF THE ASSEMBLY", "", "BODY", ""]
    for i in range(1, 9):
        lines += [f"Article {i}. Heading number {i}", f"  Body text for article {i}.", ""]
    lines += ["Article 1. Heading number 1", "  Restated in full.", ""]

    assert _tail("\n".join(lines) + "\n") is None
