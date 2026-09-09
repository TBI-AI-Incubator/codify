"""Give a table row the context its own text omits: a quarter read "Other".

Header, band, code prefix and indent depth, composed and detected from the
table's shape. Columns found by content, never position. English vocabularies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from lxml import etree

from codify.akn._schema import parse_xml
from codify.akn.vocabulary import table_row_eid

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

Mechanism = Literal["header", "group", "prefix", "indent"]

# Outline depth: repeated dashes, usually spaced. Publishers mix the forms.
_DASH = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212-"
_INDENT = re.compile(f"^[\\s\u00a0]*((?:[{_DASH}][\\s\u00a0]*)+)")
# A column of outline depths rather than data.
_DEPTH_HEADER = re.compile(r"^(h\*?|l\*?|level|depth|tier)$", re.IGNORECASE)
_CODE = re.compile(r"^[0-9][0-9.\s ]*$")
# Below this a column of numbers is an ordinal or a quantity, not a code tree.
_MIN_NESTING_VALUES = 4
# Uncapped the frame was four fifths of the embedded text, so every row of one
# act looked alike.
_MAX_TITLE = 90


class TableIdentityError(LookupError):
    """A table carries no eId, so its rows have no stable address."""


@dataclass(frozen=True)
class ResolvedCell:
    """One cell, paired with the column header that says what it holds."""

    column: int
    label: str
    text: str


@dataclass(frozen=True)
class ResolvedRow:
    """One row and what it needs to read alone. `lineage` is outermost first;
    `mechanisms` records how the context was found, for coverage reporting."""

    akn_eid: str
    table_eid: str
    row_index: int
    cells: tuple[ResolvedCell, ...]
    lineage: tuple[str, ...]
    frame: str
    resolved_text: str
    mechanisms: frozenset[Mechanism] = field(default_factory=frozenset)


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _text(el: etree._Element) -> str:
    return " ".join("".join(el.itertext()).split())


def _block_text(el: etree._Element) -> str:
    """Text of a block container. `itertext` would run its words together."""
    parts = [" ".join(t.split()) for t in el.itertext()]
    return " ".join(p for p in parts if p)


def _strip_indent(value: str) -> str:
    return _INDENT.sub("", value).strip().rstrip(":").strip()


def _indent_depth(value: str) -> int:
    match = _INDENT.match(value)
    return sum(c in _DASH for c in match.group(1)) if match else 0


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _nesting_column(rows: list[list[str]]) -> int | None:
    """The column whose values nest by prefix, found by content not position."""
    width = max((len(r) for r in rows), default=0)
    for column in range(width):
        values = [
            _digits(r[column])
            for r in rows
            if column < len(r) and _CODE.match(r[column].strip() or "x")
        ]
        values = [v for v in values if v]
        if len(values) < _MIN_NESTING_VALUES:
            continue
        if any(len(a) < len(b) and b.startswith(a) for a, b in zip(values, values[1:])):
            return column
    return None


def _description_column(rows: list[list[str]], skip: set[int]) -> int | None:
    """The column carrying prose, by weight of letters. Taken positionally a
    reversed table hands back the duty: "Copper wire > 4,8"."""
    width = max((len(r) for r in rows), default=0)
    best, best_score = None, 0
    for column in range(width):
        if column in skip:
            continue
        score = sum(len(re.findall(r"[^\W\d_]", r[column])) for r in rows if column < len(r))
        if score > best_score:
            best, best_score = column, score
    return best


def _depth_column(header: list[str]) -> int | None:
    for index, label in enumerate(header):
        if _DEPTH_HEADER.match(label.strip()):
            return index
    return None


def _is_group_row(cells: list[etree._Element], width: int) -> bool:
    """One cell spanning the table heads what follows. The span, not the cell
    count: a ragged row would otherwise become a heading."""
    if width < 2 or len(cells) != 1:
        return False
    span = cells[0].get("colspan")
    return bool(span) and int(span) >= width


def _logical_grid(rows: list[etree._Element]) -> list[list[str]]:
    """Rows as a rectangle, spans occupying what they cover: paired off the
    tree, a spanned row's cells slide onto the wrong headers."""
    grid: list[list[str]] = []
    carried: dict[int, tuple[str, int]] = {}
    for row in rows:
        line: list[str] = []
        for cell in _row_cells(row):
            while len(line) in carried:
                text, left = carried[len(line)]
                line.append(text)
                if left <= 1:
                    del carried[len(line) - 1]
                else:
                    carried[len(line) - 1] = (text, left - 1)
            value = _text(cell)
            span = cell.get("colspan")
            down = cell.get("rowspan")
            width = int(span) if span and span.isdigit() else 1
            depth = int(down) if down and down.isdigit() else 1
            for _ in range(max(1, width)):
                if depth > 1:
                    carried[len(line)] = (value, depth - 1)
                line.append(value)
        # Trailing carries are never advanced into, so they would surface on a
        # later wide row as an extra column holding a stale value.
        for column in sorted(c for c in carried if c >= len(line)):
            text, left = carried.pop(column)
            while len(line) < column:
                line.append("")
            line.append(text)
            if left > 1:
                carried[column] = (text, left - 1)
        grid.append(line)
    return grid


def _table_rows(table: etree._Element) -> list[etree._Element]:
    return [r for r in table if _local(r) == "tr"]


def _row_cells(row: etree._Element) -> list[etree._Element]:
    return [c for c in row if _local(c) in ("th", "td")]


def _header_labels(
    rows: list[etree._Element], grid: list[list[str]], width: int
) -> tuple[list[str], int]:
    """Column labels and their row, or ([], -1). A band can precede the header
    and is usually a header cell itself, so it is skipped."""
    for index, row in enumerate(rows[:3]):
        cells = _row_cells(row)
        if _is_group_row(cells, width):
            continue
        if cells and all(_local(c) == "th" for c in cells):
            # From the grid: a colspan label covers every column it spans.
            labels = grid[index] if index < len(grid) else [_text(c) for c in cells]
            if any(labels):
                return labels, index
    return [], -1


def _frame_for(table: etree._Element, document_title: str) -> str:
    """Act, annex and caption. Every row carries it: a listing entry reads as
    nothing without it."""
    parts: list[str] = []
    if document_title:
        parts.append(document_title)
    for ancestor in table.iterancestors():
        if _local(ancestor) != "attachment":
            continue
        # Both shapes: an attachment's own heading, and AKN4EU's header
        # container. Direct children only, or the first table's title inside
        # the annex answers for every row in it.
        parts.extend(_block_text(c) for c in ancestor if _local(c) == "heading" and _block_text(c))
        header = next((c for c in ancestor.iter() if c.get("name") == "headerOfAnnex"), None)
        if header is not None:
            parts.extend(t for t in (_block_text(b) for b in header) if t)
        break
    # The table's number and subject: AKN4EU on the wrapper, plain AKN in a
    # caption. An importer we do not control may use either, and may or may not
    # interpose a <content>, so the wrapper is found by ancestry not by depth.
    for ancestor in table.iterancestors():
        if ancestor.get("name") == "TAB":
            parts.extend(
                _block_text(c)
                for c in ancestor
                if _local(c) in ("num", "heading") and _block_text(c)
            )
            break
        if _local(ancestor) in ("attachment", "mainBody", "body"):
            break
    parts.extend(_block_text(c) for c in table if _local(c) == "caption" and _block_text(c))
    # A path reads better than a sentence and survives truncation from the end.
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return " · ".join(seen)


def _document_title(root: etree._Element) -> str:
    """The instrument's name, cut at a word boundary."""
    for name in ("shortTitle", "docTitle", "longTitle"):
        el = next((e for e in root.iter() if _local(e) == name), None)
        title = _block_text(el) if el is not None else ""
        if not title:
            continue
        if len(title) <= _MAX_TITLE:
            return title
        return title[:_MAX_TITLE].rsplit(" ", 1)[0]
    return ""


def _resolve_table(table: etree._Element, document_title: str) -> list[ResolvedRow]:
    rows = _table_rows(table)
    if not rows:
        return []
    table_eid = table.get("eId") or ""
    if not table_eid:
        # AKN requires an eId on <table>. Minting one here would give two
        # eId-less tables the same row addresses.
        raise TableIdentityError("a <table> carries no eId; rows cannot be addressed")
    frame = _frame_for(table, document_title)

    grid = _logical_grid(rows)
    width = max((len(r) for r in grid), default=0)
    header, header_at = _header_labels(rows, grid, width)
    body = [row for index, row in enumerate(grid) if index != header_at]

    nest_col = _nesting_column(body)
    depth_col = _depth_column(header)
    desc_col = _description_column(body, {c for c in (nest_col, depth_col) if c is not None})
    has_indent = depth_col is not None or any(
        _indent_depth(r[desc_col]) for r in body if desc_col is not None and desc_col < len(r)
    )

    # Ancestors held open as the walk descends, keyed by depth.
    by_code: dict[str, str] = {}
    open_at_depth: dict[int, str] = {}
    group: list[str] = []
    out: list[ResolvedRow] = []

    for offset, row_el in enumerate(rows):
        if offset == header_at:
            continue
        cells_text = grid[offset]
        mechanisms: set[Mechanism] = set()
        if header:
            mechanisms.add("header")

        if _is_group_row(_row_cells(row_el), width):
            label = next((c for c in cells_text if c.strip()), "")
            group = [_strip_indent(label)] if label else []
            open_at_depth.clear()
            by_code.clear()
            continue

        lineage: list[str] = list(group)
        if group:
            mechanisms.add("group")

        if desc_col is not None and desc_col < len(cells_text):
            description = cells_text[desc_col]
        else:
            skip = {i for i in (nest_col, depth_col) if i is not None}
            description = next(
                (c for i, c in enumerate(cells_text) if i not in skip and c.strip()), ""
            )
        if nest_col is not None and nest_col < len(cells_text):
            code = _digits(cells_text[nest_col])
            if code:
                ancestors = [by_code[code[:n]] for n in range(1, len(code)) if code[:n] in by_code]
                if ancestors:
                    lineage.extend(ancestors)
                    mechanisms.add("prefix")
                by_code[code] = _strip_indent(description)

        depth = 0
        if depth_col is not None and depth_col < len(cells_text):
            raw = cells_text[depth_col].strip()
            depth = int(raw) if raw.isdigit() else 0
            if depth:
                mechanisms.add("indent")
        elif description:
            depth = _indent_depth(description)
            if depth:
                mechanisms.add("indent")
        if depth and (nest_col is None or has_indent):
            # A schedule interleaves headings that carry no code ("- Horses:"),
            # so the dash ladder places what the prefix chain cannot see.
            lineage = [*group, *(open_at_depth[d] for d in sorted(open_at_depth) if d < depth)]
            mechanisms.discard("prefix")
        if description:
            open_at_depth[depth] = _strip_indent(description)
            for held in [d for d in open_at_depth if d > depth]:
                del open_at_depth[held]

        cells = tuple(
            ResolvedCell(
                column=index,
                label=header[index] if index < len(header) else "",
                text=value,
            )
            for index, value in enumerate(cells_text)
        )
        row_eid = row_el.get("eId") or table_row_eid(table_eid, offset + 1)
        out.append(
            ResolvedRow(
                akn_eid=row_eid,
                table_eid=table_eid,
                row_index=offset + 1,
                cells=cells,
                lineage=tuple(x for x in lineage if x),
                frame=frame,
                resolved_text=_compose(frame, lineage, description, cells),
                mechanisms=frozenset(mechanisms),
            )
        )
    return out


def _compose(
    frame: str, lineage: list[str], description: str, cells: tuple[ResolvedCell, ...]
) -> str:
    """Frame, then the ancestor path ending in this row, then the cells."""
    parts: list[str] = []
    if frame:
        parts.append(frame)
    path = [x for x in lineage if x]
    leaf = _strip_indent(description)
    if path:
        parts.append(" > ".join([*path, leaf] if leaf else path))
    for cell in cells:
        if not cell.text.strip():
            continue
        if path and cell.text == description:
            continue
        parts.append(f"{cell.label}: {cell.text}" if cell.label else cell.text)
    return ". ".join(p for p in parts if p)


def resolve_tables(akn: str | bytes) -> list[ResolvedRow]:
    """Every table row in the document, with its context resolved."""
    root = parse_xml(akn)
    title = _document_title(root)
    out: list[ResolvedRow] = []
    for table in root.iter("{%s}table" % AKN_NS):
        out.extend(_resolve_table(table, title))
    return out


__all__ = [
    "Mechanism",
    "ResolvedCell",
    "ResolvedRow",
    "TableIdentityError",
    "resolve_tables",
]
