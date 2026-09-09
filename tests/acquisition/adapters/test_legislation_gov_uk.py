"""The feed walker against synthetic Atom pages shaped like the publisher's."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from codify.acquisition.adapters.legislation_gov_uk import (
    CheckpointMismatch,
    ShortYearError,
    build_index,
    entry_from_path,
    feed_url,
    harvest_year,
    parse_feed,
)

_NS = (
    'xmlns="http://www.w3.org/2005/Atom" '
    'xmlns:leg="http://www.legislation.gov.uk/namespaces/legislation" '
    'xmlns:openSearch="http://a9.com/-/spec/opensearch/1.1/"'
)


def _page(ids: list[str], *, per_page: int = 20, page: int = 1, facets: str = "") -> bytes:
    entries = "".join(f"<entry><id>http://www.legislation.gov.uk/id/{i}</id></entry>" for i in ids)
    return (
        f"<feed {_NS}><openSearch:itemsPerPage>{per_page}</openSearch:itemsPerPage>"
        f"<leg:page>{page}</leg:page><leg:morePages>{page + 11}</leg:morePages>"
        f"<leg:facets>{facets}</leg:facets>{entries}</feed>"
    ).encode()


_SITE = "http://www.legislation.gov.uk"
_FACETS = (
    "<leg:facetTypes>"
    f'<leg:facetType type="Primary" href="{_SITE}/xpa/data.feed" value="30"/>'
    f'<leg:facetType type="Primary|flag" href="{_SITE}/xpa/data.feed?flag=true" value="3"/>'
    "</leg:facetTypes><leg:facetYears>"
    '<leg:facetYear year="2020" href="…" total="29"/>'
    '<leg:facetYear year="1951" href="…" total="1"/>'
    "</leg:facetYears>"
)


def test_parse_feed_reads_entries_and_facets_not_more_pages() -> None:
    page = parse_feed(_page(["xpa/2020/29", "xpa/2020/28", "xpa/Geo6/14-15/48"], facets=_FACETS))
    assert page.entries == ["xpa/2020/29", "xpa/2020/28", "xpa/Geo6/14-15/48"]
    assert page.items_per_page == 20
    assert page.type_totals == {"xpa": 30}  # the ?flag partition is not a type
    assert page.year_totals == {2020: 29, 1951: 1}


def test_feed_url_pages_with_a_query() -> None:
    assert feed_url("xpa") == "https://www.legislation.gov.uk/xpa/data.feed"
    assert feed_url("xpa", 2020) == "https://www.legislation.gov.uk/xpa/2020/data.feed"
    assert feed_url("xpa", 2020, 3) == "https://www.legislation.gov.uk/xpa/2020/data.feed?page=3"


def test_entry_keeps_a_regnal_path_whole() -> None:
    assert entry_from_path("xpa", 2020, "xpa/2020/29") == {
        "token": "xpa",
        "year": 2020,
        "number": "29",
        "path": "xpa/2020/29",
    }
    regnal = entry_from_path("xpa", 1951, "xpa/Geo6/14-15/48")
    assert regnal["number"] == "48" and regnal["regnal"] == "Geo6/14-15"
    assert regnal["path"] == "xpa/Geo6/14-15/48"


def _client(pages: dict[str, bytes]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pages.get(str(request.url), _page([])))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_harvest_pages_to_the_empty_page_and_reconciles() -> None:
    # 29 items: a full page, a nine-item page, then an empty page.
    first = [f"xpa/2020/{n}" for n in range(29, 9, -1)]
    second = [f"xpa/2020/{n}" for n in range(9, 0, -1)]
    client = _client(
        {
            feed_url("xpa", 2020): _page(first),
            feed_url("xpa", 2020, 2): _page(second),
        }
    )
    harvest = await harvest_year(client, "xpa", 2020, expected=29)
    assert len(harvest.paths) == 29 and not harvest.short


async def test_harvest_refuses_a_short_year() -> None:
    client = _client({feed_url("xpa", 2020): _page(["xpa/2020/1", "xpa/2020/2"])})
    with pytest.raises(ShortYearError, match="harvested 2 of 29"):
        await harvest_year(client, "xpa", 2020, expected=29)


async def test_build_index_walks_types_and_records_short_years() -> None:
    client = _client(
        {
            feed_url("xpa"): _page([], facets=_FACETS),
            feed_url("xpa", 2020): _page([f"xpa/2020/{n}" for n in range(1, 21)]),
            feed_url("xpa", 2020, 2): _page([f"xpa/2020/{n}" for n in range(21, 30)]),
            feed_url("xpa", 1951): _page([]),  # short: facet says 1, feed gives 0
        }
    )
    index = await build_index(client, ["xpa"])
    assert index["stats"]["items"] == 29
    assert index["stats"]["type_totals"] == {"xpa": 30}
    assert [s["year"] for s in index["stats"]["short_years"]] == [1951]
    assert index["entries"][0] == {
        "token": "xpa",
        "year": 2020,
        "number": "1",
        "path": "xpa/2020/1",
    }


async def test_a_page_that_repeats_ends_the_harvest() -> None:
    """A feed that serves the same page again must not be walked forever."""
    same = _page([f"xpa/2020/{n}" for n in range(1, 21)])
    client = _client({feed_url("xpa", 2020): same, feed_url("xpa", 2020, 2): same})
    harvest = await harvest_year(client, "xpa", 2020, expected=20)
    assert len(harvest.paths) == 20


async def test_an_http_error_skips_the_year_and_the_walk_goes_on() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == feed_url("xpa"):
            return httpx.Response(200, content=_page([], facets=_FACETS))
        if url == feed_url("xpa", 1951):
            return httpx.Response(503)
        if url == feed_url("xpa", 2020):
            return httpx.Response(200, content=_page([f"xpa/2020/{n}" for n in range(1, 21)]))
        if url == feed_url("xpa", 2020, 2):
            return httpx.Response(200, content=_page([f"xpa/2020/{n}" for n in range(21, 30)]))
        return httpx.Response(200, content=_page([]))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    index = await build_index(client, ["xpa"])
    assert index["stats"]["items"] == 29
    assert [s["year"] for s in index["stats"]["short_years"]] == [1951]
    assert "503" in index["stats"]["short_years"][0]["error"]


async def test_type_totals_follow_the_year_window() -> None:
    client = _client(
        {
            feed_url("xpa"): _page([], facets=_FACETS),
            feed_url("xpa", 2020): _page([f"xpa/2020/{n}" for n in range(1, 21)]),
            feed_url("xpa", 2020, 2): _page([f"xpa/2020/{n}" for n in range(21, 30)]),
        }
    )
    index = await build_index(client, ["xpa"], years=(2000, None))
    assert index["stats"]["type_totals"] == {"xpa": 29}
    assert index["stats"]["short_years"] == []


def test_the_index_path_follows_the_datadump_convention(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent or empty, the variable means the mounted datadump directory."""
    from pathlib import Path

    from codify.acquisition.index import index_path

    monkeypatch.delenv("ACQUISITION_INDEX_DIR", raising=False)
    assert index_path("xq") == Path("data/datadumps/xq/index.json")
    monkeypatch.setenv("ACQUISITION_INDEX_DIR", "")
    assert index_path("xq") == Path("data/datadumps/xq/index.json")
    monkeypatch.setenv("ACQUISITION_INDEX_DIR", "/srv/indexes")
    assert index_path("xq") == Path("/srv/indexes/xq/index.json")


_TWO_YEARS = (
    "<leg:facetTypes>"
    f'<leg:facetType type="Primary" href="{_SITE}/xpa/data.feed" value="4"/>'
    "</leg:facetTypes><leg:facetYears>"
    '<leg:facetYear year="2019" href="…" total="2"/>'
    '<leg:facetYear year="2020" href="…" total="2"/>'
    "</leg:facetYears>"
)


def _two_year_pages() -> dict[str, bytes]:
    return {
        feed_url("xpa"): _page([], facets=_TWO_YEARS),
        feed_url("xpa", 2019): _page(["xpa/2019/1", "xpa/2019/2"]),
        feed_url("xpa", 2020): _page(["xpa/2020/1", "xpa/2020/2"]),
    }


async def test_a_killed_build_resumes_from_its_checkpoint(tmp_path: Path) -> None:
    """Killed after the first year, the file holds that year; the rerun walks
    only the rest and ends equal to an uninterrupted build."""
    out = tmp_path / "index.json"
    pages = _two_year_pages()
    hits: list[str] = []

    def dying(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if str(request.url) == feed_url("xpa", 2020):
            raise RuntimeError("killed")
        return httpx.Response(200, content=pages[str(request.url)])

    with pytest.raises(RuntimeError):
        await build_index(
            httpx.AsyncClient(transport=httpx.MockTransport(dying)), ["xpa"], checkpoint=out
        )
    saved = json.loads(out.read_text())
    assert saved["complete"] is False
    assert saved["stats"]["done"] == ["xpa/2019"]
    assert [e["path"] for e in saved["entries"]] == ["xpa/2019/1", "xpa/2019/2"]

    hits.clear()

    def serving(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, content=pages.get(str(request.url), _page([])))

    resumed = await build_index(
        httpx.AsyncClient(transport=httpx.MockTransport(serving)),
        ["xpa"],
        checkpoint=out,
        resume=True,
    )
    assert feed_url("xpa", 2019) not in hits
    assert feed_url("xpa", 2020) in hits
    whole = await build_index(_client(pages), ["xpa"])
    assert resumed["entries"] == whole["entries"]
    assert resumed["stats"]["done"] == ["xpa/2019", "xpa/2020"]
    assert resumed["complete"] is True
    assert json.loads(out.read_text())["stats"]["items"] == 4


async def test_a_short_year_is_walked_again_on_resume(tmp_path: Path) -> None:
    out = tmp_path / "index.json"
    pages = _two_year_pages()
    first = dict(pages, **{feed_url("xpa", 2019): _page([])})  # facet says 2, feed gives 0
    index = await build_index(_client(first), ["xpa"], checkpoint=out)
    assert [s["year"] for s in index["stats"]["short_years"]] == [2019]
    assert index["stats"]["done"] == ["xpa/2020"]

    hits: list[str] = []

    def serving(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, content=pages.get(str(request.url), _page([])))

    resumed = await build_index(
        httpx.AsyncClient(transport=httpx.MockTransport(serving)),
        ["xpa"],
        checkpoint=out,
        resume=True,
    )
    assert feed_url("xpa", 2019) in hits and feed_url("xpa", 2020) not in hits
    assert resumed["stats"]["short_years"] == []
    assert sorted(e["path"] for e in resumed["entries"]) == [
        "xpa/2019/1",
        "xpa/2019/2",
        "xpa/2020/1",
        "xpa/2020/2",
    ]


async def test_without_resume_an_existing_file_is_walked_over(tmp_path: Path) -> None:
    out = tmp_path / "index.json"
    pages = _two_year_pages()
    await build_index(_client(pages), ["xpa"], checkpoint=out)
    hits: list[str] = []

    def serving(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, content=pages.get(str(request.url), _page([])))

    again = await build_index(
        httpx.AsyncClient(transport=httpx.MockTransport(serving)), ["xpa"], checkpoint=out
    )
    assert feed_url("xpa", 2019) in hits and feed_url("xpa", 2020) in hits
    assert again["stats"]["items"] == 4


async def test_resume_refuses_a_checkpoint_built_for_other_types_or_years(
    tmp_path: Path,
) -> None:
    out = tmp_path / "index.json"
    pages = _two_year_pages()
    await build_index(_client(pages), ["xpa"], checkpoint=out)
    with pytest.raises(CheckpointMismatch, match="types"):
        await build_index(_client(pages), ["xpb"], checkpoint=out, resume=True)
    with pytest.raises(CheckpointMismatch, match="years"):
        await build_index(_client(pages), ["xpa"], years=(2020, None), checkpoint=out, resume=True)


async def test_entries_are_written_in_walk_order_after_a_short_year_returns(
    tmp_path: Path,
) -> None:
    out = tmp_path / "index.json"
    pages = _two_year_pages()
    first = dict(pages, **{feed_url("xpa", 2019): _page([])})
    await build_index(_client(first), ["xpa"], checkpoint=out)
    await build_index(_client(pages), ["xpa"], checkpoint=out, resume=True)
    saved = json.loads(out.read_text())
    assert [e["path"] for e in saved["entries"]] == [
        "xpa/2019/1",
        "xpa/2019/2",
        "xpa/2020/1",
        "xpa/2020/2",
    ]
    assert saved["stats"]["done"] == ["xpa/2019", "xpa/2020"]


async def test_a_completed_year_whose_count_moved_is_walked_again(tmp_path: Path) -> None:
    """The publisher added an item to 2019 between attempts: the resume re-walks
    that year and leaves 2020 alone."""
    out = tmp_path / "index.json"
    await build_index(_client(_two_year_pages()), ["xpa"], checkpoint=out)
    grown = _TWO_YEARS.replace('year="2019" href="…" total="2"', 'year="2019" href="…" total="3"')
    pages = {
        feed_url("xpa"): _page([], facets=grown),
        feed_url("xpa", 2019): _page(["xpa/2019/1", "xpa/2019/2", "xpa/2019/3"]),
        feed_url("xpa", 2020): _page(["xpa/2020/1", "xpa/2020/2"]),
    }
    hits: list[str] = []

    def serving(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, content=pages.get(str(request.url), _page([])))

    resumed = await build_index(
        httpx.AsyncClient(transport=httpx.MockTransport(serving)),
        ["xpa"],
        checkpoint=out,
        resume=True,
    )
    assert feed_url("xpa", 2019) in hits and feed_url("xpa", 2020) not in hits
    assert [e["path"] for e in resumed["entries"] if e["year"] == 2019] == [
        "xpa/2019/1",
        "xpa/2019/2",
        "xpa/2019/3",
    ]
    assert resumed["stats"]["items"] == 5 and resumed["stats"]["type_totals"] == {"xpa": 5}


async def test_a_completed_year_the_publisher_no_longer_lists_is_dropped(tmp_path: Path) -> None:
    out = tmp_path / "index.json"
    await build_index(_client(_two_year_pages()), ["xpa"], checkpoint=out)
    only_2020 = _TWO_YEARS.replace('<leg:facetYear year="2019" href="…" total="2"/>', "")
    pages = {
        feed_url("xpa"): _page([], facets=only_2020),
        feed_url("xpa", 2020): _page(["xpa/2020/1", "xpa/2020/2"]),
    }
    hits: list[str] = []

    def serving(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, content=pages.get(str(request.url), _page([])))

    resumed = await build_index(
        httpx.AsyncClient(transport=httpx.MockTransport(serving)),
        ["xpa"],
        checkpoint=out,
        resume=True,
    )
    assert hits == [feed_url("xpa")]
    assert [e["path"] for e in resumed["entries"]] == ["xpa/2020/1", "xpa/2020/2"]
    assert resumed["stats"]["done"] == ["xpa/2020"]
    assert resumed["stats"]["items"] == 2 and resumed["stats"]["type_totals"] == {"xpa": 2}
