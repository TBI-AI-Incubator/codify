"""Reconstruct table structure from flat markdown pipe rows.

Transcription is asked for pipe rows (`ocr_system.txt`) and body-fill to carry them,
per window that has one (`structure.TABLE_ROWS_RULE`). Neither asks for Bluebell's
`TABLE`/`TR`/`TH`/`TC` keywords, so this module is what makes the rows a table, as
`points.nest_enumerated_lines` does for enumerated points.

Stated exclusion: a lead-in title line spanning the whole table (rendered as its own
`TH{colspan N}` row in the synthetic factory) is not detected here — in real body text a
short line before a table is common prose, not reliably a caption, so misreading one as a
colspan header risks corrupting real content. Only the pipe rows themselves are converted.
"""

from __future__ import annotations

import re

_INDENT = "  "
_SEPARATOR_RE = re.compile(r"^\|?[\s:_-]+(\|[\s:_-]+)+\|?$")


def is_pipe_row(line: str) -> bool:
    """A `| cell | cell |` markdown table row, header/body or separator alike."""
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3


def _is_separator_row(line: str) -> bool:
    return bool(_SEPARATOR_RE.match(line.strip()))


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().split("|")[1:-1]]


def _table_markers(lines: list[str]) -> int:
    return sum(line.strip() == "TABLE" for line in lines)


def has_table(lines: list[str]) -> bool:
    """Whether `nest_pipe_tables` would build a table from these lines. Counts the
    markers it added rather than re-deriving its rule: `is_pipe_row` alone is true of
    a lone pipe-shaped line the nester leaves as prose, and a source line already
    reading `TABLE` passes through it untouched."""
    return _table_markers(nest_pipe_tables(lines)) > _table_markers(lines)


def nest_pipe_tables(lines: list[str]) -> list[str]:
    """Rewrite contiguous pipe-table rows as nested `TABLE`/`TR`/`TH`|`TC` markup;
    other lines pass through. A blank line inside a run does not end it. A lone pipe
    row is prose unless a separator declares it a table, and the rendered markup keeps
    the run's indent so the table stays where the rows sat.
    """
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if not is_pipe_row(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        run: list[str] = []
        j = i
        while j < n:
            if is_pipe_row(lines[j]):
                run.append(lines[j].strip())
                j += 1
            elif not lines[j].strip() and j + 1 < n and is_pipe_row(lines[j + 1]):
                j += 1  # blank row separator inside the table
            else:
                break
        body_rows = [r for r in run if not _is_separator_row(r)]
        # A separator row declares a table, so a header carrying one is a table
        # even where the body continues on the next page.
        declared = len(body_rows) < len(run)
        if len(body_rows) < 2 and not (declared and body_rows):
            out.extend(lines[i:j])
        else:
            # The run's own indent, or the markup leaves the container the rows
            # sat in: rendered at column 0, a preface table reparses into body.
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            headed = not _is_separator_row(run[0])
            out.extend(indent + line for line in _render_table(body_rows, headed=headed))
        i = j
    return out


def _render_table(rows: list[str], *, headed: bool = True) -> list[str]:
    """`rows` is the run with separators dropped. `headed` makes the first row `TH`;
    a run that opened with a separator has no header row, so it is all `TC`. A blank
    leading cell continues the column's still-open cell above, bumping its
    `{rowspan N}` rather than emitting an empty cell of its own."""
    out = ["TABLE"]
    col_origin: dict[int, int] = {}  # column -> `out` index of its open TH/TC line
    for ri, row in enumerate(rows):
        cells = _split_cells(row)
        out.append(f"{_INDENT}TR")
        for ci, text in enumerate(cells):
            if not text and ci in col_origin:
                origin_idx = col_origin[ci]
                keyword_line, _, attrs = out[origin_idx].partition("{")
                existing = re.search(r"rowspan (\d+)", attrs)
                span = int(existing.group(1)) + 1 if existing else 2
                out[origin_idx] = f"{keyword_line}{{rowspan {span}}}"
                continue
            keyword = "TH" if (headed and ri == 0) else "TC"
            out.append(f"{_INDENT * 2}{keyword}")
            col_origin[ci] = len(out) - 1
            if text:
                out.append(f"{_INDENT * 3}{text}")
    return out


def literal_pipe_body(lines: list[str]) -> list[str]:
    """Escape source text for Bluebell while retaining pipe-table delimiters."""

    def literal(text: str) -> str:
        # Bluebell's escape production is a backslash followed by any non-newline.
        escaped = re.sub(r"([\\*/_{])", r"\\\1", text)
        return "\\" + escaped if escaped and not escaped.startswith("\\") else escaped

    return [
        line
        if _is_separator_row(line)
        else "|" + "|".join(literal(cell.strip()) for cell in _split_cells(line)) + "|"
        if is_pipe_row(line)
        else literal(line)
        for line in lines
    ]
