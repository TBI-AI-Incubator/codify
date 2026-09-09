"""The offline DB gate's own guard: prove REQUIRE_DB turns an absent database
into a hard failure, not a silent skip.

`apply_db_gate` is the acting half of the collection hook (`_gate_integration_db`
in conftest calls it with the live reachability probe). Testing it directly
exercises the real raise / skip-mark / count logic, not just the pure policy
mapper, without a database. This test is deliberately NOT `integration`-marked,
so the mechanism it verifies cannot skip it.
"""

from __future__ import annotations

import pytest

from codify.testing import apply_db_gate, integration_gate_action


class _FakeItem:
    """The slice of a pytest item the gate touches: `keywords` membership and
    `add_marker`."""

    def __init__(self, *, integration: bool) -> None:
        self.keywords = {"integration"} if integration else {"unit"}
        self.markers: list = []

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


# ── the policy mapper ───────────────────────────────────────────────────────


def test_policy_reachable_runs_regardless_of_require_db() -> None:
    assert integration_gate_action(reachable=True, require_db=False) == "run"
    assert integration_gate_action(reachable=True, require_db=True) == "run"


def test_policy_offline_maps_skip_or_require_fail() -> None:
    assert integration_gate_action(reachable=False, require_db=False) == "skip"
    assert integration_gate_action(reachable=False, require_db=True) == "require-fail"


# ── the acting logic (what the hook actually does) ──────────────────────────


def test_require_db_without_a_database_hard_fails_not_skips() -> None:
    # CI provisions Postgres and sets REQUIRE_DB=1. If the database is then
    # unreachable the run must ABORT, not skip, a broken CI Postgres cannot pass
    # by skipping the assertions that needed it.
    items = [_FakeItem(integration=True), _FakeItem(integration=False)]
    with pytest.raises(pytest.UsageError, match="REQUIRE_DB"):
        apply_db_gate(items, reachable=False, require_db=True, endpoint="localhost:5999")
    # And it raised before marking anything skipped.
    assert all(not it.markers for it in items)


def test_offline_skips_only_integration_items_and_counts_them() -> None:
    items = [
        _FakeItem(integration=True),
        _FakeItem(integration=True),
        _FakeItem(integration=False),
    ]
    skipped = apply_db_gate(items, reachable=False, require_db=False, endpoint="localhost:5999")
    assert skipped == 2
    assert len(items[0].markers) == 1 and len(items[1].markers) == 1
    assert not items[2].markers  # a non-integration item is left alone
    assert "5999" in items[0].markers[0].kwargs["reason"]  # the skip names the endpoint


def test_reachable_runs_everything_and_marks_nothing() -> None:
    items = [_FakeItem(integration=True)]
    assert apply_db_gate(items, reachable=True, require_db=False) == 0
    assert not items[0].markers
