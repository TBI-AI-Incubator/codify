"""Mapper position assignment for flattened nested points."""

from __future__ import annotations

import uuid

from codify.akn import Article, Point
from codify.akn.document import Document
from codify.storage.mappers import document_to_rows


def test_nested_points_flatten_with_monotonic_positions() -> None:
    """Points nest in the source (أ has sub-points 1-2, then siblings ب, ج) but
    the v2 schema flattens point>point to siblings under the article. Each
    point's own `position` is local to its parent, so without renumbering the
    sub-points of أ would collide with ب/ج. Assert the flattened provisions get
    a per-article position that preserves document (pre-order) reading order."""
    article = Article(
        akn_eid="art_6",
        akn_type="article",
        position=0,
        text="The committee shall:",  # lead-in -> art_6__intro at position 0
        children=[
            Point(
                akn_eid="art_6__point_a",
                akn_type="point",
                position=0,
                text="",  # empty grouping marker -> dropped, children reparent
                children=[
                    Point(
                        akn_eid="art_6__point_a__point_1",
                        akn_type="point",
                        position=0,
                        text="sub one",
                    ),
                    Point(
                        akn_eid="art_6__point_a__point_2",
                        akn_type="point",
                        position=1,
                        text="sub two",
                    ),
                ],
            ),
            Point(akn_eid="art_6__point_b", akn_type="point", position=1, text="bee"),
            Point(akn_eid="art_6__point_c", akn_type="point", position=2, text="cee"),
        ],
    )
    doc = Document(
        id=uuid.uuid4(),
        frbr_work_uri="/akn/jo/act/2021/20",
        frbr_expression_uri="/akn/jo/act/2021/20/ara@2021",
        language="ara",
        expression_date="2021-01-01",
        body=[article],
    )

    _, sections, provisions, _, _, _ = document_to_rows(doc, law_id=uuid.uuid4())

    art = next(s for s in sections if s.akn_eid == "art_6")
    in_article = sorted((p for p in provisions if p.section_id == art.id), key=lambda p: p.position)
    # Positions are a dense 0..n run with no collisions.
    assert [p.position for p in in_article] == list(range(len(in_article)))
    # Reading order matches the source: intro, أ's subs, then ب, ج.
    assert [p.akn_eid for p in in_article] == [
        "art_6__intro",
        "art_6__point_a__point_1",
        "art_6__point_a__point_2",
        "art_6__point_b",
        "art_6__point_c",
    ]
