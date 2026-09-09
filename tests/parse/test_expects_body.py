"""`_expects_body` must treat every container kind as a container, not a leaf.

Wave-2 kind-vocabulary consolidation: structure.py carried its own short
container set that omitted subchapter/subdivision/tome/subpart/subtitle. This is
behaviour-preserving in practice (the retry call sites also gate on
`_anchors_with_body_source`, whose `_TOP_LEVEL_KINDS` never yields those kinds),
but the vocabulary is now single-sourced from `kinds.CONTAINER_KINDS` (plus
`section`, kept for parity) so the two definitions cannot drift."""

from __future__ import annotations

from codify.pipeline.enrich.kinds import CONTAINER_KINDS
from codify.pipeline.enrich.structure import _expects_body


def test_every_canonical_container_is_not_body_bearing() -> None:
    # The bug: subchapter/subdivision/tome/subpart/subtitle were body-bearing.
    for kind in CONTAINER_KINDS:
        assert _expects_body(kind) is False, kind


def test_section_stays_a_container_here() -> None:
    # section is a basic unit in the canonical set, but this empty-check has
    # always treated it as a container; keep that (UK/UA section grouping).
    assert _expects_body("section") is False


def test_basic_units_still_expect_body() -> None:
    for kind in ("article", "paragraph", "point", "subsection"):
        assert _expects_body(kind) is True, kind
