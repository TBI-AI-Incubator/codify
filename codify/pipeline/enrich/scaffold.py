"""Bluebell skeleton emitter + body-fill response schema."""

from __future__ import annotations

import re
from typing import Iterable

import structlog
from pydantic import BaseModel, Field, field_validator

from codify.pipeline.enrich.anchors import StructuralAnchor
from codify.pipeline.enrich.arabic_normalise import heading_restates_number
from codify.pipeline.enrich.kinds import BLUEBELL_HIER_KEYWORDS, kind_to_kw
from codify.pipeline.enrich.points import nest_enumerated_lines, starts_with_enumerator
from codify.pipeline.enrich.tables import is_pipe_row, nest_pipe_tables
from codify.pipeline.enrich.titles import long_title_lead_ins, opens_long_title

logger = structlog.get_logger()

# Bluebell hier_element_name aliases; emitter vocabulary deliberately excludes these.
_PARSER_HIER_KEYWORDS = BLUEBELL_HIER_KEYWORDS | {
    "ART",
    "CHAP",
    "PARA",
    "SEC",
    "SUBCHAP",
    "SUBPARA",
    "SUBSEC",
}

# Upper case only: Bluebell reads "SECTION 6" as structure and "Section 6 of
# the same Act" as prose. Matching either way refused every amending act.
_STRUCTURAL_LINE_RE = re.compile(
    r"^\s*(" + "|".join(sorted(_PARSER_HIER_KEYWORDS, key=len, reverse=True)) + r")\b"
)


class BodyBlock(BaseModel):
    eid: str
    heading: str | None = None
    lines: list[str] = Field(default_factory=list)

    @field_validator("heading", mode="after")
    @classmethod
    def reject_structural_heading_continuations(cls, value: str | None) -> str | None:
        for line in (value or "").splitlines()[1:]:
            if _STRUCTURAL_LINE_RE.match(line):
                raise ValueError(f"heading line begins with structural keyword: {line!r}")
        if value is not None and len(value.splitlines()) > 1:
            return " ".join(line.strip() for line in value.splitlines() if line.strip())
        return value

    @field_validator("lines", mode="after")
    @classmethod
    def reject_structural_keywords(cls, value: list[str]) -> list[str]:
        lines = [line for block in value for line in (block.splitlines() or [""])]
        for line in lines:
            if _STRUCTURAL_LINE_RE.match(line):
                raise ValueError(f"body line begins with structural keyword: {line!r}")
        return lines


class BodyFillResponse(BaseModel):
    bodies: list[BodyBlock] = Field(default_factory=list)


def _schedule_rooted(anchors: Iterable[StructuralAnchor]) -> set[str]:
    """Eids of anchors inside a schedule subtree (the schedule itself and
    every descendant, e.g. an annex's own articles)."""
    rooted: set[str] = set()
    for a in anchors:
        if a.kind == "schedule" or (a.parent_eid and a.parent_eid in rooted):
            rooted.add(a.akn_eid)
    return rooted


def _header_indent(anchor: StructuralAnchor, schedule_rooted: set[str]) -> str:
    """Schedule anchors open attachments at column zero; their children use one
    fewer indentation level. Schedule text inside a section remains prose.
    """
    if anchor.kind == "schedule":
        return ""
    level = anchor.depth if anchor.akn_eid in schedule_rooted else anchor.depth + 1
    return "  " * level


def _marker_header(anchor: StructuralAnchor, schedule_rooted: set[str], heading: str | None) -> str:
    """The Bluebell header line for an anchor: `{indent}{KEYWORD} {number}` plus
    an optional ` - heading`. Single source so the two emit sites cannot drift
    from the `_is_header` matcher (in assemble_filled_scaffold) that re-detects
    the same line; a silent divergence there drops bodies."""
    keyword = kind_to_kw(anchor.kind)
    indent = _header_indent(anchor, schedule_rooted)
    number = anchor.number or ""
    header = f"{indent}{keyword} {number}".rstrip()
    if heading:
        header = f"{header} - {heading}"
    return header


def _preface_lines(block: str, leads: tuple[str, ...]) -> list[str]:
    """Preface lines, with the long title tagged for Bluebell.

    `LONGTITLE` takes one line but a long title wraps, so continuations join onto it, and
    the lead-in can sit on any line: whether a blank precedes it is an artefact of the
    source's line breaks. The join stops at wrap-terminal punctuation so gazette furniture
    is not swallowed. Only the first match is taken, because a later `An Act ...` cites
    another statute.
    """
    out: list[str] = []
    tagged = False
    for paragraph in re.split(r"\n\s*\n", block.strip()):
        lines = [raw.strip() for raw in paragraph.splitlines() if raw.strip()]
        index = 0
        while index < len(lines):
            if tagged or not leads or not opens_long_title(lines[index], leads):
                out.append(lines[index])
                index += 1
                continue
            title = [lines[index]]
            index += 1
            while index < len(lines) and not title[-1].endswith(_WRAP_TERMINAL):
                title.append(lines[index])
                index += 1
            out.append(f"LONGTITLE {' '.join(title)}")
            tagged = True
    return out


def scaffold_from_anchors(
    anchors: Iterable[StructuralAnchor],
    *,
    preface: str | None = None,
    preamble: str | None = None,
    country: str = "",
) -> tuple[str, dict[str, StructuralAnchor]]:
    """Return (bluebell_skeleton, eid_to_anchor) with empty body lines.

    ``preface`` carries title/cover matter, ``preamble`` the recital chain +
    enacting formula; Bluebell maps the keywords to distinct AKN elements."""
    lines: list[str] = []
    leads = long_title_lead_ins(country)
    for keyword, block in (("PREFACE", preface), ("PREAMBLE", preamble)):
        if block and block.strip():
            lines.append(keyword)
            # The long title lives in the preface; the preamble is recitals and
            # the enacting formula, so it is emitted exactly as before.
            emitted = _preface_lines(block, leads if keyword == "PREFACE" else ())
            lines.extend(f"  {line}" for line in emitted)
            lines.append("")
    lines.append("BODY")

    eid_to_anchor: dict[str, StructuralAnchor] = {}
    anchor_list = list(anchors)
    schedule_rooted = _schedule_rooted(anchor_list)
    for anchor in anchor_list:
        # Quoted-amendment anchors have no eid and are not emitted as peer headers, so
        # they surface as prose under the host article's body-fill. Bluebell
        # <embeddedStructure> integration is deferred; see
        # docs/log/2026-07-22-amendment-quoted-structure.md.
        if anchor.quoted_amendment:
            continue
        lines.append(_marker_header(anchor, schedule_rooted, anchor.heading))
        lines.append("")
        eid_to_anchor[anchor.akn_eid] = anchor
    # Container-only anchors yield no windows, so this scaffold is returned
    # without ever reaching assembly.
    return "\n".join(nest_pipe_tables(lines)) + "\n", eid_to_anchor


_WRAP_TERMINAL = (".", ":", ";", "!", "?", ")", "]", "»", "”", '"', "。")
_ENUM_RE = re.compile(
    r"^\s*(\(?\d+[.)]|\(?[a-zA-Zا-ي][.)]|[ivxlcdm]+[.)]|[-•—*])\s",
    re.IGNORECASE,
)


def _join_soft_wraps(lines: list[str]) -> list[str]:
    """Merge PDF soft-wrapped fragments back into logical paragraphs. A line continues the
    previous one when that one does not end on terminal punctuation and neither it nor this
    one opens a new enumerated item or a table row; otherwise each source wrap becomes its
    own <p> and the reader spaces them apart.
    """
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            out.append("")
            continue
        prev = out[-1] if out else ""
        if (
            prev
            and not prev.endswith(_WRAP_TERMINAL)
            and not _ENUM_RE.match(line)
            and not starts_with_enumerator(line)
            and not is_pipe_row(line)
            and not is_pipe_row(prev)
        ):
            out[-1] = f"{prev} {line}"
        else:
            out.append(line)
    return out


def assemble_filled_scaffold(
    scaffold: str,
    eid_to_anchor: dict[str, StructuralAnchor],
    response: BodyFillResponse,
    *,
    literal_body_eids: frozenset[str] = frozenset(),
) -> str:
    """Splice response.bodies into scaffold keyed by eid; orphan bodies drop."""
    bodies_by_eid = {block.eid: block for block in response.bodies}
    out_lines: list[str] = []
    schedule_rooted = _schedule_rooted(eid_to_anchor.values())
    anchor_iter = iter(eid_to_anchor.values())
    scaffold_lines = scaffold.splitlines()
    pending = next(anchor_iter, None)

    def _is_header(line: str, anchor: StructuralAnchor) -> bool:
        if not anchor:
            return False
        kw = kind_to_kw(anchor.kind)
        stripped = line.strip()
        return stripped.startswith(f"{kw} ") or stripped == kw

    i = 0
    while i < len(scaffold_lines):
        line = scaffold_lines[i]
        if pending is not None and _is_header(line, pending):
            block = bodies_by_eid.get(pending.akn_eid)
            # A heading the scanner captured is the source's own words and wins over
            # the model's. The reverse precedence let fill overwrite captured headings
            # with bare ordinals restating their num, and made heading text drift
            # between runs. The model supplies one only where the source gave none, and
            # not one that merely repeats the number.
            filled = block.heading if block else None
            if filled and heading_restates_number(filled, pending.number or ""):
                logger.info(
                    "container_heading_fill_rejected",
                    eid=pending.akn_eid,
                    reason="restates_num",
                    heading=filled,
                )
                filled = None
            heading = pending.heading or filled
            out_lines.append(_marker_header(pending, schedule_rooted, heading))
            if i + 1 < len(scaffold_lines) and not scaffold_lines[i + 1].strip():
                i += 1
            body_lines = list(block.lines) if block else []
            # The body-fill window includes the heading line for anchors whose
            # heading came from the source's next line; drop the duplicate.
            if heading and body_lines and body_lines[0].strip() == heading.strip():
                body_lines = body_lines[1:]
            if body_lines:
                body_indent = _header_indent(pending, schedule_rooted) + "  "
                # Enumerated sub-points are rewritten as nested POINT markup (with their
                # own relative indent) and pipe-table rows as nested TABLE markup, so
                # Bluebell emits <point>s and <table>s instead of flat paragraphs.
                joined = _join_soft_wraps(body_lines)
                nested = nest_pipe_tables(
                    joined
                    if pending.akn_eid in literal_body_eids
                    else nest_enumerated_lines(joined)
                )
                for body_line in nested:
                    body_text = body_line.rstrip()
                    if not body_text:
                        out_lines.append("")
                        continue
                    out_lines.append(f"{body_indent}{body_text}")
            out_lines.append("")
            pending = next(anchor_iter, None)
        else:
            out_lines.append(line)
        i += 1

    # Pass-through lines and front matter never enter the window branch above.
    # Front matter holds a table: the root is a parser argument, not a line here.
    out_lines = nest_pipe_tables(out_lines)
    return "\n".join(out_lines) + ("\n" if not out_lines or out_lines[-1] != "" else "")


__all__ = [
    "BodyBlock",
    "BodyFillResponse",
    "scaffold_from_anchors",
    "assemble_filled_scaffold",
]
