"""The mapper stamps the exclusion flag on every provision row it writes."""

from __future__ import annotations

import uuid

from codify.akn import Article, Point
from codify.akn.document import Document
from codify.storage.mappers import document_to_rows


def test_rows_carry_exclusion_flag() -> None:
    article = Article(
        akn_eid="art_1",
        akn_type="article",
        position=0,
        text="The Authority shall:",
        children=[
            Point(akn_eid="art_1__point_a", akn_type="point", position=0, text="keep a register."),
            Point(
                akn_eid="art_1__point_b",
                akn_type="point",
                position=1,
                text="[TIFF not transcribed: annex-1.tif]",
            ),
        ],
    )
    doc = Document(
        id=uuid.uuid4(),
        frbr_work_uri="/akn/xz/act/2020/1",
        frbr_expression_uri="/akn/xz/act/2020/1/eng@2020",
        language="eng",
        expression_date="2020-01-01",
        body=[article],
    )
    _, _, provisions, *_ = document_to_rows(doc, law_id=uuid.uuid4())
    flags = {p.akn_eid: p.excluded_from_pool for p in provisions}
    assert flags["art_1__intro"] is False
    assert flags["art_1__point_a"] is False
    assert flags["art_1__point_b"] is True
