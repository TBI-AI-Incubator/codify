"""A `refersTo` marker excludes nothing where the jurisdiction lists no value:
a document can carry the markup without its jurisdiction opting in."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from codify.akn import Document, Paragraph, Point, Section
from codify.storage.mappers import document_to_rows
from codify.storage.search_tokens import retokenise_version_provisions

_WORK = "/akn/xz/act/2020/1"
_SECTION = uuid.uuid4()
_AKN = """<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act><body>
<section eId="sec_1" refersTo="#nonAdherentDuty"><paragraph eId="sec_1__para_1"
refersTo="#nonAdherentDuty"><content><p>The Committee shall report.</p></content></paragraph>
</section></body></act></akomaNtoso>"""


def test_the_mapper_keeps_marked_units_where_none_is_listed() -> None:
    doc = Document(
        frbr_work_uri=_WORK,
        frbr_expression_uri=f"{_WORK}/eng@2020-01-01",
        language="eng",
        expression_date=date(2020, 1, 1),
        body=[
            Section(
                akn_eid="sec_1",
                akn_type="section",
                position=0,
                refers_to="#nonAdherentDuty",
                text="The Secretariat shall publish it.",
                children=[
                    Paragraph(
                        akn_eid="sec_1__para_1",
                        akn_type="paragraph",
                        position=0,
                        refers_to="#nonAdherentDuty",
                        text="The Committee shall report.",
                        children=[
                            Point(
                                akn_eid="sec_1__para_1__point_a",
                                akn_type="point",
                                position=0,
                                refers_to="#nonAdherentDuty",
                                text="within five years",
                            )
                        ],
                    )
                ],
            )
        ],
    )
    _, _, provisions, *_ = document_to_rows(doc, law_id=uuid.uuid4())
    assert {p.akn_eid: (p.excluded_from_pool, p.exclusion_reason) for p in provisions} == {
        "sec_1__intro": (False, None),
        "sec_1__para_1": (False, None),
        "sec_1__para_1__point_a": (False, None),
    }


class _Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def one_or_none(self) -> Any:
        return self.value

    def all(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _Session:
    """Answers the backfill's reads by table; records its one write."""

    def __init__(self) -> None:
        self.writes: list[list[dict[str, Any]]] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        if params is not None:
            self.writes.append(params)
            return _Result(None)
        if "versions.akn_xml" in sql:
            return _Result(_AKN)
        if "FROM sections" in sql:
            return _Result([(_SECTION, "sec_1")])
        if "FROM provisions" in sql:
            return _Result(
                [(uuid.uuid4(), "The Committee shall report.", "sec_1__para_1", _SECTION)]
            )
        return _Result(("eng", _WORK))


async def test_the_backfill_keeps_marked_units_where_none_is_listed() -> None:
    session = _Session()
    assert await retokenise_version_provisions(session, uuid.uuid4(), reflag=True) == 1  # type: ignore[arg-type]
    assert [(w["excluded"], w["reason"]) for w in session.writes[0]] == [(False, None)]
