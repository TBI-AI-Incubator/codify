"""The two markers and the skip decision, on both sides."""

from __future__ import annotations

from pathlib import Path

import pytest

from codify.jurisdictions import JURISDICTIONS_DIR, JurisdictionDataMissing

from .conftest import _skip_when_not_shipped

_SHIPPED = "xa"


def test_the_shipped_code_is_present_here() -> None:
    """Unmarked on purpose: a marker test that skips proves nothing about
    whether the shipped branch runs."""
    assert (JURISDICTIONS_DIR / _SHIPPED / "config.json").is_file()


@pytest.mark.jurisdiction(_SHIPPED)
def test_a_shipped_code_runs(request: pytest.FixtureRequest) -> None:
    assert not request.node.get_closest_marker("skip")


@pytest.mark.requires_repo("scripts/reject-assistant-trailers.sh")
def test_a_present_artefact_still_runs() -> None:
    assert (JURISDICTIONS_DIR.parent.parent / "scripts").is_dir()


def test_an_absent_artefact_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository is not the requirement: a tree can be one and still lack the
    file, which is how an sdist reported the hook as letting trailers through."""
    from . import conftest

    monkeypatch.setattr(conftest, "_FULL_CORPUS", False)
    check = conftest._declared_requirements.__wrapped__  # type: ignore[attr-defined]
    with pytest.raises(pytest.skip.Exception):
        check(_RepoRequest("scripts/nothing-ships-this.sh"))


def test_absence_skips_where_the_corpus_is_a_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The decision the two wrappers share, which no marker test reaches."""
    from . import conftest

    monkeypatch.setattr(conftest, "_FULL_CORPUS", False)
    with pytest.raises(pytest.skip.Exception):
        _skip_when_not_shipped(JurisdictionDataMissing("no config for 'zz'"))


def test_absence_still_fails_on_a_full_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Here it is a real defect and must stay one."""
    from . import conftest

    monkeypatch.setattr(conftest, "_FULL_CORPUS", True)
    # Not `pytest.raises`: a skip escaping it reports as skipped, so the guard
    # could vanish and this would stay green.
    try:
        _skip_when_not_shipped(JurisdictionDataMissing("no config for 'zz'"))
    except JurisdictionDataMissing:
        return
    except BaseException as exc:  # noqa: BLE001
        raise AssertionError(f"a full corpus must not skip, got {type(exc).__name__}") from None
    raise AssertionError("absence on a full corpus must raise")


class _Node:
    def __init__(self, marker: str, arg: str) -> None:
        self._marker, self._arg = marker, arg

    def iter_markers(self, name: str) -> list[pytest.Mark]:
        return [pytest.Mark(name, (self._arg,), {})] if name == self._marker else []


class _Request:
    def __init__(self, code: str) -> None:
        self.node = _Node("jurisdiction", code)


class _RepoRequest:
    def __init__(self, rel: str) -> None:
        self.node = _Node("requires_repo", rel)


def test_the_marker_defers_to_the_shared_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    """The marker reads the filesystem itself, so it can answer differently from
    the exception hooks unless it routes through them."""
    from . import conftest

    monkeypatch.setattr(conftest, "_FULL_CORPUS", True)
    check = conftest._declared_requirements.__wrapped__  # type: ignore[attr-defined]
    try:
        check(_Request("zz"))
    except JurisdictionDataMissing:
        return
    except BaseException as exc:  # noqa: BLE001
        raise AssertionError(f"a full corpus must not skip, got {type(exc).__name__}") from None
    raise AssertionError("a marker naming an absent config must raise on a full corpus")


def test_the_marker_skips_where_the_corpus_is_a_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other side of the same call, which cannot be a marked test: this tree
    is the full corpus, where the marker is now meant to raise."""
    from . import conftest

    monkeypatch.setattr(conftest, "_FULL_CORPUS", False)
    check = conftest._declared_requirements.__wrapped__  # type: ignore[attr-defined]
    with pytest.raises(pytest.skip.Exception):
        check(_Request("zz"))


class _Crash:
    def __init__(self, message: str) -> None:
        self.message = message


class _Longrepr:
    def __init__(self, message: str) -> None:
        self.reprcrash = _Crash(message)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("codify.jurisdictions.JurisdictionDataMissing: no config for 'zz'", True),
        ("JurisdictionDataMissing: no config for 'zz'", True),
        # A module that merely names the class while failing at something else.
        ("RuntimeError: JurisdictionDataMissing", False),
        ("NameError: name 'JurisdictionDataMissing' is not defined", False),
        ("ImportError: cannot import name 'x'", False),
    ],
)
def test_only_the_raised_type_turns_a_collection_failure_into_a_skip(
    message: str, expected: bool
) -> None:
    from . import conftest

    assert conftest._crashed_on_absent_config(_Longrepr(message)) is expected


def test_a_longrepr_without_a_crash_is_not_a_skip() -> None:
    """pytest also hands back plain strings and tuples here."""
    from . import conftest

    assert conftest._crashed_on_absent_config("some string") is False


def test_a_shipped_config_that_is_wrong_is_not_absence() -> None:
    """A config that ships and contradicts itself is a defect in every tree, so
    only the absence subclass may reach the skip decision."""
    from codify.jurisdictions import JurisdictionConfig, JurisdictionConfigError
    from codify.pipeline.enrich.anchors import keyword_aliases

    from . import conftest

    config = JurisdictionConfig(
        code="xq", name="Nowhere", tradition=["civil_law"], languages=["en"], document_classes={}
    )
    with pytest.raises(JurisdictionConfigError) as exc:
        keyword_aliases(config, "act")
    assert not isinstance(exc.value, JurisdictionDataMissing), "a wrong config would skip"
    assert (
        conftest._crashed_on_absent_config(
            _Longrepr(f"codify.jurisdictions.{type(exc.value).__name__}: {exc.value}")
        )
        is False
    )


def test_a_malformed_code_is_not_absent_data() -> None:
    """`try_load_config` answers None for a path-like code as well as for a code
    with no file, so classifying every None as absence made input a skip."""
    from codify.jurisdictions import JurisdictionConfigError, load_config

    with pytest.raises(JurisdictionConfigError) as exc:
        load_config("../../etc")
    assert not isinstance(exc.value, JurisdictionDataMissing)


def test_a_missing_artefact_fails_on_a_full_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting the hook must not silence the suite that guards it."""
    from . import conftest

    monkeypatch.setattr(conftest, "_FULL_CORPUS", True)
    check = conftest._declared_requirements.__wrapped__  # type: ignore[attr-defined]
    try:
        check(_RepoRequest("scripts/nothing-ships-this.sh"))
    except AssertionError as exc:
        assert "nothing-ships-this" in str(exc)
        return
    except BaseException as exc:  # noqa: BLE001
        raise AssertionError(f"a full corpus must not skip, got {type(exc).__name__}") from None
    raise AssertionError("a missing artefact on a full corpus must fail")


def test_the_monorepo_signal_follows_the_shipping_rule(tmp_path: Path) -> None:
    """A count would call a large enough open set the monorepo. Only a config
    that does not ship distinguishes them, whatever the number of them."""
    from codify.open_wheel import ships_in_open_wheel

    for code, flags in (("xa", '"synthetic": true'), ("gb", '"public_reference": true')):
        d = tmp_path / code
        d.mkdir()
        (d / "config.json").write_text(
            f'{{"code": "{code}", "name": "N", "tradition": ["civil_law"], '
            f'"languages": ["en"], {flags}}}'
        )
    shipped = list(tmp_path.glob("*/config.json"))
    assert not any(not ships_in_open_wheel(c) for c in shipped), "an open set is not the monorepo"

    withheld = tmp_path / "zz"
    withheld.mkdir()
    (withheld / "config.json").write_text(
        '{"code": "zz", "name": "N", "tradition": ["civil_law"], "languages": ["en"]}'
    )
    assert any(not ships_in_open_wheel(c) for c in tmp_path.glob("*/config.json")), (
        "one withheld config is what makes a tree the monorepo"
    )
