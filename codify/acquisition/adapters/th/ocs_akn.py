"""The Council of State's typed units as Bluebell, parsed to AKN without a model.

Each unit already names its kind (title, chapter, section, clause, schedule), so the
structure is read, not inferred. Paragraphs inside a unit stay paragraphs.

A consolidation appends each amending law after the body, title first. Units are
split into one segment per title that follows a body: the segment titled as the law
is the body, every other segment an attachment, so no law's sections land in another's.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

from codify.acquisition.adapters.th.ocs import section_text
from codify.akn import parse_xml
from codify.akn.bluebell import bluebell_to_akn
from codify.lang import normalise_digits
from codify.pipeline.formats.akn_native import canonicalise_identification

PREFACE = {"1", "2"}
PREAMBLE = {"3", "17"}
PROVISIONS = {"4", "10"}  # มาตรา, ข้อ
# บรรพ and ภาค are parts, ลักษณะ a title, as the jurisdiction's structuring names them.
GROUPS = {
    "5": ("PART", 0),
    "6": ("PART", 0),
    "7": ("TITLE", 1),
    "8": ("CHAPTER", 2),
    "9": ("DIVISION", 3),
}
CROSSHEADINGS = {"13"}  # บทเฉพาะกาล
PROSE = {"18"}  # unnumbered operative text
CONCLUSIONS = {"14", "15", "19"}
SCHEDULES = {"16", "20"}
BODY_KINDS = PROVISIONS | set(GROUPS) | CROSSHEADINGS | PROSE

_DIGIT = "[0-9๐-๙]"
_NUM = rf"{_DIGIT}+(?:/{_DIGIT}+)?"
_GROUP_TERM = r"(?:บรรพ|ภาค|ลักษณะ|หมวด|ส่วน)(?:ที่)?"
_IMG = re.compile(r"<img\b[^>]*?\bsrc=\"([^\"\s]+)\"[^>]*>", re.IGNORECASE)
# Every character an inline marker can start with; Bluebell takes `\` before any.
_MARKER_CHAR = re.compile(r"([*/_{}\[\]^\\])")
_KEYWORD_START = re.compile(r"^[A-Z]{2,}")


class UnmappedUnit(ValueError):
    """A unit kind the mapping does not place; refused rather than dropped."""


def escape(line: str) -> str:
    out = _MARKER_CHAR.sub(r"\\\1", line)
    return "\\" + out if _KEYWORD_START.match(out) else out


def _kind(unit: dict[str, Any]) -> str:
    return str(unit.get("sectionTypeId"))


def _html(unit: dict[str, Any]) -> str:
    return str(unit.get("sectionContent") or "")


def _stream(html: str) -> list[tuple[str, bool]]:
    """Text lines and images (as `(src, True)`), in source order."""
    out: list[tuple[str, bool]] = []
    for i, part in enumerate(_IMG.split(html)):
        if i % 2:
            out.append((part, True))
        else:
            out += [(x, False) for x in section_text(part).splitlines()]
    return out


def _render(stream: list[tuple[str, bool]]) -> list[str]:
    return [f"{{{{IMG {x}}}}}" if image else escape(x) for x, image in stream]


def _lines(html: str) -> list[str]:
    """Escaped text lines, each image its own line, in source order."""
    return _render(_stream(html))


def _without_label(stream: list[tuple[str, bool]], label: str) -> list[tuple[str, bool]]:
    """The stream with its leading label removed, across lines, images kept in place."""
    want = re.sub(r"\s+", "", label)
    out: list[tuple[str, bool]] = []
    for text, image in stream:
        if want and not image:
            kept = text.lstrip()
            while want and kept and kept[0] == want[0]:
                kept, want = kept[1:].lstrip(), want[1:]
            text = kept
        if text:
            out.append((text, image))
    return out


def _numbered(unit: dict[str, Any], term: str) -> tuple[str, str, str] | None:
    """(number as printed, the text after it, the label as printed). An ordinal
    suffix (ทวิ, เอกาทศ, ...) is part of the number only where the unit's label has it."""
    tokens = str(unit.get("sectionLabel") or "").split()
    suffix = tokens[2] if len(tokens) > 2 and tokens[2] != "ถึง" else ""
    text = section_text(_IMG.sub("", _html(unit)))
    for tail in ([rf"\s*{re.escape(suffix)}"] if suffix else []) + [""]:
        m = re.match(rf"^{term}\s*({_NUM}{tail})", text)
        if m:
            return " ".join(m.group(1).split()), text[m.end(1) :].strip(), text[: m.end(1)]
    return None


def _segments(units: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    segments: list[list[dict[str, Any]]] = [[]]
    in_body = False
    for unit in sorted(units, key=lambda u: u.get("sectionSeq") or 0):
        if _kind(unit) == "1" and in_body:
            segments.append([])
            in_body = False
        segments[-1].append(unit)
        in_body = in_body or _kind(unit) in BODY_KINDS
    return segments


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", normalise_digits(text))


def _title(segment: list[dict[str, Any]]) -> str:
    return " ".join(" ".join(section_text(_html(u)).split()) for u in segment if _kind(u) == "1")


def _attachment(segment: list[dict[str, Any]]) -> list[str]:
    """Another law's units as one schedule: heading, then every line as printed."""
    heading = _title(segment) or " ".join(section_text(_html(segment[0])).split())
    lines = [x for u in segment if _kind(u) != "1" for x in _lines(_html(u))]
    return ["", f"SCHEDULE {escape(heading)}"] + [f"  {x}" for x in lines]


def to_bluebell(units: list[dict[str, Any]], *, title: str = "") -> str:
    """Units in their stated order as one Bluebell document. `title` picks the
    segment that is the law itself; otherwise the first."""
    segments = _segments(units)
    main = next((s for s in segments if title and _squash(_title(s)) == _squash(title)), None)
    main = main or segments[0]
    preface: list[str] = []
    preamble: list[str] = []
    body: list[str] = []
    conclusions: list[str] = []
    schedules: list[str] = []
    depth = 0  # indent of the next provision, under the open groups
    open_levels: list[int] = []
    last_body = max((i for i, u in enumerate(main) if _kind(u) in BODY_KINDS), default=-1)
    # A decree whose whole operative text is its enabling words has no other body.
    enacting_is_body = last_body < 0
    for i, unit in enumerate(main):
        kind = _kind(unit)
        lines = _lines(_html(unit))
        if not lines:
            continue
        pad = "  " * (depth + 1)
        if kind in PREFACE | PREAMBLE and body:
            conclusions += lines
        elif kind in PREFACE:
            preface += lines
        elif kind == "17" and enacting_is_body:
            body += [f"{pad}{x}" for x in lines]
        elif kind in PREAMBLE:
            preamble += lines
        elif kind in GROUPS:
            keyword, level = GROUPS[kind]
            while open_levels and open_levels[-1] >= level:
                open_levels.pop()
            got = _numbered(unit, _GROUP_TERM)
            num, heading = got[:2] if got else ("", " ".join(section_text(_html(unit)).split()))
            heading = " ".join(heading.split())
            pad = "  " * (len(open_levels) + 1)
            body.append(
                f"{pad}{keyword} {num}".rstrip() + (f" - {escape(heading)}" if heading else "")
            )
            open_levels.append(level)
            depth = len(open_levels)
        elif kind in CROSSHEADINGS or (kind in SCHEDULES and i < last_body):
            # A schedule-typed unit followed by provisions heads them; it is no annex.
            body.append(f"{pad}CROSSHEADING {' '.join(lines)}")
        elif kind in PROVISIONS:
            got = _numbered(unit, "(?:มาตรา|ข้อ)")
            labelled = re.search(rf"{_NUM}(?:\s+\S+)?$", str(unit.get("sectionLabel") or ""))
            if got is None:
                if labelled is None:
                    # A lone unnumbered provision is printed as prose; so it stays.
                    body += [f"{pad}{x}" for x in lines]
                    continue
                got = (labelled.group(0), section_text(_IMG.sub("", _html(unit))), "")
            body.append(f"{pad}SEC {got[0]}")
            label = got[2]
            body += [f"{pad}  {x}" for x in _render(_without_label(_stream(_html(unit)), label))]
        elif kind in PROSE:
            body += [f"{pad}{x}" for x in lines]
        elif kind in CONCLUSIONS:
            conclusions += lines
        elif kind in SCHEDULES:
            schedules += ["", f"SCHEDULE {lines[0]}"] + [f"  {x}" for x in lines[1:]]
        else:
            raise UnmappedUnit(f"unit type {kind} ({unit.get('sectionLabel')!r})")
    if not body:
        raise UnmappedUnit("no operative text to form a body")
    for segment in segments:
        if segment is not main:
            schedules += _attachment(segment)
    doc: list[str] = []
    if preface:
        doc += ["PREFACE"] + [f"  {x}" for x in preface]
    if preamble:
        doc += ["PREAMBLE"] + [f"  {x}" for x in preamble]
    doc += ["BODY"] + body
    if conclusions:
        doc += ["CONCLUSIONS"] + [f"  {x}" for x in conclusions]
    return "\n".join(doc + schedules) + "\n"


def to_akn(
    units: list[dict[str, Any]],
    *,
    doctype: str,
    number: str,
    work: str,
    published: str,
    expression: str,
    as_of: str,
    title: str = "",
) -> str:
    """AKN XML for the units, identified as `work` (dated `published`) and
    `expression` (dated `as_of`). A Bluebell parse error is raised, never swallowed."""
    akn, _, errors = bluebell_to_akn(
        to_bluebell(units, title=title),
        country="th",
        doctype=doctype,
        date=published,
        number=number,
        language="tha",
    )
    if errors:
        e = errors[0]
        raise UnmappedUnit(f"Bluebell rejected line {e.line}:{e.column}: {e.message}")
    root = parse_xml(canonicalise_identification(akn, work, expression))
    for level, day in (("FRBRWork", published), ("FRBRExpression", as_of)):
        for node in root.iterfind(
            f"./{{*}}*/{{*}}meta/{{*}}identification/{{*}}{level}/{{*}}FRBRdate"
        ):
            node.set("date", day)
    return str(etree.tostring(root, encoding="unicode"))


__all__ = ["UnmappedUnit", "escape", "to_akn", "to_bluebell"]
