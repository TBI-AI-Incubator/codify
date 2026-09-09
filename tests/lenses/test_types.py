"""Finding + SchemeMatch model invariants."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from codify.lenses import Finding, SchemeMatch


def _finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "lens_run_id": uuid.uuid4(),
        "lens_name": "test",
        "version_id": uuid.uuid4(),
        "provision_eid": "art_1",
        "severity": "low",
        "confidence": 0.5,
        "rationale": "ok",
    }
    base.update(overrides)
    return Finding(**base)


def test_finding_defaults_id_and_payload() -> None:
    f = _finding()
    assert isinstance(f.id, uuid.UUID)
    assert f.payload == {}
    assert f.recommendation is None
    assert f.provision_id is None


def test_finding_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        _finding(unknown="x")


def test_finding_rejects_bad_severity() -> None:
    with pytest.raises(ValidationError):
        _finding(severity="bogus")


def test_finding_clamps_confidence_range() -> None:
    with pytest.raises(ValidationError):
        _finding(confidence=1.5)
    with pytest.raises(ValidationError):
        _finding(confidence=-0.1)


def test_scheme_match_round_trips() -> None:
    sm = SchemeMatch(
        lens_name="x",
        scheme_id="s1",
        confidence=0.9,
        findings=[uuid.uuid4()],
        rationale="match",
    )
    again = SchemeMatch.model_validate(sm.model_dump(mode="json"))
    assert again == sm
