"""Synthetic texts and readers for the history builder tests."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any

from codify.history.build import SourceText, Unit, WorkBuild, build_work

WORK = uuid.UUID(int=1)
JURISDICTION = uuid.UUID(int=2)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 6, 1, tzinfo=UTC)
T2 = datetime(2026, 11, 23, tzinfo=UTC)

HARBOUR = (
    "A vessel entering the harbour shall pay the berthing fee set out in the schedule "
    "before it leaves the quay."
)
PILOT = "The harbour master may require a pilot on board any vessel longer than forty metres."
LIGHTS = "Every vessel at anchor between sunset and sunrise shall show the lights prescribed."


def sec(number: int, text: str, *, chapter: int = 1, **kw: Any) -> Unit:
    return Unit(
        eid=f"chp_{chapter}__sec_{number}",
        akn_type="section",
        position=number,
        text=text,
        **kw,
    )


def text(
    start: date | None,
    units: Sequence[Unit] = (),
    *,
    ref: str | None = None,
    sha: str | None = None,
    **kw: Any,
) -> SourceText:
    ref = ref if ref is not None else f"xa/{start}"
    args: dict[str, Any] = {
        "language": "eng",
        "valid_from": start,
        "source_ref": ref,
        "source_sha256": sha or hashlib.sha256(ref.encode()).hexdigest(),
        "akn_xml": "<akomaNtoso/>",
        "units": tuple(units),
        "converter_rev": "c1",
    }
    args.update(kw)
    return SourceText(**args)


def build(
    texts: Iterable[SourceText],
    *,
    now: datetime = T0,
    previous: WorkBuild | None = None,
    **kw: Any,
) -> WorkBuild:
    return build_work(WORK, JURISDICTION, list(texts), now=now, previous=previous, **kw)


def active(rows: Iterable[Any], day: date) -> list[Any]:
    return [r for r in rows if r.valid_from <= day and (r.valid_to is None or day < r.valid_to)]


def in_force(
    built: WorkBuild, day: date, variant: str = "", language: str = "eng"
) -> dict[str, str]:
    return {
        r.akn_eid: r.text
        for r in active(built.intervals, day)
        if r.variant == variant and r.language == language
    }


def spans(built: WorkBuild, eid: str, variant: str = "") -> list[tuple[date, date | None]]:
    return [
        (r.valid_from, r.valid_to)
        for r in built.intervals
        if r.akn_eid == eid and r.variant == variant
    ]


def d(text_: str) -> date:
    return date.fromisoformat(text_)
