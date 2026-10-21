"""The Council of State's typed units as Bluebell, parsed to AKN without a model.

Each unit already names its kind (title, chapter, section, clause, schedule), so the
structure is read, not inferred. Paragraphs inside a unit stay paragraphs.
"""

from __future__ import annotations

import re
from typing import Any

from codify.acquisition.adapters.th.ocs import section_text
from codify.akn.bluebell import bluebell_to_akn

PREFACE = {"1", "2"}
PREAMBLE = {"3", "17"}
PROVISIONS = {"4", "10"}  # มาตรา, ข้อ
GROUPS = {"7": ("PART", 0), "8": ("CHAPTER", 1), "9": ("DIVISION", 2)}
CROSSHEADINGS = {"13"}  # บทเฉพาะกาล
CONCLUSIONS = {"14", "15", "19"}
SCHEDULES = {"16", "20"}

_DIGIT = "[0-9๐-๙]"
_NUM = rf"{_DIGIT}+(?:/{_DIGIT}+)?(?:\s*(?:ทวิ|ตรี|จัตวา|เบญจ|ฉ|สัตต|อัฏฐ|นว|ทศ))?"
_PROVISION = re.compile(rf"^(?:มาตรา|ข้อ)\s*({_NUM})")
_GROUP = re.compile(rf"^(?:ลักษณะ|หมวด|ส่วน)(?:ที่)?\s*({_NUM})\s*(.*)$")
# Inline markers and the backslash; one escaped character breaks each.
_INLINE = re.compile(r"\*\*|//|__|\{\{|\}\}|\[\[|\]\]|\^\^|_\^|\\")
_KEYWORD_START = re.compile(r"^[A-Z]{2,}")


class UnmappedUnit(ValueError):
    """A unit kind the mapping does not place; refused rather than dropped."""


def escape(line: str) -> str:
    out = _INLINE.sub(lambda m: "\\" + m.group(0), line)
    return "\\" + out if _KEYWORD_START.match(out) else out


def _lines(unit: dict[str, Any]) -> list[str]:
    return [escape(line) for line in section_text(unit.get("sectionContent") or "").splitlines()]


def to_bluebell(units: list[dict[str, Any]]) -> str:
    """Units in their stated order as one Bluebell document."""
    preface: list[str] = []
    preamble: list[str] = []
    body: list[str] = []
    conclusions: list[str] = []
    schedules: list[str] = []
    depth = 0  # indent of the next provision, under the open groups
    open_levels: list[int] = []
    for unit in sorted(units, key=lambda u: u.get("sectionSeq") or 0):
        kind = str(unit.get("sectionTypeId"))
        lines = _lines(unit)
        if not lines:
            continue
        if kind in PREFACE | PREAMBLE and body:
            # A consolidation appends each amending law's title after the body.
            conclusions += lines
        elif kind in PREFACE:
            preface += lines
        elif kind in PREAMBLE:
            preamble += lines
        elif kind in GROUPS:
            keyword, level = GROUPS[kind]
            while open_levels and open_levels[-1] >= level:
                open_levels.pop()
            m = _GROUP.match(section_text(unit.get("sectionContent") or "").replace("\n", " "))
            num, heading = (m.group(1), m.group(2)) if m else ("", " ".join(lines))
            pad = "  " * (len(open_levels) + 1)
            body.append(
                f"{pad}{keyword} {num}".rstrip() + (f" - {escape(heading)}" if heading else "")
            )
            open_levels.append(level)
            depth = len(open_levels)
        elif kind in CROSSHEADINGS:
            body.append(f"{'  ' * (depth + 1)}CROSSHEADING {' '.join(lines)}")
        elif kind in PROVISIONS:
            full = section_text(unit.get("sectionContent") or "")
            m = _PROVISION.match(full)
            if m is None:
                raise UnmappedUnit(f"provision without a number: {full[:60]!r}")
            pad = "  " * (depth + 1)
            body.append(f"{pad}SEC {m.group(1)}")
            body += [f"{pad}  {escape(x)}" for x in full[m.end(1) :].strip().splitlines() if x]
        elif kind in CONCLUSIONS:
            conclusions += lines
        elif kind in SCHEDULES:
            schedules += ["", f"SCHEDULE {lines[0]}", "  BODY"] + [
                f"    {x}" for x in lines[1:] or ["-"]
            ]
        else:
            raise UnmappedUnit(f"unit type {kind} ({unit.get('sectionLabel')!r})")
    if not body:
        raise UnmappedUnit("no provision to form a body")
    doc: list[str] = []
    if preface:
        doc += ["PREFACE"] + [f"  {x}" for x in preface]
    if preamble:
        doc += ["PREAMBLE"] + [f"  {x}" for x in preamble]
    doc += ["BODY"] + body
    if conclusions:
        doc += ["CONCLUSIONS"] + [f"  {x}" for x in conclusions]
    return "\n".join(doc + schedules) + "\n"


def to_akn(units: list[dict[str, Any]], *, doctype: str, date: str, number: str) -> str:
    """AKN XML for the units; a Bluebell parse error is raised, never swallowed."""
    akn, _, errors = bluebell_to_akn(
        to_bluebell(units), country="th", doctype=doctype, date=date, number=number, language="tha"
    )
    if errors:
        e = errors[0]
        raise UnmappedUnit(f"Bluebell rejected line {e.line}:{e.column}: {e.message}")
    return akn


__all__ = ["UnmappedUnit", "escape", "to_akn", "to_bluebell"]
