"""A recital whose eId a body row already holds keeps its citations off that row on refresh."""

from __future__ import annotations

import uuid

import pytest
from lxml import etree

from codify.akn._schema import NS
from codify.akn.io import parse_akn
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.enacting import emit_enacting_formula
from codify.storage.mappers import document_to_rows
from codify.storage.refresh_refs import _extract_reference_rows

_SOURCE = (
    "PREAMBLE\n\n  Having regard to {{>/akn/xa/act/1992/7 the Legislation Act, 1992}},\n\n"
    "  HEREBY RESOLVES:\n\n"
    "BODY\n\nSECTION 1\n\n"
    "  As {{>/akn/xa/act/2001/3 the Freedom of Information Act, 2001}} requires.\n"
)


def _decision(recital_eid: str = "recitals__rec_1") -> str:
    akn = parse_to_akn(_SOURCE, "xa", doctype="resolution", date="2024-03-05", number="4")
    root = etree.fromstring(emit_enacting_formula(akn, "xa", "resolution", None).encode())
    recital = root.find(".//akn:recital", NS)
    assert recital is not None
    recital.set("eId", recital_eid)
    return etree.tostring(root, encoding="unicode")


@pytest.mark.asyncio
async def test_a_recitals_citations_land_on_its_own_row() -> None:
    akn = _decision()
    _, _, provisions, _, _, _ = document_to_rows(parse_akn(akn), law_id=uuid.uuid4())
    held = {p.akn_eid: p for p in provisions}
    rows, unmatched = await _extract_reference_rows(
        akn,
        "xa",
        "resolution",
        {eid: p.id for eid, p in held.items()},
        recital_eids=frozenset({"recitals__rec_1"}),
    )
    assert sorted((r.source_provision_id, r.target_uri) for r in rows) == sorted(
        [
            (held["recitals__rec_1"].id, "/akn/xa/act/1992/7"),
            (held["sec_1__content"].id, "/akn/xa/act/2001/3"),
        ]
    )
    assert unmatched == 0


@pytest.mark.asyncio
async def test_a_skipped_recitals_citations_never_land_on_the_body_row() -> None:
    # The recital carries the eId the mapper gives section 1's own text, so it has no row.
    akn = _decision("sec_1__content")
    _, _, provisions, mapped, _, _ = document_to_rows(parse_akn(akn), law_id=uuid.uuid4())
    held = {p.akn_eid: p for p in provisions}
    assert [p.akn_type for p in provisions] == ["paragraph"]
    assert [(r.source_provision_id, r.target_uri) for r in mapped] == [
        (held["sec_1__content"].id, "/akn/xa/act/2001/3")
    ]

    rows, unmatched = await _extract_reference_rows(
        akn, "xa", "resolution", {eid: p.id for eid, p in held.items()}
    )
    assert [(r.source_provision_id, r.target_uri) for r in rows] == [
        (held["sec_1__content"].id, "/akn/xa/act/2001/3")
    ]
    assert unmatched == 1
