"""Lens registry, register / get / list_names."""

from __future__ import annotations

from typing import cast

import pytest

from codify.lenses import Lens, get, list_names, register
from codify.lenses.registry import _reset_for_tests


class _StubLens:
    name = "stub"
    taxonomy_models: list[type] = []

    async def scan(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if False:
            yield None

    async def match_schemes(self, findings):  # type: ignore[no-untyped-def]
        return []


@pytest.fixture(autouse=True)
def _reset():  # type: ignore[no-untyped-def]
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_register_then_get() -> None:
    lens = cast(Lens, _StubLens())
    register(lens)
    assert get("stub") is lens


def test_register_duplicate_raises() -> None:
    register(cast(Lens, _StubLens()))
    with pytest.raises(ValueError, match="already registered"):
        register(cast(Lens, _StubLens()))


def test_get_missing_raises() -> None:
    with pytest.raises(KeyError):
        get("missing")


def test_list_names_sorted() -> None:
    class Z:
        name = "zebra"
        taxonomy_models: list[type] = []

        async def scan(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if False:
                yield None

        async def match_schemes(self, findings):  # type: ignore[no-untyped-def]
            return []

    register(cast(Lens, Z()))
    register(cast(Lens, _StubLens()))
    assert list_names() == ["stub", "zebra"]


def test_discovers_entry_point_lenses(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lens advertised on the codify.lenses entry-point group is discovered
    lazily, without this package importing it."""
    import codify.lenses.registry as reg

    class _EpLens(_StubLens):
        name = "from_ep"

    class _FakeEP:
        name = "from_ep"

        def load(self):  # type: ignore[no-untyped-def]
            return _EpLens

    monkeypatch.setattr(
        reg,
        "entry_points",
        lambda group: [_FakeEP()] if group == "codify.lenses" else [],
    )
    reg._reset_for_tests()
    assert "from_ep" in list_names()
    assert get("from_ep").name == "from_ep"


def test_discovery_skips_already_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery never double-registers a name a built-in already claimed."""
    import codify.lenses.registry as reg

    class _EpLens(_StubLens):
        name = "stub"

    class _FakeEP:
        name = "stub"

        def load(self):  # type: ignore[no-untyped-def]
            return _EpLens

    monkeypatch.setattr(
        reg,
        "entry_points",
        lambda group: [_FakeEP()] if group == "codify.lenses" else [],
    )
    reg._reset_for_tests()
    builtin = cast(Lens, _StubLens())
    register(builtin)
    assert get("stub") is builtin  # discovery did not overwrite it


def test_discovery_skips_broken_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken entry point is skipped; healthy lenses still load."""
    import codify.lenses.registry as reg

    class _GoodLens(_StubLens):
        name = "good"

    class _GoodEP:
        name = "good"
        value = "pkg:GoodLens"

        def load(self):  # type: ignore[no-untyped-def]
            return _GoodLens

    class _BrokenEP:
        name = "broken"
        value = "pkg:missing"

        def load(self):  # type: ignore[no-untyped-def]
            raise ImportError("boom")

    monkeypatch.setattr(
        reg,
        "entry_points",
        lambda group: [_BrokenEP(), _GoodEP()] if group == "codify.lenses" else [],
    )
    reg._reset_for_tests()
    assert list_names() == ["good"]  # broken one skipped, not fatal


def test_discovery_skips_invalid_lens(monkeypatch: pytest.MonkeyPatch) -> None:
    """A factory returning a non-Lens object is skipped, not registered."""
    import codify.lenses.registry as reg

    class _BadEP:
        name = "bad"
        value = "pkg:not_a_lens"

        def load(self):  # type: ignore[no-untyped-def]
            return object  # object() has no .name / .scan

    monkeypatch.setattr(
        reg,
        "entry_points",
        lambda group: [_BadEP()] if group == "codify.lenses" else [],
    )
    reg._reset_for_tests()
    assert list_names() == []
