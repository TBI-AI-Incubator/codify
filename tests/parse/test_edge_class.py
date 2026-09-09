"""Invariants for `cross_references.edge_class`, classification of a
cross-reference by its role in the legal graph."""

from __future__ import annotations

import uuid

from codify.akn.references import AmendmentReference, Citation, CrossReference
from codify.storage.mappers import _ref_to_row


def _base_offsets() -> dict[str, object]:
    return {"start_offset": 0, "end_offset": 10, "text_snippet": "section 1"}


def test_citation_maps_to_freetext_reference() -> None:
    row = _ref_to_row(
        Citation(target_uri="/akn/xx/act/2020/1", **_base_offsets()),
        source_provision_id=uuid.uuid4(),
    )
    assert row.ref_type == "citation"
    assert row.edge_class == "freetext_reference"


def test_cross_reference_maps_to_freetext_reference() -> None:
    row = _ref_to_row(
        CrossReference(target_eid="sec_1__para_2", **_base_offsets()),
        source_provision_id=uuid.uuid4(),
    )
    assert row.ref_type == "cross_reference"
    assert row.edge_class == "freetext_reference"


def test_amendment_reference_maps_to_mod_textual() -> None:
    for op_name in ("insert", "delete", "replace", "renumber"):
        row = _ref_to_row(
            AmendmentReference(
                amends_uri="/akn/xx/act/2020/1",
                operation=op_name,  # type: ignore[arg-type]
                **_base_offsets(),
            ),
            source_provision_id=uuid.uuid4(),
        )
        assert row.ref_type == f"amendment_{op_name}"
        assert row.edge_class == "mod_textual", op_name
