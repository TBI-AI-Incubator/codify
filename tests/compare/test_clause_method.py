"""Post-validation floor, the table never carries actionable=true && clause_method=na."""

from __future__ import annotations

import pytest

from codify.compare.comparator import _enforce_clause_method_contract
from codify.compare.types import LLMVerdict


def _verdict(*, actionable: bool, clause_method: str) -> LLMVerdict:
    return LLMVerdict(
        verdict="partial",
        confidence=0.7,
        note="…",
        citations=[],
        actionable=actionable,
        provision_kind="obligation" if actionable else "recital",
        clause_method=clause_method,  # type: ignore[arg-type]
    )


def test_coerces_actionable_na_to_normal() -> None:
    v = _verdict(actionable=True, clause_method="na")
    _enforce_clause_method_contract(v, directive_eid="art_18__para_1")
    assert v.clause_method == "normal"


def test_coerces_nonactionable_non_na_to_na() -> None:
    v = _verdict(actionable=False, clause_method="normal")
    _enforce_clause_method_contract(v, directive_eid="art_1")
    assert v.clause_method == "na"


def test_preserves_nonactionable_na() -> None:
    v = _verdict(actionable=False, clause_method="na")
    _enforce_clause_method_contract(v, directive_eid="rec_3")
    assert v.clause_method == "na"


@pytest.mark.parametrize("method", ["normal", "optional", "discretionary"])
def test_preserves_actionable_valid_classifications(method: str) -> None:
    v = _verdict(actionable=True, clause_method=method)
    _enforce_clause_method_contract(v, directive_eid="art_9__para_3")
    assert v.clause_method == method
