"""Keep held table cells when body fill changes or drops their rows."""

from __future__ import annotations

import re

import structlog

from codify.pipeline.enrich.anchors import StructuralAnchor
from codify.pipeline.enrich.scaffold import BodyBlock
from codify.pipeline.enrich.tables import has_table, is_pipe_row, literal_pipe_body

logger = structlog.get_logger()


def _rows(lines: list[str]) -> list[tuple[str, ...]]:
    """Include separators: their presence declares an otherwise solitary table row."""
    return [
        tuple(re.sub(r"\s+", " ", cell.strip()) for cell in line.strip().split("|")[1:-1])
        for line in lines
        if is_pipe_row(line)
    ]


# Footnote brackets and amendment stars after the number are not a heading.
_DECORATIONS_RE = re.compile(r"^(?:[ \t]*(?:\*|\[[^\]\n]{1,6}\]))+")


def preserve_source_tables(
    text: str, anchors: list[StructuralAnchor], bodies: dict[str, BodyBlock]
) -> frozenset[str]:
    """Restore only the affected anchor's source span; normalise cell whitespace only."""
    restored_eids: set[str] = set()
    ordered = sorted(anchors, key=lambda a: a.char_offset)
    for i, anchor in enumerate(ordered):
        end = ordered[i + 1].char_offset if i + 1 < len(ordered) else len(text)
        source = text[anchor.char_offset : end].lstrip()
        # The marker may wrap (a suffix on its own line): consume all of it first.
        matched = anchor.matched_text.strip()
        after = source[len(matched) :] if matched and source.startswith(matched) else source
        after = _DECORATIONS_RE.sub("", after, count=1)
        first_line, _, rest = after.partition("\n")
        lines = rest.splitlines()
        if not has_table(lines):
            continue
        body = bodies.get(anchor.akn_eid)
        if body is not None and _rows(lines) == _rows(body.lines):
            continue
        heading = anchor.heading or first_line.strip(" .-—:\r") or None
        source_lines = [line.strip() for line in lines if line.strip()]
        if heading and source_lines and source_lines[0] == heading:
            source_lines = source_lines[1:]
        bodies[anchor.akn_eid] = BodyBlock(
            eid=anchor.akn_eid, heading=heading, lines=literal_pipe_body(source_lines)
        )
        restored_eids.add(anchor.akn_eid)
        logger.warning("body_fill_table_restored", eid=anchor.akn_eid, rows=len(_rows(lines)))
    return frozenset(restored_eids)
