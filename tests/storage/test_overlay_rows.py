"""Which held amendment effects a rederive keeps as overlay."""

from __future__ import annotations

import uuid
from datetime import date

from codify.storage.models import AmendmentEffect
from codify.storage.repository import _overlay_rows


def _effect(when: date | None = None, **extra: object) -> AmendmentEffect:
    return AmendmentEffect(
        version_id=uuid.uuid4(),
        source_akn_wid="art_1",
        target_frbr_uri="/akn/xx/act/2000/1/main#art_1",
        akn_category="textual",
        akn_action="substitution",
        authority_uri="https://example.org/a/1",
        in_force_date=when,
        **extra,
    )


def test_a_date_the_akn_still_declares_is_derived_not_overlay() -> None:
    held = _effect(date(2004, 6, 1))
    assert _overlay_rows([held], [_effect(date(2004, 6, 1))]) == []


def test_a_date_the_akn_does_not_declare_stays_overlay() -> None:
    held = _effect(date(2004, 6, 1))
    assert _overlay_rows([held], [_effect(date(2005, 1, 1))]) == [held]


def test_dated_and_undated_twins_are_both_derived() -> None:
    held = _effect(date(2004, 6, 1))
    derived = [_effect(date(2004, 6, 1)), _effect(None)]
    assert _overlay_rows([held], derived) == []


def test_publisher_rows_are_always_kept() -> None:
    held = _effect(date(2004, 6, 1), publisher_effect_id="p1")
    assert _overlay_rows([held], [_effect(date(2004, 6, 1))]) == [held]
