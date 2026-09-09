"""Enumerate legislation.gov.uk through its Atom feeds. The feed carries no
total and `leg:morePages` never reaches zero, so each year is paged to the
empty page and reconciled against its `leg:facetYears` count."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
from lxml import etree

logger = structlog.get_logger()

# The feed is untrusted input: no entity expansion, no network, no recovery.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)

BASE_URL = "https://www.legislation.gov.uk"
_ATOM = "{http://www.w3.org/2005/Atom}"
_LEG = "{http://www.legislation.gov.uk/namespaces/legislation}"
_OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
# /id/{type}/{path...}: the path is {year}/{number} or a regnal form such as
# Geo6/14-15/48, so everything after the type is kept whole.
_ENTRY_ID_RE = re.compile(r"/id/([a-z]+)/(.+?)/?$")


@dataclass(frozen=True)
class FeedPage:
    entries: list[str]  # publisher paths, e.g. "ukpga/1998/42" or "ukpga/Geo6/14-15/48"
    items_per_page: int
    type_totals: dict[str, int]  # facet type token → total
    year_totals: dict[int, int]  # facet year → total


@dataclass
class YearHarvest:
    token: str
    year: int
    expected: int
    paths: list[str] = field(default_factory=list)

    @property
    def short(self) -> bool:
        return len(self.paths) < self.expected


class ShortYearError(RuntimeError):
    """A year's harvest fell short of the facet total; the index must not record it."""


def parse_feed(body: bytes) -> FeedPage:
    """The entries and facet totals of one Atom feed page."""
    root = etree.fromstring(body, _PARSER)
    entries: list[str] = []
    for entry in root.findall(f"{_ATOM}entry"):
        entry_id = entry.findtext(f"{_ATOM}id") or ""
        match = _ENTRY_ID_RE.search(entry_id)
        if match:
            entries.append(f"{match.group(1)}/{match.group(2)}")
    type_totals: dict[str, int] = {}
    for facet in root.iter(f"{_LEG}facetType"):
        href, value = facet.get("href") or "", facet.get("value") or ""
        # The href names the token; the type attribute is the long form.
        token = href.rstrip("/").rsplit("/", 2)[-2] if href.endswith("data.feed") else ""
        if token and value.isdigit() and "?" not in href:
            type_totals[token] = int(value)
    year_totals: dict[int, int] = {}
    for facet in root.iter(f"{_LEG}facetYear"):
        year, total = facet.get("year") or "", facet.get("total") or ""
        if year.isdigit() and total.isdigit():
            year_totals[int(year)] = int(total)
    per_page = (root.findtext(f"{_OPENSEARCH}itemsPerPage") or "20").strip()
    return FeedPage(entries, int(per_page) if per_page.isdigit() else 20, type_totals, year_totals)


def feed_url(token: str, year: int | None = None, page: int = 1) -> str:
    path = f"{BASE_URL}/{token}" + (f"/{year}" if year is not None else "") + "/data.feed"
    return path if page == 1 else f"{path}?page={page}"


async def year_totals(client: httpx.AsyncClient, token: str) -> dict[int, int]:
    """Per-year item totals for one type, from its top-level feed's facets."""
    response = await client.get(feed_url(token))
    response.raise_for_status()
    return parse_feed(response.content).year_totals


async def harvest_year(
    client: httpx.AsyncClient, token: str, year: int, expected: int
) -> YearHarvest:
    """Every item path of one type and year, paged to the empty page and
    reconciled against the facet total."""
    harvest = YearHarvest(token=token, year=year, expected=expected)
    seen: set[str] = set()
    page = 1
    while True:
        response = await client.get(feed_url(token, year, page))
        response.raise_for_status()
        parsed = parse_feed(response.content)
        if not parsed.entries:
            break
        added = 0
        for path in parsed.entries:
            if path not in seen:
                seen.add(path)
                harvest.paths.append(path)
                added += 1
        # A page that repeats what came before is the end too.
        if added == 0 or len(parsed.entries) < parsed.items_per_page:
            break
        page += 1
    if harvest.short:
        raise ShortYearError(
            f"{token}/{year}: harvested {len(harvest.paths)} of {expected} items in {page} page(s)"
        )
    return harvest


def entry_from_path(token: str, year: int, path: str) -> dict[str, Any]:
    """An index entry. `number` is the last path segment; a regnal path keeps
    its full publisher form, since only the fetched document names its
    calendar-year work."""
    tail = path[len(token) + 1 :]
    number = tail.rsplit("/", 1)[-1]
    entry: dict[str, Any] = {"token": token, "year": year, "number": number, "path": path}
    if tail != f"{year}/{number}":
        entry["regnal"] = tail.rsplit("/", 1)[0]
    return entry


Window = tuple[int | None, int | None]


def _payload(
    tokens: list[str],
    years: Window,
    entries: list[dict[str, Any]],
    totals: dict[str, int],
    short: list[dict[str, Any]],
    done: set[str],
    *,
    complete: bool,
) -> dict[str, Any]:
    rank = {token: i for i, token in enumerate(tokens)}
    # Walk order, whatever order the years were completed in.
    ordered = sorted(entries, key=lambda e: (rank.get(e["token"], len(rank)), e["year"]))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": BASE_URL,
        "tokens": tokens,
        "years": list(years),
        # False on every checkpoint; a reader must not sample a build still running.
        "complete": complete,
        "entries": ordered,
        "stats": {
            "items": len(ordered),
            "type_totals": totals,
            "regnal": sum(1 for e in ordered if "regnal" in e),
            "short_years": short,
            "done": sorted(done, key=lambda d: (rank.get(d.split("/")[0], len(rank)), d)),
        },
    }


def write_index(path: Path, payload: dict[str, Any]) -> None:
    """Whole file or nothing, durable: temp file, fsync, rename, fsync the directory."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(payload, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class CheckpointMismatch(ValueError):
    """The file at the checkpoint path was built for other types or years."""


def _resume_from(
    path: Path, tokens: list[str], years: Window
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Entries and completed token-years (with their item counts) of the build at
    `path`, or nothing if absent."""
    if not path.exists():
        return [], {}
    prior = json.loads(path.read_text())
    if prior.get("tokens") != tokens or tuple(prior.get("years", (None, None))) != years:
        raise CheckpointMismatch(
            f"{path} was built for types {prior.get('tokens')} and years "
            f"{prior.get('years')}; this run asks for {tokens} and {list(years)}. "
            "Use the same --types and year window, or a different --out."
        )
    done = set(prior.get("stats", {}).get("done", []))
    entries = [e for e in prior.get("entries", []) if f"{e['token']}/{e['year']}" in done]
    counts: dict[str, int] = dict.fromkeys(done, 0)
    for e in entries:
        counts[f"{e['token']}/{e['year']}"] += 1
    return entries, counts


async def build_index(
    client: httpx.AsyncClient,
    tokens: list[str],
    *,
    years: Window = (None, None),
    on_progress: Callable[[str, int, int, int], Awaitable[None] | None] | None = None,
    checkpoint: Path | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Walk every (token, year) and return the index payload; a short year is
    recorded under `short_years`, its items left out. With `checkpoint` the payload
    is written there after every completed token-year; with `resume` a file already
    there (same types and window) is continued: short years and any completed year
    whose facet count has moved are walked again."""
    entries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    if resume and checkpoint is not None:
        entries, counts = _resume_from(checkpoint, tokens, years)
        if counts:
            logger.info(
                "legislation_gov_uk_index_resumed", token_years=len(counts), items=len(entries)
            )
    done = set(counts)
    totals: dict[str, int] = {}
    short: list[dict[str, Any]] = []
    year_from, year_to = years
    for token in tokens:
        per_year = await year_totals(client, token)
        in_window = {
            year: n
            for year, n in per_year.items()
            if (year_from is None or year >= year_from) and (year_to is None or year <= year_to)
        }
        totals[token] = sum(in_window.values())
        # A saved year the publisher no longer lists is gone from the index too.
        gone = {
            k for k in done if k.split("/")[0] == token and int(k.split("/")[1]) not in in_window
        }
        for key in sorted(gone):
            logger.info("legislation_gov_uk_year_removed", key=key, had=counts.get(key))
            done.discard(key)
        if gone:
            entries = [e for e in entries if f"{e['token']}/{e['year']}" not in gone]
        for year in sorted(in_window):
            key = f"{token}/{year}"
            expected = in_window[year]
            if key in done:
                if counts.get(key) == expected:
                    continue
                # The publisher's count moved since this year completed: walk it again.
                logger.info(
                    "legislation_gov_uk_year_changed", key=key, had=counts.get(key), now=expected
                )
                entries = [e for e in entries if f"{e['token']}/{e['year']}" != key]
                done.discard(key)
            try:
                harvest = await harvest_year(client, token, year, expected)
            except (ShortYearError, httpx.HTTPError) as exc:
                # One bad year is recorded and skipped; the walk goes on.
                logger.warning("legislation_gov_uk_year_skipped", detail=str(exc))
                short.append(
                    {"token": token, "year": year, "expected": expected, "error": str(exc)}
                )
                continue
            entries.extend(entry_from_path(token, year, path) for path in harvest.paths)
            done.add(f"{token}/{year}")
            if checkpoint is not None:
                write_index(
                    checkpoint,
                    _payload(tokens, years, entries, totals, short, done, complete=False),
                )
            result = on_progress(token, year, len(harvest.paths), expected) if on_progress else None
            if result is not None:
                await result
    return _payload(tokens, years, entries, totals, short, done, complete=True)


# Regnal paths. A pre-1963 item is indexed under the publisher's regnal path
# (`ukpga/Vict/56-57/71`), while a citation names it by calendar year
# (`ukpga/1893/71`). The accession dates below turn a regnal session into the
# calendar years it covers, so the cited year finds its index entry.
_ACCESSIONS: dict[str, date] = {
    "Hen7": date(1485, 8, 22),
    "Hen8": date(1509, 4, 22),
    "Edw6": date(1547, 1, 28),
    "Mar": date(1553, 7, 6),
    "Eliz1": date(1558, 11, 17),
    "Ja1": date(1603, 3, 24),
    "Cha1": date(1625, 3, 27),
    "Chas1": date(1625, 3, 27),
    # Charles II's regnal years run from his father's death, not the Restoration.
    "Cha2": date(1649, 1, 30),
    "Ja2": date(1685, 2, 6),
    "WillandMar": date(1689, 2, 13),
    "Will3": date(1689, 2, 13),
    "Ann": date(1702, 3, 8),
    "Anne": date(1702, 3, 8),
    "Geo1": date(1714, 8, 1),
    "Geo2": date(1727, 6, 11),
    "Geo3": date(1760, 10, 25),
    "Geo4": date(1820, 1, 29),
    "Will4": date(1830, 6, 26),
    "Vict": date(1837, 6, 20),
    "Edw7": date(1901, 1, 22),
    "Geo5": date(1910, 5, 6),
    "Edw8": date(1936, 1, 20),
    "Geo6": date(1936, 12, 11),
    "Eliz2": date(1952, 2, 6),
}
# A session or statute suffix (`Sess2`, `St1`, `Stat5`, `un1`) names a sitting, not a reign.
_SITTING_SUFFIX = re.compile(r"(?:Sess|Stat|St|st|un)\d+$")
# `1Geo5` inside a compound carries its own year.
_REGNAL_PART = re.compile(r"^(\d+)?([A-Za-z]+\d?)$")


def _regnal_year_span(monarch: str, regnal_year: int) -> set[int]:
    accession = _ACCESSIONS.get(monarch)
    if accession is None:
        return set()
    start = accession.replace(year=accession.year + regnal_year - 1)
    end = accession.replace(year=accession.year + regnal_year)
    return set(range(start.year, end.year + 1))


def regnal_calendar_years(regnal: str) -> set[int]:
    """The calendar years a publisher regnal path segment covers, `Vict/62-63`
    or `Edw7and1Geo5/10`; empty for a monarch the table does not know."""
    reigns, _, years = regnal.partition("/")
    years = years.strip()
    reigns = _SITTING_SUFFIX.sub("", reigns)
    # A joint reign is one key (`WillandMar`); a compound session joins two reigns.
    parts = [reigns] if reigns in _ACCESSIONS else [p for p in re.split(r"and|&", reigns) if p]
    covered: set[int] = set()
    for index, part in enumerate(parts):
        m = _REGNAL_PART.match(part)
        if not m:
            return set()
        leading, monarch = m.group(1), m.group(2)
        if leading is not None:
            # `1Geo5` inside a compound: that monarch's first year.
            covered |= _regnal_year_span(monarch, int(leading))
            continue
        if not years:
            continue
        span = years.split("-")
        if index and len(parts) > 1:
            continue  # the later monarch carries its own leading number
        if not all(s.isdigit() for s in span):
            return set()
        first, last = int(span[0]), int(span[-1])
        for n in range(first, last + 1):
            covered |= _regnal_year_span(monarch, n)
    return covered


def paths_for_work(entries: list[dict[str, Any]], token: str, year: int, number: str) -> list[str]:
    """The index paths a calendar-year citation names: the exact year and number
    when the index has it, else every regnal entry of that token and number whose
    session covers the year. More than one is an ambiguity for the caller."""
    exact = [
        str(e["path"])
        for e in entries
        if e["token"] == token and int(e["year"]) == year and str(e["number"]) == number
    ]
    if exact:
        return exact
    return sorted(
        str(e["path"])
        for e in entries
        if e["token"] == token
        and str(e["number"]) == number
        and e.get("regnal")
        and year in regnal_calendar_years(str(e["regnal"]))
    )
