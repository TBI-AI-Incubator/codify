"""The UK effects feed: what the parser must not lose.

The two facts these protect are that the export's own date column is always
blank, so the sidecar is the source, and that a full page is a truncation the
feed never announces.
"""

from __future__ import annotations

from datetime import date

import pytest

from codify.acquisition.effects import FeedTruncated
from codify.acquisition.effects.types import EffectsAdapter
from codify.acquisition.effects.uk import (
    RESULTS_CAP,
    crosswalk,
    effects_csv_url,
    parse_effects_csv,
    parse_inforce_feed,
)

_HEAD = (
    "ID,Affected Legislation,Affected Provision(s),Type of Effect,"
    "Affecting Legislation,Affecting Provision,IF Date,Amendment applied to Database\n"
)
_ROW = '"abc123","2018 c. 012","s. 5","words substituted","2020 c. 001","s. 3","","Y"\n'


def test_the_sidecar_dates_the_row() -> None:
    result = parse_effects_csv(_HEAD + _ROW, {"abc123": "2021-06-01"})
    assert result.effects[0].in_force_date == date(2021, 6, 1)
    assert result.dated == 1


def test_without_the_sidecar_the_row_is_undated_and_counted() -> None:
    """The export leaves the column blank on every row it serves, so a loader
    that skips the second pass stores a corpus nobody can read on a date."""
    result = parse_effects_csv(_HEAD + _ROW, {})
    assert result.effects[0].in_force_date is None
    assert result.skipped["undated"] == 1


def test_the_sidecar_wins_when_both_carry_a_date() -> None:
    """The only case that decides the precedence. With the column blank, which
    is every row the publisher actually serves, either order gives the same
    answer and the rule is untested."""
    row = _ROW.replace('"s. 3","",', '"s. 3","2019-01-01",')
    result = parse_effects_csv(_HEAD + row, {"abc123": "2021-06-01"})
    assert result.effects[0].in_force_date == date(2021, 6, 1)


def test_the_column_is_read_when_the_sidecar_has_nothing() -> None:
    """Kept as a fallback: a feed that starts filling the column should not be
    ignored because it never did before."""
    row = _ROW.replace('"s. 3","",', '"s. 3","2019-01-01",')
    assert parse_effects_csv(_HEAD + row, {}).effects[0].in_force_date == date(2019, 1, 1)


def test_an_unmapped_type_keeps_its_raw_words() -> None:
    row = _ROW.replace("words substituted", "some effect nobody crosswalked")
    result = parse_effects_csv(_HEAD + row, {})
    effect = result.effects[0]
    assert effect.akn_action is None
    assert effect.raw_type == "some effect nobody crosswalked"
    assert result.skipped["unmapped_type"] == 1


def test_the_publisher_id_is_carried() -> None:
    """It is the key a re-read updates against; without it a reload can only
    delete and rebuild."""
    assert parse_effects_csv(_HEAD + _ROW, {}).effects[0].publisher_id == "abc123"


@pytest.mark.parametrize(
    ("raw", "action"),
    [
        ("words substituted", "substitution"),
        ("inserted", "insertion"),
        ("repealed", "repeal"),
        ("coming into force", "entryIntoForce"),
        ("extended", "extensionOfScope"),
        ("modified", "variation"),
    ],
)
def test_the_crosswalk_covers_the_common_verbs(raw: str, action: str) -> None:
    assert crosswalk(raw)[0] == action


def test_the_earliest_in_force_date_wins() -> None:
    xml = f"""<feed xmlns:ukm="{"http://www.legislation.gov.uk/namespaces/metadata"}">
      <ukm:Effect EffectId="key-abc"><ukm:InForceDates>
        <ukm:InForce Date="2022-01-01"/><ukm:InForce Date="2021-01-01"/>
      </ukm:InForceDates></ukm:Effect></feed>""".encode()
    dates, more = parse_inforce_feed(xml)
    assert dates == {"abc": "2021-01-01"}
    assert more is True


def test_an_empty_page_ends_the_paging() -> None:
    xml = b'<feed xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"/>'
    assert parse_inforce_feed(xml) == ({}, False)


def test_the_cap_is_in_the_url_so_the_guard_has_something_to_compare() -> None:
    assert f"results-count={RESULTS_CAP}" in effects_csv_url("ukpga/2018/12")


def test_truncation_is_an_error_type_callers_can_catch() -> None:
    assert issubclass(FeedTruncated, RuntimeError)


# --- The two facts the fetch itself has to get right --------------------------


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    """Serves a scripted feed and records what was asked for."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    async def get(self, url: str) -> _FakeResponse:
        self.asked.append(url)
        for fragment, body in self.pages.items():
            if fragment in url:
                return _FakeResponse(body)
        return _FakeResponse(
            b'<feed xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"/>'
        )


def _feed_page(pairs: list[tuple[str, str]]) -> bytes:
    effects = "".join(
        f'<ukm:Effect EffectId="key-{eid}"><ukm:InForceDates>'
        f'<ukm:InForce Date="{when}"/></ukm:InForceDates></ukm:Effect>'
        for eid, when in pairs
    )
    ns = "http://www.legislation.gov.uk/namespaces/metadata"
    return f'<feed xmlns:ukm="{ns}">{effects}</feed>'.encode()


@pytest.mark.asyncio
async def test_the_paging_keeps_going_past_the_first_page() -> None:
    """A single page would date only the first two hundred effects, and the
    rest would be stored undated with nothing to say why."""
    from codify.acquisition.effects.uk import UkEffectsAdapter

    rows = "".join(
        f'"id{n}","2018 c. 012","s. {n}","inserted","2020 c. 001","s. 1","","Y"\n' for n in (1, 2)
    )
    client = _FakeClient(
        {
            "data.csv": (_HEAD + rows).encode(),
            "page=1": _feed_page([("id1", "2021-01-01")]),
            "page=2": _feed_page([("id2", "2022-01-01")]),
        }
    )
    result = await UkEffectsAdapter(client).fetch("ukpga/2018/12")  # type: ignore[arg-type]
    assert [e.in_force_date for e in result.effects] == [date(2021, 1, 1), date(2022, 1, 1)]
    assert any("page=2" in u for u in client.asked), "the second page was never asked for"


@pytest.mark.asyncio
async def test_an_export_at_the_cap_is_refused() -> None:
    """The publisher returns exactly what was asked for and says nothing about
    the rest, so a full page is a truncation."""
    from codify.acquisition.effects.uk import UkEffectsAdapter

    rows = "".join(
        f'"id{n}","2018 c. 012","s. {n}","inserted","2020 c. 001","s. 1","","Y"\n'
        for n in range(RESULTS_CAP)
    )
    client = _FakeClient({"data.csv": (_HEAD + rows).encode()})
    with pytest.raises(FeedTruncated, match="truncated"):
        await UkEffectsAdapter(client).fetch("ukpga/2018/12")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_quoted_newline_does_not_look_like_truncation() -> None:
    """Counting lines rather than records refuses a complete export whose notes
    field happens to contain a line break."""
    from codify.acquisition.effects.uk import UkEffectsAdapter

    rows = "".join(
        f'"id{n}","2018 c. 012","s. {n}","inserted","2020 c. 001","s.\n1","","Y"\n'
        for n in range(RESULTS_CAP - 1)
    )
    client = _FakeClient({"data.csv": (_HEAD + rows).encode()})
    result = await UkEffectsAdapter(client).fetch("ukpga/2018/12")  # type: ignore[arg-type]
    assert len(result.effects) == RESULTS_CAP - 1


def test_the_work_uris_are_ours_not_the_publishers() -> None:
    """`authority_uri` has to join `laws.frbr_work_uri`, so an adapter converts
    the publisher's own URL rather than passing it through."""
    row = '"abc","2018 c. 012","s. 5","inserted","2020 c. 001","s. 3","","Y"\n'
    effect = parse_effects_csv(_HEAD + row, {}).effects[0]
    assert effect.affected_work is not None
    assert effect.affected_work.startswith("/akn/"), effect.affected_work
    assert effect.affecting_work is not None
    assert effect.affecting_work.startswith("/akn/"), effect.affecting_work


@pytest.mark.asyncio
async def test_fetch_many_answers_for_every_path_asked() -> None:
    """The default is a loop; a publisher that batches overrides it. Either way
    the caller gets one result per path."""
    from codify.acquisition.effects.uk import UkEffectsAdapter

    row = '"a","2018 c. 012","s. 1","inserted","2020 c. 001","s. 1","","Y"\n'
    client = _FakeClient({"data.csv": (_HEAD + row).encode()})
    out = await UkEffectsAdapter(client).fetch_many(["ukpga/2018/1", "ukpga/2018/2"])  # type: ignore[arg-type]
    assert sorted(out) == ["ukpga/2018/1", "ukpga/2018/2"]


@pytest.mark.asyncio
async def test_an_adapter_that_does_not_override_the_batch_still_answers() -> None:
    """The optional method's body is the loop its docstring promises. An empty
    body would have every inheriting adapter return None from a call the
    protocol says answers for each path asked."""

    class _Minimal:
        jurisdiction_code = "xa"
        calls: list[str] = []

        async def fetch(self, publisher_path: str) -> object:
            self.calls.append(publisher_path)
            return publisher_path

        fetch_many = EffectsAdapter.fetch_many

    adapter = _Minimal()
    out = await adapter.fetch_many(["a/1", "b/2"])
    assert out == {"a/1": "a/1", "b/2": "b/2"}
