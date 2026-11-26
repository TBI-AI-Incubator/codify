"""After a build, the spans for every date reproduce the text in force on that date."""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from codify.history.build import SourceText, Unit
from tests.history._support import active, build, d

WORDINGS = (
    "A vessel entering the harbour shall pay the berthing fee.",
    "The harbour master may require a pilot on board any vessel.",
    "Every vessel at anchor shall show the lights prescribed.",
    "No vessel shall discharge ballast within the harbour limits.",
)
EIDS = [f"chp_{c}__sec_{n}" for c in (1, 2) for n in (1, 2, 3)]
VARIANTS = ("", "", "extent:a")


@st.composite
def works(draw: st.DrawFn) -> list[SourceText]:
    starts = draw(st.lists(st.integers(0, 400), min_size=1, max_size=7, unique=True))
    out = []
    for offset in sorted(starts):
        converted = draw(st.booleans()) or draw(st.booleans())
        units = []
        if converted:
            for eid in draw(st.lists(st.sampled_from(EIDS), unique=True, max_size=len(EIDS))):
                units.append(
                    Unit(
                        eid=eid,
                        akn_type="section",
                        position=len(units),
                        text=draw(st.sampled_from(WORDINGS)),
                        variant=draw(st.sampled_from(VARIANTS)),
                    )
                )
        end = d("2024-01-01") + timedelta(days=offset + draw(st.integers(0, 30)))
        out.append(
            SourceText(
                language="eng",
                valid_from=d("2024-01-01") + timedelta(days=offset),
                valid_to=end if draw(st.booleans()) else None,
                source_ref=f"ref-{offset}",
                source_sha256=f"{offset:064x}",
                akn_xml="<akomaNtoso/>" if converted else None,
                units=tuple(units),
                converter_rev="c1",
            )
        )
    return out


@settings(max_examples=150, deadline=None, derandomize=True)
@given(texts=works())
def test_the_spans_for_a_date_are_the_text_in_force_on_it(texts: list[SourceText]) -> None:
    built = build(texts)
    first = min(t.valid_from for t in texts if t.valid_from)
    last = max(t.valid_from for t in texts if t.valid_from)
    for offset in range(-2, (last - first).days + 40):
        day = first + timedelta(days=offset)
        started = [t for t in texts if t.valid_from and t.valid_from <= day]
        current = started[-1] if started else None
        if current and current.valid_to and day > current.valid_to:
            current = None
        expected: dict[tuple[str, str], str] = {}
        if current and current.akn_xml:
            for u in current.units:
                expected[(u.variant, u.eid)] = u.text
        got = {(r.variant, r.akn_eid): r.text for r in active(built.intervals, day)}
        assert got == expected, day
        assert len(active(built.intervals, day)) == len(got)
        expression = active(built.expressions, day)
        assert [e.source_ref for e in expression] == ([current.source_ref] if current else [])
