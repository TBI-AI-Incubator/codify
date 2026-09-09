"""Pins hold a correction across re-translations; the point is that a reviewed
fix stops decaying, so these tests are about loss, not about matching."""

from __future__ import annotations

import json
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest

from codify.translate.regression_pins import (
    PinFileError,
    check_pins,
    load_pins,
    pins_for,
)

WORK = "/akn/xa/act/1992/7"
NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _doc(text: str, eid: str = "sec_15__subsec_2__p_1") -> str:
    return (
        f'<akomaNtoso xmlns="{NS}"><act><body><section eId="sec_15">'
        f'<content><p eId="{eid}">{text}</p></content>'
        "</section></body></act></akomaNtoso>"
    )


def test_a_translation_that_kept_the_correction_passes() -> None:
    kept = _doc("shall be punished by imprisonment for a term of three to fifteen years")
    assert check_pins(kept, WORK, "eng") == []


def test_a_translation_that_dropped_the_correction_is_reported() -> None:
    """The synthetic Legislation Act section 15 offence lost the penalty verb
    across successive releases, each time found by a fresh review, not the pipeline."""
    lost = _doc(
        "is liable on conviction on indictment to a fine not exceeding five thousand pounds"
    )
    violations = check_pins(lost, WORK, "eng")
    assert len(violations) == 1
    assert violations[0].pin.must_contain == "imprisonment"
    assert "imprisonment" in violations[0].describe()
    assert "sec_15__subsec_2__p_1" in violations[0].describe()


def test_a_missing_eid_is_a_violation_not_a_pass() -> None:
    """A dropped provision must not read as a kept correction."""
    violations = check_pins(_doc("text", eid="art_99"), WORK, "eng")
    assert len(violations) == 1
    assert violations[0].found is None
    assert "eId absent" in violations[0].describe()


def test_matching_is_case_insensitive_but_not_fuzzy() -> None:
    assert check_pins(_doc("punished by IMPRISONMENT for a term"), WORK, "eng") == []
    assert check_pins(_doc("punished by imprisonments"), WORK, "eng") == []  # substring
    assert len(check_pins(_doc("punished by incarceration"), WORK, "eng")) == 1


def test_rewording_around_the_pinned_phrase_stays_free() -> None:
    """Pins assert a phrase, not a provision, so ordinary variation is allowed."""
    for wording in (
        "shall be punished by imprisonment for three to fifteen years",
        "is liable to imprisonment for a period of between three and fifteen years",
    ):
        assert check_pins(_doc(wording), WORK, "eng") == []


def test_unparseable_xml_is_not_reported_as_clean() -> None:
    """A check that could not run must never read as nothing lost."""
    assert check_pins("<not xml", WORK, "eng") is None


def test_a_work_with_no_pins_returns_empty_not_none() -> None:
    assert check_pins(_doc("anything"), "/akn/zz/act/1999/1", "eng") == []


def test_pins_do_not_cross_languages() -> None:
    assert pins_for(WORK, "heb") == ()
    assert check_pins(_doc("no penalty verb here"), WORK, "heb") == []


def test_every_shipped_pin_carries_its_reason() -> None:
    """A pin without a stated reason cannot be re-judged later, and this file is
    the record of what the corpus guarantees."""
    for pin in load_pins():
        assert pin.work_uri.startswith("/akn/"), pin
        assert pin.language and pin.eid and pin.must_contain, pin
        assert len(pin.why) > 25, f"{pin.eid} needs a real reason, got {pin.why!r}"


def test_pin_files_are_valid_jsonl_with_the_expected_schema() -> None:
    expected = {"work_uri", "language", "eid", "must_contain", "why"}
    seen = 0
    for entry in resources.files("codify.translate.pins").iterdir():
        if not entry.name.endswith(".jsonl"):
            continue
        for line in entry.read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert set(json.loads(line)) == expected, line
                seen += 1
    assert seen >= 5


def test_pins_are_unique_per_work_language_eid() -> None:
    """One slot, one pin. Violations are reported per eId, so a second pin on
    the same slot marks both as violated when only one phrase has gone."""
    keys = [(p.work_uri, p.language, p.eid) for p in load_pins()]
    assert len(keys) == len(set(keys))


_DELIVERED = Path(__file__).parents[1] / "fixtures" / "synthetic-atlantis-1992.akn.xml"


def test_a_shipped_pin_resolves_against_a_delivered_document() -> None:
    """A pin keyed on an eId that does not exist blocks every future
    translation of that law, and shape tests cannot tell the difference. This is
    a synthetic pipeline-emitted document that every shipped pin must resolve in."""
    violations = check_pins(_DELIVERED.read_text(encoding="utf-8"), WORK, "eng")
    assert violations == [], [v.describe() for v in violations]


def test_a_quoted_amendment_does_not_satisfy_a_pin() -> None:
    """An amending act quotes the provision it amends; grading the quotation
    would pass a law whose own text lost the phrase."""
    quoted = (
        f'<akomaNtoso xmlns="{NS}"><act><body>'
        '<mod><quotedStructure><p eId="sec_15__subsec_2__p_1">punished by imprisonment</p>'
        "</quotedStructure></mod>"
        '<p eId="sec_15__subsec_2__p_1">punished for three years</p>'
        "</body></act></akomaNtoso>"
    )
    violations = check_pins(quoted, WORK, "eng")
    assert len(violations) == 1
    assert violations[0].found == "punished for three years"


def test_a_missing_eid_says_the_pin_may_be_stale() -> None:
    """A dropped provision and a stale pin need different responses, so the
    failure message must not read the same for both."""
    violations = check_pins(_doc("text", eid="art_99"), WORK, "eng")
    assert violations[0].eid_missing
    assert "stale" in violations[0].describe()
    assert "xa.jsonl" in violations[0].describe()


class _FakeEntry:
    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self._text = text

    def read_text(self, encoding: str = "utf-8") -> str:
        return self._text


class _FakeDir:
    def __init__(self, *entries: _FakeEntry) -> None:
        self._entries = entries

    def iterdir(self) -> tuple[_FakeEntry, ...]:
        return self._entries


def _with_pin_files(monkeypatch: pytest.MonkeyPatch, *entries: _FakeEntry) -> None:
    from codify.translate import regression_pins

    monkeypatch.setattr(regression_pins.resources, "files", lambda _pkg: _FakeDir(*entries))
    load_pins.cache_clear()
    monkeypatch.setattr(load_pins, "cache_clear", load_pins.cache_clear)


@pytest.fixture(autouse=True)
def _restore_pin_cache() -> Iterator[None]:
    yield
    load_pins.cache_clear()


def test_a_malformed_pin_line_names_the_file_and_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pins file exists to be hand-audited, so it is the input most likely
    to be hand-broken; the error has to say where to look."""
    _with_pin_files(monkeypatch, _FakeEntry("xy.jsonl", '{"work_uri": "/akn/x/act/1/1"}\n'))
    with pytest.raises(PinFileError) as excinfo:
        load_pins()
    assert "xy.jsonl line 1" in str(excinfo.value)


def test_unparseable_json_names_the_file_and_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_pin_files(monkeypatch, _FakeEntry("xy.jsonl", '{"a": 1}\nnot json\n'))
    with pytest.raises(PinFileError) as excinfo:
        load_pins()
    assert "line 1" in str(excinfo.value)


def test_an_empty_pin_set_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero pins is a disarmed gate, which must never read as "no corrections
    to hold": every translation would pass unchecked and look verified."""
    _with_pin_files(monkeypatch, _FakeEntry("readme.txt", "not a pins file"))
    with pytest.raises(PinFileError, match="no regression pins"):
        load_pins()


def test_a_quotation_inside_the_pinned_provision_does_not_satisfy_it() -> None:
    """The pin sits on the article, and the article quotes the text it amends.
    Reading the quotation as the document's own words would pass a provision
    whose operative text lost the phrase."""
    amending = (
        f'<akomaNtoso xmlns="{NS}"><act><body>'
        '<section eId="sec_15"><content>'
        '<p eId="sec_15__subsec_2__p_1">The following is substituted: '
        "<mod><quotedStructure><p>punished by imprisonment</p></quotedStructure></mod>"
        " and the penalty is removed.</p>"
        "</content></section>"
        "</body></act></akomaNtoso>"
    )
    violations = check_pins(amending, WORK, "eng")
    assert len(violations) == 1
    assert violations[0].found == "The following is substituted: and the penalty is removed."


def test_text_outside_a_quotation_still_counts() -> None:
    """Only the quotation is skipped; the provision's own words around it are
    the text the pin is asserted against."""
    kept = (
        f'<akomaNtoso xmlns="{NS}"><act><body>'
        '<section eId="sec_15"><content>'
        '<p eId="sec_15__subsec_2__p_1">Replace '
        "<mod><quotedStructure><p>a fine</p></quotedStructure></mod>"
        " with imprisonment for three years.</p>"
        "</content></section>"
        "</body></act></akomaNtoso>"
    )
    assert check_pins(kept, WORK, "eng") == []
