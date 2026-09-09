"""The UK publisher's effects feed: what changed, where, by whom and when.

Two requests per act. The export says what changed; its own in-force column is
blank on every row the publisher serves, so the dates come from a second pass
over the same feed in Atom form. An adapter that skips the second returns
effects nobody can place on a timeline.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Literal

import httpx
from lxml import etree

from codify.acquisition.effects.types import FeedEffect, FeedResult, FeedTruncated
from codify.frbr import UK_TYPE_TOKENS, parse_source_ref

BASE_HOST = "https://www.legislation.gov.uk"
_CHANGES = f"{BASE_HOST}/changes"
# The export caps at whatever is asked for and says nothing when it truncates,
# so a count equal to the cap is a truncation rather than a total.
RESULTS_CAP = 5000
_FEED_PAGE = 200
_NS = {"ukm": "http://www.legislation.gov.uk/namespaces/metadata"}
# Same hardening as every other feed parser here: no entities, no network.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)

AknCategory = Literal["textual", "meaning", "scope", "force", "efficacy"]

# Effect verbs to (AKN action, category), keyed on the leading verb phrase with
# provenance tails stripped first. Conservative on purpose: a verb absent here
# resolves to (None, None) and is flagged unmapped rather than guessed at.
_CROSSWALK: dict[str, tuple[str, AknCategory]] = {
    "inserted": ("insertion", "textual"),
    "added": ("insertion", "textual"),
    "omitted": ("repeal", "textual"),
    "repealed": ("repeal", "textual"),
    "revoked": ("repeal", "textual"),
    "substituted": ("substitution", "textual"),
    "renumbered": ("renumbering", "textual"),
    "modified": ("variation", "meaning"),
    "extended": ("extensionOfScope", "scope"),
    "applied": ("extensionOfScope", "scope"),
    "excluded": ("exceptionOfScope", "scope"),
    "restricted": ("exceptionOfScope", "scope"),
    "coming into force": ("entryIntoForce", "force"),
    "in force": ("entryIntoForce", "force"),
    "commencement order": ("entryIntoForce", "force"),
}

# Leading modifiers that don't change the effect verb ("words omitted" == omit,
# "entry inserted" == insert, "heading substituted" == substitute).
_QUANTIFIER = re.compile(
    r"^(?:word|words|sum|para|paras|comma|and comma|entry|entries|heading)\s+", re.I
)
_PARENS = re.compile(r"\([^)]*\)")
_PROVENANCE = re.compile(r"\s+(?:by|in earlier|for|of|conferred)\b.*$", re.I)
_META_MARK = re.compile(r"earlier affecting|earlier amending provision", re.I)


def crosswalk(type_of_effect: str) -> tuple[str | None, AknCategory | None]:
    """Map a legislation.gov.uk effect type to (akn_action, akn_category).

    Returns (None, None) when no confident mapping exists; the caller keeps the
    raw string regardless.
    """
    s = type_of_effect.strip().lower()
    s = _PARENS.sub("", s)  # drop "(with modifications)", "(S.N.I.)"
    s = _PROVENANCE.sub("", s)  # drop "... by S.I. ...", "... for ss. 49, 50"
    s = s.replace(" in part", "")
    while _QUANTIFIER.match(s):
        s = _QUANTIFIER.sub("", s, count=1)
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    if s in _CROSSWALK:
        return _CROSSWALK[s]
    if s == "substututed":  # tolerate a recurring editorial typo
        return _CROSSWALK["substituted"]
    return (None, None)


# CSV legislation labels → legislation.gov.uk `/id/` document-type. "2018 c. 012"
# → ukpga; the statutory-instrument families carry a jurisdiction prefix:
# SI→uksi, WSI→wsi (Wales), SSI→ssi (Scotland), NISR→nisr (Northern Ireland).
_CHAPTER = re.compile(r"^(\d{4})\s+c\.\s*0*(\d+)(\s*\(N\.I\.\))?", re.I)
# An S.I. number with "(N.I.)" is a Northern Ireland Order in Council: nisi.
_SI_FAMILY = re.compile(r"^(\d{4})\s+(SI|WSI|SSI|NISR)\s*0*(\d+)(\s*\(N\.I\.\))?", re.I)
_SI_TYPE = {"si": "uksi", "wsi": "wsi", "ssi": "ssi", "nisr": "nisr"}


def _ni_primary_token(year: int) -> str:
    """A Northern Ireland chapter's series by era: the Parliament to 1972, the
    1974 Assembly's Measures, the Assembly from 1999."""
    if year <= 1972:
        return "apni"
    if year < 1999:
        return "mnia"
    return "nia"


def parse_leg_ref(raw: str) -> str | None:
    """Best-effort legislation.gov.uk `/id/` Work URI from a CSV legislation
    label. Returns None for classes we don't map (e.g. devolved Assembly Acts,
    whose CSV labels arrive as 'Unknown class: …')."""
    raw = raw.strip()
    m = _CHAPTER.match(raw)
    if m:
        token = _ni_primary_token(int(m.group(1))) if m.group(3) else "ukpga"
        return f"http://www.legislation.gov.uk/id/{token}/{m.group(1)}/{int(m.group(2))}"
    m = _SI_FAMILY.match(raw)
    if m:
        doctype = "nisi" if m.group(4) else _SI_TYPE[m.group(2).lower()]
        return f"http://www.legislation.gov.uk/id/{doctype}/{m.group(1)}/{int(m.group(3))}"
    return None


def _truthy(cell: str) -> bool:
    return cell.strip().lower() in {"y", "yes", "true", "1"}


def effects_csv_url(path: str, *, results: int = RESULTS_CAP) -> str:
    return f"{_CHANGES}/affected/{path}/data.csv?results-count={results}&sort=affected-provision"


def inforce_feed_url(path: str, page: int) -> str:
    return f"{_CHANGES}/affected/{path}/data.feed?results-count={_FEED_PAGE}&page={page}"


def _frbr_work(leg_id_url: str | None) -> str | None:
    """A legislation.gov.uk `/id/` work URI to ours, else None."""
    parsed = parse_source_ref(leg_id_url) if leg_id_url else None
    return parsed[0] if parsed else None


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def parse_effects_csv(text: str, dates: dict[str, str]) -> FeedResult:
    """Rows to effects, dated from the sidecar. Nothing is dropped for being
    unmapped: the raw type survives on the record and the count says so."""
    result = FeedResult()
    skipped: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(text)):
        raw_type = row["Type of Effect"]
        action, category = crosswalk(raw_type)
        if action is None:
            skipped["unmapped_type"] = skipped.get("unmapped_type", 0) + 1
        pid = row["ID"].strip()
        result.effects.append(
            FeedEffect(
                publisher_id=pid,
                # Our FRBR, not the publisher's URL, so an effect's authority
                # joins `laws.frbr_work_uri`.
                affected_work=_frbr_work(parse_leg_ref(row["Affected Legislation"])),
                affected_provision=row["Affected Provision(s)"].strip(),
                affecting_work=_frbr_work(parse_leg_ref(row["Affecting Legislation"])),
                affecting_provision=row["Affecting Provision"].strip(),
                raw_type=raw_type.strip(),
                akn_action=action,
                akn_category=category,
                # The sidecar first: the export's own column is blank on every
                # row it serves, so the column is a fallback, not the source.
                in_force_date=_parse_date(dates.get(pid) or row.get("IF Date", "")),
                applied=_truthy(row.get("Amendment applied to Database", "")),
                is_meta=bool(_META_MARK.search(raw_type)),
            )
        )
        if result.effects[-1].in_force_date is None:
            skipped["undated"] = skipped.get("undated", 0) + 1
    result.skipped = skipped
    return result


def parse_inforce_feed(xml: bytes) -> tuple[dict[str, str], bool]:
    """`{publisher id: earliest in-force date}` from one page, and whether the
    page held any effects at all, which is how the paging ends."""
    root = etree.fromstring(xml, parser=_PARSER)
    found = root.findall(".//ukm:Effect", _NS)
    out: dict[str, str] = {}
    for effect in found:
        eid = (effect.get("EffectId") or "").removeprefix("key-")
        for in_force in effect.findall("ukm:InForceDates/ukm:InForce", _NS):
            when = in_force.get("Date")
            if eid and when and (eid not in out or when < out[eid]):
                out[eid] = when
    return out, bool(found)


class UkEffectsAdapter:
    """legislation.gov.uk's Changes to Legislation, in two passes."""

    jurisdiction_code = "gb"

    @staticmethod
    def publisher_path_for(work_uri: str) -> str | None:
        """`/akn/gb/act/ukpga/2018/12` to `ukpga/2018/12`, the feed's own key.

        The publisher token has to be one this publisher serves. A URI without
        one, `/akn/gb/act/2018/12`, would otherwise yield `act/2018/12` and be
        fetched as though it were a type token, which 404s at best and reads a
        different act's feed at worst.
        """
        parts = [p for p in work_uri.split("/") if p]
        if len(parts) < 6 or parts[0] != "akn":
            return None
        token, year, number = parts[-3:]
        if token.lower() not in UK_TYPE_TOKENS or not year.isdigit() or not number:
            return None
        return f"{token}/{year}/{number}"

    @staticmethod
    def held_target_eid(eid: str, held: set[str]) -> str | None:
        return eid if eid in held else None

    @staticmethod
    def target_eid(affected_provision: str) -> str | None:
        return provision_eid(affected_provision)

    async def fetch_many(self, publisher_paths: list[str]) -> dict[str, FeedResult]:
        """One act at a time: this publisher serves per act, so the batch is a
        loop and the politeness bucket paces it."""
        return {path: await self.fetch(path) for path in publisher_paths}

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _get(self, url: str) -> bytes:
        response = await self._client.get(url)
        response.raise_for_status()
        return response.content

    async def fetch(self, publisher_path: str) -> FeedResult:
        csv_text = (await self._get(effects_csv_url(publisher_path))).decode("utf-8")
        # Records, not newlines: a quoted field may hold one, and counting lines
        # would call a complete export truncated.
        rows = sum(1 for _ in csv.reader(io.StringIO(csv_text))) - 1
        if rows >= RESULTS_CAP:
            raise FeedTruncated(
                f"{publisher_path}: the export returned {rows} rows at the {RESULTS_CAP} cap, "
                "so it is truncated and this act cannot be loaded from it"
            )
        dates: dict[str, str] = {}
        page = 1
        while True:
            found, more = parse_inforce_feed(
                await self._get(inforce_feed_url(publisher_path, page))
            )
            if not more:
                break
            dates.update(found)
            page += 1
        return parse_effects_csv(csv_text, dates)


# Provision refs as the feed writes them, to eIds ("s. 5(2)(a)" -> "section-5-2-a").
# Unit-level annotations collapse to the unit; ranges and lists do not parse and
# are counted rather than guessed at.
_SUBS = r"(?P<subs>(?:\(\w+\))*)"
_TAIL = r"(?:\s+(?:heading|cross-heading|table))?\.?\s*$"
_SEC = re.compile(rf"^s\.?\s*(?P<num>\d+[A-Z]*){_SUBS}{_TAIL}", re.I)
_SCH_PARA = re.compile(
    rf"^sch\.?\s*(?P<sch>\d+[A-Z]*)\s+para\.?\s*(?P<num>\d+[A-Z]*){_SUBS}{_TAIL}", re.I
)
_SCH = re.compile(rf"^sch\.?\s*(?P<sch>\d+[A-Z]*){_TAIL}", re.I)
# An act's basic unit is the section; a statutory instrument's is the regulation,
# the article or the rule, and its eIds are written from that word. Without all
# three, no provision effect on an instrument resolves to anything.
_UNITS = {"reg": "regulation", "art": "article", "rule": "rule", "r": "rule"}
_UNIT = re.compile(
    rf"^(?P<unit>reg|art|rule|r)(?:ulation|icle)?\.?\s*(?P<num>\d+[A-Z]*){_SUBS}{_TAIL}", re.I
)
_PAREN = re.compile(r"\((\w+)\)")


def _case(token: str) -> str:
    """The publisher's own eIds spell an inserted unit's letter in upper case
    (`section-1A`, `section-1-3A`) and a sub-paragraph's in lower (`-3-a`).
    Folding either way puts the target beside the eId rather than on it."""
    return token[:1] + token[1:].upper() if token[:1].isdigit() else token.lower()


def _chain(num: str, subs: str) -> str:
    return "-".join([_case(num), *(_case(m.group(1)) for m in _PAREN.finditer(subs))])


def provision_eid(raw: str) -> str | None:
    """One provision reference to its eId, or None when it names more than one."""
    s = raw.strip()
    if m := _SCH_PARA.match(s):
        return (
            f"schedule-{_case(m.group('sch'))}-paragraph-{_chain(m.group('num'), m.group('subs'))}"
        )
    if m := _SCH.match(s):
        return f"schedule-{_case(m.group('sch'))}"
    if m := _SEC.match(s):
        return f"section-{_chain(m.group('num'), m.group('subs'))}"
    if m := _UNIT.match(s):
        unit = _UNITS[m.group("unit").lower()]
        return f"{unit}-{_chain(m.group('num'), m.group('subs'))}"
    return None
