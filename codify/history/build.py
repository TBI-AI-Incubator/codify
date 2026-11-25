"""Derive a work's dated expressions, provision intervals and identity links from its texts.
Pure, synchronous and CPU-bound: an async caller runs `build_work` on a worker pool.
"""

from __future__ import annotations

import hashlib
import re
import struct
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

BUILDER_REV = "2"
LOW_OVERLAP = 0.3
MOVED_TEXT_CONFIDENCE = 0.3
_SIMILARITY_WINDOW = 600
DEFAULT_UNIT_PREFIXES = ("sec", "art")
_DUP = re.compile(r"_dup\d+")
_DASHES = re.compile("[\u2010-\u2015\u2212\u00ad]")
_DOUBLE_QUOTES = re.compile("[\u00ab\u00bb\u201e\u201c\u201d\u201f\u2033]")
_SINGLE_QUOTES = re.compile("[\u2018\u2019\u201a\u201b`\u00b4]")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True, kw_only=True)
class Unit:
    """One provision of a text. `kind` is label, boilerplate, fragment or text, derived if unset."""

    eid: str
    akn_type: str
    position: int
    text: str
    variant: str = ""
    kind: str | None = None
    opened_by: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceText:
    """A dated text of one work. No `source_ref` means a derived text; no `akn_xml` means a gap."""

    language: str
    valid_from: date | None
    valid_to: date | None = None
    published_on: date | None = None
    source_ref: str | None = None
    source_sha256: str | None = None
    akn_xml: str | None = None
    units: Sequence[Unit] = ()
    variant: str = ""
    derivation: str = "publisher_text"
    official: bool = True
    confidence: float | None = None
    derived_from: Mapping[str, Any] | None = None
    version_id: uuid.UUID | None = None
    gap_reason: str = "conversion_failed"
    converter_rev: str


@dataclass(frozen=True, slots=True)
class Expression:
    work_id: uuid.UUID
    jurisdiction_id: uuid.UUID
    language: str
    variant: str
    valid_from: date
    valid_to: date | None
    source_ref: str | None
    source_sha256: str | None
    akn_xml: str | None
    version_id: uuid.UUID | None
    gap_reason: str | None
    derivation: str
    confidence: float | None
    official: bool
    derived_from: Mapping[str, Any] | None
    converter_rev: str
    builder_rev: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class Interval:
    work_id: uuid.UUID
    jurisdiction_id: uuid.UUID
    language: str
    variant: str
    akn_eid: str
    valid_from: date
    valid_to: date | None
    akn_type: str
    position: int
    text: str
    text_sha256: bytes
    text_norm_sha256: bytes
    derivation: str
    confidence: float | None
    official: bool
    opened_by: str | None
    converter_rev: str
    builder_rev: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class Link:
    work_id: uuid.UUID
    language: str
    variant: str
    linked_on: date
    from_eid: str
    to_eid: str
    rule: str
    confidence: float | None
    builder_rev: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class Withdrawal:
    jurisdiction_id: uuid.UUID
    work_id: uuid.UUID
    row_kind: str
    language: str
    variant: str
    akn_eid: str | None
    from_eid: str | None
    valid_from: date
    builder_rev: str
    withdrawn_at: datetime


@dataclass(frozen=True, slots=True)
class WorkBuild:
    expressions: tuple[Expression, ...] = ()
    intervals: tuple[Interval, ...] = ()
    links: tuple[Link, ...] = ()
    withdrawals: tuple[Withdrawal, ...] = field(default=())


def normalize_text(text: str) -> str:
    """Typography-blind form: NFKC, one dash, one quote of each kind, no whitespace, case folded."""
    text = unicodedata.normalize("NFKC", text)
    text = _DASHES.sub("-", text)
    text = _DOUBLE_QUOTES.sub('"', text)
    text = _SINGLE_QUOTES.sub("'", text)
    return _SPACE.sub("", text).casefold()


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


def _kind(unit: Unit, norm: str) -> str:
    if unit.kind is not None:
        return unit.kind
    stripped = unit.text.strip()
    if len(norm) < 25 or stripped[:1] in "-\u2013\u2014" or stripped.endswith(":"):
        return "fragment"
    return "text"


def _number_path(unit: Unit, prefixes: Collection[str]) -> tuple[str, str] | None:
    """The eId from its deepest basic-unit segment down, converter duplicate suffixes removed."""
    segments = unit.eid.split("__")
    for i in range(len(segments) - 1, -1, -1):
        if segments[i].split("_", 1)[0] in prefixes:
            return _DUP.sub("", "__".join(segments[i:])), unit.akn_type
    return None


def _similarity(a: str, b: str) -> float:
    a, b = a[:_SIMILARITY_WINDOW], b[:_SIMILARITY_WINDOW]
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


@dataclass(slots=True)
class _Open:
    unit: Unit
    norm: str
    sha: bytes
    nsha: bytes
    kind: str
    start: date
    provenance: tuple[str, bool, float | None]
    converter_rev: str
    path: tuple[str, str] | None


@dataclass(slots=True)
class _Step:
    key_of: dict[str, int | None] = field(default_factory=dict)
    rule: dict[str, str] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)


def _link_units(open_: dict[int, _Open], cur: dict[str, _Open]) -> _Step:
    """A provision keeps its history while its eId holds, else joins the one departing provision
    with its number path and type. Labels never join another eId; identical text is a hint."""
    step = _Step()
    by_eid = {o.unit.eid: k for k, o in open_.items()}
    for eid, c in cur.items():
        k = by_eid.get(eid)
        if k is None:
            continue
        if open_[k].kind != "label" and c.kind != "label" or open_[k].sha == c.sha:
            step.key_of[eid] = k
    new = [eid for eid in cur if eid not in step.key_of]
    claimed = set(step.key_of.values())
    gone = [k for k in open_ if k not in claimed]
    departing: dict[tuple[str, str], list[int]] = defaultdict(list)
    arriving: dict[tuple[str, str], list[str]] = defaultdict(list)
    for k in gone:
        o = open_[k]
        if o.kind != "label" and o.path is not None:
            departing[o.path].append(k)
    for eid in new:
        c = cur[eid]
        if c.kind != "label" and c.path is not None:
            arriving[c.path].append(eid)
    for path, eids in arriving.items():
        keys = departing.get(path, [])
        if len(eids) == 1 and len(keys) == 1:
            step.key_of[eids[0]], step.rule[eids[0]] = keys[0], "same_number"
        elif eids and keys:
            by_old: dict[bytes, list[int]] = defaultdict(list)
            for k in keys:
                by_old[open_[k].nsha].append(k)
            by_new: dict[bytes, list[str]] = defaultdict(list)
            for eid in eids:
                by_new[cur[eid].nsha].append(eid)
            for h, news in by_new.items():
                if len(news) == 1 and len(by_old.get(h, [])) == 1:
                    step.key_of[news[0]], step.rule[news[0]] = by_old[h][0], "same_number_text"
    claimed = set(step.key_of.values())
    moved_from: dict[bytes, list[int]] = defaultdict(list)
    moved_to: dict[bytes, list[str]] = defaultdict(list)
    for k in gone:
        if k not in claimed and open_[k].kind == "text":
            moved_from[open_[k].nsha].append(k)
    for eid in new:
        if eid not in step.key_of and cur[eid].kind == "text":
            moved_to[cur[eid].nsha].append(eid)
    for h, eids in moved_to.items():
        if len(eids) == 1 and len(moved_from.get(h, [])) == 1:
            step.rule[eids[0]] = "moved_text"
            step.source[eids[0]] = open_[moved_from[h][0]].unit.eid
    for eid in new:
        step.key_of.setdefault(eid, None)
    return step


def _choose(texts: Sequence[SourceText]) -> list[SourceText]:
    """One text per start date: a text that ended wins, then the latest publication."""
    days: dict[date, list[SourceText]] = defaultdict(list)
    for t in texts:
        if t.valid_from is None or (t.valid_to is not None and t.valid_to < t.valid_from):
            continue
        days[t.valid_from].append(t)

    def rank(t: SourceText) -> tuple[bool, date, str, str]:
        return (
            t.valid_to is not None,
            t.published_on or date.min,
            t.source_ref or "",
            t.source_sha256 or "",
        )

    return [max(days[d], key=rank) for d in sorted(days)]


def _build_chain(
    work_id: uuid.UUID,
    jurisdiction_id: uuid.UUID,
    chain: Sequence[SourceText],
    now: datetime,
    prefixes: Collection[str],
) -> tuple[list[Expression], list[Interval], list[Link]]:
    expressions: list[Expression] = []
    intervals: list[Interval] = []
    links: list[Link] = []
    states: dict[str, dict[int, _Open]] = defaultdict(dict)
    language = chain[0].language
    serial = 0

    def close(variant: str, o: _Open, end: date | None) -> None:
        intervals.append(
            Interval(
                work_id=work_id,
                jurisdiction_id=jurisdiction_id,
                language=language,
                variant=variant,
                akn_eid=o.unit.eid,
                valid_from=o.start,
                valid_to=end,
                akn_type=o.unit.akn_type,
                position=o.unit.position,
                text=o.unit.text,
                text_sha256=o.sha,
                text_norm_sha256=o.nsha,
                derivation=o.provenance[0],
                confidence=o.provenance[2],
                official=o.provenance[1],
                opened_by=o.unit.opened_by,
                converter_rev=o.converter_rev,
                builder_rev=BUILDER_REV,
                recorded_at=now,
            )
        )

    def link(
        variant: str, day: date, from_eid: str, to_eid: str, rule: str, conf: float | None
    ) -> None:
        links.append(
            Link(
                work_id=work_id,
                language=language,
                variant=variant,
                linked_on=day,
                from_eid=from_eid,
                to_eid=to_eid,
                rule=rule,
                confidence=conf,
                builder_rev=BUILDER_REV,
                recorded_at=now,
            )
        )

    for i, t in enumerate(chain):
        assert t.valid_from is not None
        day = t.valid_from
        next_start = chain[i + 1].valid_from if i + 1 < len(chain) else None
        end = next_start
        if t.valid_to is not None and t.valid_to < (next_start or date.max):
            end = t.valid_to + timedelta(days=1)
        gap = t.akn_xml is None
        expressions.append(
            Expression(
                work_id=work_id,
                jurisdiction_id=jurisdiction_id,
                language=language,
                variant=t.variant,
                valid_from=day,
                valid_to=end,
                source_ref=t.source_ref,
                source_sha256=t.source_sha256,
                akn_xml=t.akn_xml,
                version_id=t.version_id,
                gap_reason=t.gap_reason if gap else None,
                derivation=t.derivation,
                confidence=t.confidence,
                official=t.official,
                derived_from=t.derived_from,
                converter_rev=t.converter_rev,
                builder_rev=BUILDER_REV,
                recorded_at=now,
            )
        )
        provenance = (t.derivation, t.official, t.confidence)
        by_variant: dict[str, dict[str, _Open]] = defaultdict(dict)
        for u in () if gap else t.units:
            cur = by_variant[u.variant or t.variant]
            if u.eid not in cur:
                norm = normalize_text(u.text)
                cur[u.eid] = _Open(
                    unit=u,
                    norm=norm,
                    sha=_digest(u.text),
                    nsha=_digest(norm),
                    kind=_kind(u, norm),
                    start=day,
                    provenance=provenance,
                    converter_rev=t.converter_rev,
                    path=_number_path(u, prefixes),
                )
        for variant in sorted(set(states) | set(by_variant)):
            open_, cur = states[variant], by_variant.get(variant, {})
            step = _link_units(open_, cur)
            live: dict[str, int] = {}
            for eid, key in step.key_of.items():
                if key is None:
                    serial += 1
                    key = -serial
                live[eid] = key
            kept = set(live.values())
            for key in [k for k in open_ if k not in kept]:
                close(variant, open_.pop(key), day)
            for eid, key in live.items():
                c = cur[eid]
                o = open_.get(key)
                if o is None:
                    if eid in step.source:
                        link(
                            variant, day, step.source[eid], eid, "moved_text", MOVED_TEXT_CONFIDENCE
                        )
                elif (
                    o.sha == c.sha
                    and o.unit.eid == eid
                    and o.unit.akn_type == c.unit.akn_type
                    and o.unit.position == c.unit.position
                    and o.unit.opened_by == c.unit.opened_by
                    and o.provenance == c.provenance
                    and o.converter_rev == c.converter_rev
                ):
                    o.kind = c.kind
                    continue
                else:
                    close(variant, open_.pop(key), day)
                    if o.unit.eid != eid:
                        sim = _similarity(o.norm, c.norm)
                        link(
                            variant,
                            day,
                            o.unit.eid,
                            eid,
                            step.rule[eid],
                            sim if sim < LOW_OVERLAP else None,
                        )
                open_[key] = c
        if next_start is None or end != next_start:
            for variant, open_ in states.items():
                for o in open_.values():
                    close(variant, o, end)
            states.clear()
    return expressions, intervals, links


_Key = tuple[str, str, str, str | None, date, str]


def _rows(build: WorkBuild) -> dict[_Key, tuple[datetime, tuple[Any, ...]]]:
    """Each row by key, with its `recorded_at` and the fields whose change readers must see."""

    def content(row: Expression | Interval | Link) -> tuple[Any, ...]:
        values = tuple(
            getattr(row, f.name) for f in fields(row) if f.name not in {"recorded_at", "confidence"}
        )
        confidence = struct.pack("!f", row.confidence) if row.confidence is not None else None
        return (*values, confidence)

    rows: dict[_Key, tuple[datetime, tuple[Any, ...]]] = {}
    for e in build.expressions:
        key: _Key = ("expression", e.language, e.variant, None, e.valid_from, "")
        rows[key] = (e.recorded_at, content(e))
    for i in build.intervals:
        key = ("interval", i.language, i.variant, i.akn_eid, i.valid_from, "")
        rows[key] = (i.recorded_at, content(i))
    for k in build.links:
        key = ("link", k.language, k.variant, k.to_eid, k.linked_on, k.from_eid)
        rows[key] = (k.recorded_at, content(k))
    return rows


def _check(texts: Sequence[SourceText]) -> None:
    for t in texts:
        if (t.source_ref is None) != (t.source_sha256 is None):
            raise ValueError("source_ref and source_sha256 are given together or not at all")
        if t.confidence is not None and not 0 <= t.confidence <= 1:
            raise ValueError("confidence lies between 0 and 1")


def build_work(
    work_id: uuid.UUID,
    jurisdiction_id: uuid.UUID,
    texts: Sequence[SourceText],
    *,
    now: datetime,
    previous: WorkBuild | None = None,
    unit_prefixes: Collection[str] = DEFAULT_UNIT_PREFIXES,
) -> WorkBuild:
    """Build one work. A row keeps the `recorded_at` of its predecessor when all other persisted
    fields are unchanged; a previous row with no successor becomes a withdrawal.
    `unit_prefixes` names the eId prefixes of the basic unit, such as `sec` or `art`."""
    _check(texts)
    groups: dict[tuple[str, str], list[SourceText]] = defaultdict(list)
    for t in texts:
        groups[(t.language, t.variant)].append(t)
    expressions: list[Expression] = []
    intervals: list[Interval] = []
    links: list[Link] = []
    for key in sorted(groups):
        if chain := _choose(groups[key]):
            e, i, k = _build_chain(work_id, jurisdiction_id, chain, now, unit_prefixes)
            expressions += e
            intervals += i
            links += k
    built = WorkBuild(
        expressions=tuple(sorted(expressions, key=lambda r: (r.language, r.variant, r.valid_from))),
        intervals=tuple(
            sorted(intervals, key=lambda r: (r.language, r.variant, r.akn_eid, r.valid_from))
        ),
        links=tuple(
            sorted(links, key=lambda r: (r.language, r.variant, r.linked_on, r.to_eid, r.from_eid))
        ),
    )
    fresh = _rows(built)
    if len(fresh) != len(built.expressions) + len(built.intervals) + len(built.links):
        raise ValueError("two derived rows share a key; texts of one variant overlap")
    for earlier, later in zip(built.intervals, built.intervals[1:], strict=False):
        if (
            earlier.language == later.language
            and earlier.variant == later.variant
            and earlier.akn_eid == later.akn_eid
            and (earlier.valid_to is None or earlier.valid_to > later.valid_from)
        ):
            raise ValueError("provision intervals overlap across variant chains")
    old = _rows(previous) if previous else {}

    def stamp(key: _Key) -> datetime:
        before = old.get(key)
        return before[0] if before and before[1] == fresh[key][1] else now

    built = WorkBuild(
        expressions=tuple(
            replace(
                r, recorded_at=stamp(("expression", r.language, r.variant, None, r.valid_from, ""))
            )
            for r in built.expressions
        ),
        intervals=tuple(
            replace(
                r,
                recorded_at=stamp(("interval", r.language, r.variant, r.akn_eid, r.valid_from, "")),
            )
            for r in built.intervals
        ),
        links=tuple(
            replace(
                r,
                recorded_at=stamp(
                    ("link", r.language, r.variant, r.to_eid, r.linked_on, r.from_eid)
                ),
            )
            for r in built.links
        ),
    )
    withdrawals = tuple(
        Withdrawal(
            jurisdiction_id=jurisdiction_id,
            work_id=work_id,
            row_kind=kind,
            language=language,
            variant=variant,
            akn_eid=eid,
            from_eid=source if kind == "link" else None,
            valid_from=start,
            builder_rev=BUILDER_REV,
            withdrawn_at=now,
        )
        for kind, language, variant, eid, start, source in sorted(
            (k for k in old if k not in fresh),
            key=lambda k: (k[0], k[1], k[2], k[3] or "", k[4], k[5]),
        )
    )
    return replace(built, withdrawals=withdrawals)
