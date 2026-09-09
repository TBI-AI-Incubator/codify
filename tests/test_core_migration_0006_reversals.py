"""Which objects a downgrade of 0006 removes, at each revision boundary.

Reversing one the product chain owns would break a database carrying both.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "codify/migrations/versions/0006_corpus_schema_after_0123.py"
)


def _module():  # noqa: ANN202 - a loaded migration has no stable type
    spec = importlib.util.spec_from_file_location("_core_0006", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORG_ID = "ALTER TABLE runs DROP COLUMN IF EXISTS org_id"
_TITLE_FN = "DROP FUNCTION IF EXISTS laws_title_tokens_stale()"
_TITLE_TRG = "DROP TRIGGER IF EXISTS laws_title_tokens_stale_trg ON laws"
_EVENTS_TRG = "DROP TRIGGER IF EXISTS events_no_truncate ON events"


@pytest.mark.parametrize(
    ("product", "expected"),
    [
        # 0123 and below cannot reach 0006: `upgrade` refuses, because the
        # product chain would later create these objects unguarded.
        ("", {_ORG_ID, _TITLE_FN, _TITLE_TRG, _EVENTS_TRG}),
        ("0124", {_TITLE_FN, _TITLE_TRG, _EVENTS_TRG}),
        ("0125", {_TITLE_FN, _TITLE_TRG, _EVENTS_TRG}),
        ("0126", {_EVENTS_TRG}),
        ("0127", set()),
        ("0131", set()),  # the demo
    ],
)
def test_only_what_the_product_chain_does_not_own_is_reversed(
    product: str, expected: set[str]
) -> None:
    """Exact statements, not substrings: the function and the trigger share a
    name, so a substring for one also matches the other."""
    statements = set(_module().reversals_for(product))
    for statement in (_ORG_ID, _TITLE_FN, _TITLE_TRG, _EVENTS_TRG):
        assert (statement in statements) is (statement in expected), statement


def test_every_object_the_upgrade_adds_has_a_reversal() -> None:
    """A delta added without one would be left behind on a standalone database."""
    reversed_at_zero = " ".join(_module().reversals_for(""))
    for added in (
        "org_id",
        "ix_runs_org_created",
        "title_tokens",
        "title_search_pipeline_version",
        "title_tsv",
        "laws_title_tsv_idx",
        "laws_title_bm25_idx",
        "laws_title_tokens_stale",
        "events_no_truncate",
    ):
        assert added in reversed_at_zero, f"{added} is added and never reversed"
