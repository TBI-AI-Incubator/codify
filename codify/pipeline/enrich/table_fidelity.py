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


def preserve_source_tables(
    text: str, anchors: list[StructuralAnchor], bodies: dict[str, BodyBlock]
) -> frozenset[str]:
    """Restore only the affected anchor's source span; normalise cell whitespace only."""
    restored_eids: set[str] = set()
    ordered = sorted(anchors, key=lambda a: a.char_offset)
    for i, anchor in enumerate(ordered):
        end = ordered[i + 1].char_offset if i + 1 < len(ordered) else len(text)
        source = text[anchor.char_offset : end]
        lines = source.split("\n", 1)[1].splitlines() if "\n" in source else []
        if not has_table(lines):
            continue
        body = bodies.get(anchor.akn_eid)
        if body is not None and _rows(lines) == _rows(body.lines):
            continue
        first_line = source.partition("\n")[0].lstrip()
        matched = anchor.matched_text.strip()
        remainder = first_line[len(matched) :] if matched and first_line.startswith(matched) else ""
        heading = anchor.heading or remainder.strip(" .-—:") or None
        source_lines = [line.strip() for line in lines if line.strip()]
        if heading and source_lines and source_lines[0] == heading:
            source_lines = source_lines[1:]
        bodies[anchor.akn_eid] = BodyBlock(
            eid=anchor.akn_eid, heading=heading, lines=literal_pipe_body(source_lines)
        )
        restored_eids.add(anchor.akn_eid)
        logger.warning("body_fill_table_restored", eid=anchor.akn_eid, rows=len(_rows(lines)))
    return frozenset(restored_eids)
