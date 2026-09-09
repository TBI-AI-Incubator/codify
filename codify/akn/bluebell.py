"""AKN-XML ⇄ Bluebell-text bridge."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import cast

from bluebell.akn import ParseError
from cobalt.uri import FrbrUri
from lxml import etree

from codify.pipeline.enrich.bluebell import (
    parse_to_akn,
    rejoin_citation_lines,
)

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Bluebell structural keywords mapped to AKN element names.
_BLUEBELL_TO_AKN: dict[str, str] = {
    "PART": "part",
    "TITLE": "title",
    "CHAPTER": "chapter",
    "SUBCHAPTER": "chapter",
    "SECTION": "section",
    "SUBSECTION": "subsection",
    "ARTICLE": "article",
    # The emitter writes Bluebell's own abbreviation for an article.
    "ART": "article",
    "SUBTITLE": "subtitle",
    "PARAGRAPH": "paragraph",
    "SUBPARAGRAPH": "subparagraph",
    "POINT": "point",
    "BOOK": "book",
    "TOME": "tome",
    "DIVISION": "division",
    "SUBDIVISION": "subdivision",
    "SUBPART": "subpart",
    "ALINEA": "alinea",
    "CLAUSE": "clause",
    "INDENT": "indent",
    "LEVEL": "level",
    "LIST": "list",
    "PROVISO": "proviso",
    "RULE": "rule",
    "SUBCLAUSE": "subclause",
    "SUBLIST": "sublist",
    "SUBRULE": "subrule",
    "TRANSITIONAL": "transitional",
}

_MARKER_RE = re.compile(
    r"^[ \t]*(" + "|".join(_BLUEBELL_TO_AKN) + r")\b",
    re.MULTILINE,
)


@dataclass(frozen=True)
class SourceAnchor:
    eid: str
    line: int  # 1-indexed
    kind: str  # AKN element name


@dataclass(frozen=True)
class BluebellParseError:
    line: int
    column: int
    message: str


# Structural AKN tags mapped to Bluebell headers.
_STRUCTURAL_TO_KEYWORD: dict[str, str] = {
    "book": "BOOK",
    "tome": "TOME",
    "part": "PART",
    "subpart": "SUBPART",
    "title": "TITLE",
    "subtitle": "SUBTITLE",
    "chapter": "CHAPTER",
    "subchapter": "SUBCHAPTER",
    "division": "DIVISION",
    "subdivision": "SUBDIVISION",
    "section": "SECTION",
    "subsection": "SUBSECTION",
    "article": "ART",
    "paragraph": "PARAGRAPH",
    "subparagraph": "SUBPARAGRAPH",
    "point": "POINT",
    "alinea": "ALINEA",
    "clause": "CLAUSE",
    "indent": "INDENT",
    "level": "LEVEL",
    "list": "LIST",
    "proviso": "PROVISO",
    "rule": "RULE",
    "subclause": "SUBCLAUSE",
    "sublist": "SUBLIST",
    "subrule": "SUBRULE",
    "transitional": "TRANSITIONAL",
}

# Walk through non-structural containers so schedules and recitals are retained.
_TRANSPARENT_CONTAINERS = frozenset({"content", "intro", "wrapUp", "hcontainer", "tblock", "block"})


def _local(el: etree._Element) -> str:
    """Local-name of an element, namespace-stripped."""
    if not isinstance(el.tag, str):
        return ""
    return cast(str, etree.QName(el).localname)


def _direct_child(el: etree._Element, name: str) -> etree._Element | None:
    for c in el:
        if _local(c) == name:
            return c
    return None


def _direct_child_text(el: etree._Element, name: str) -> str:
    c = _direct_child(el, name)
    if c is None:
        return ""
    return _inline_text(c)


def _inline_text(el: etree._Element) -> str:
    """Concatenate text content; inline elements (term, ref, …) flatten to
    their textContent. Newlines collapsed so a `<p>` stays a single line."""
    parts = "".join(el.itertext())
    return re.sub(r"\s+", " ", parts).strip()


def akn_xml_to_bluebell(akn_xml: str) -> str:
    """Render an AKN-3 XML string as Bluebell text, with indent derived strictly from
    element-tree depth rather than content positioning.

    The bundled cobalt XSL emits the next article at the indent of the previous article's
    last paragraph, and round-tripping that through `parse_to_akn` collapses every article
    whose predecessor had multi-line content: 95 articles to 37 in one case. Walking the
    tree and emitting each block at `depth * 2` spaces makes
    `parse_to_akn(akn_xml_to_bluebell(x))` reproduce x's structure exactly.
    """
    payload = akn_xml.encode("utf-8") if isinstance(akn_xml, str) else akn_xml
    doc = etree.fromstring(payload)
    doc_root = next(
        (c for c in doc if _local(c) in {"act", "bill", "doc", "judgment", "debate"}),
        None,
    )
    if doc_root is None:
        return ""

    out: list[str] = []
    preface = _direct_child(doc_root, "preface")
    preamble = _direct_child(doc_root, "preamble")
    body = _direct_child(doc_root, "body")
    if body is None:
        body = _direct_child(doc_root, "mainBody")

    if preface is not None:
        out.append("PREFACE")
        out.append("")
        _emit_frontmatter(preface, depth=1, out=out)

    if preamble is not None:
        out.append("PREAMBLE")
        out.append("")
        _emit_frontmatter(preamble, depth=1, out=out)

    if body is not None:
        out.append("BODY")
        out.append("")
        for child in body:
            cname = _local(child)
            if cname in _TRANSPARENT_CONTAINERS:
                _emit_text_run(child, depth=1, out=out)
            else:
                _emit_block(child, depth=0, out=out)

    attachments = _direct_child(doc_root, "attachments")
    if attachments is not None:
        for idx, att in enumerate(attachments, start=1):
            if _local(att) != "attachment":
                continue
            heading = _direct_child_text(att, "heading")
            out.append("")
            header = f"SCHEDULE {idx}"
            if heading:
                header = f"{header} - {heading}"
            out.append(header)
            out.append("")
            # Attachments wrap their structural tree in <doc><mainBody>.
            doc_el = _direct_child(att, "doc")
            inner_body = None
            if doc_el is not None:
                inner_body = _direct_child(doc_el, "mainBody")
                if inner_body is None:
                    inner_body = _direct_child(doc_el, "body")
            if inner_body is None:
                inner_body = _direct_child(att, "mainBody")
                if inner_body is None:
                    inner_body = _direct_child(att, "body")
            if inner_body is not None:
                for child in inner_body:
                    cname = _local(child)
                    if cname == "p":
                        text = _inline_text(child)
                        if text:
                            out.append(f"  {text}")
                            out.append("")
                    else:
                        _emit_block(child, depth=1, out=out)

    # Trim a trailing blank line, never two in a row at the end.
    while len(out) > 1 and out[-1] == "" and out[-2] == "":
        out.pop()
    return "\n".join(out) + ("\n" if out else "")


def _emit_frontmatter(container: etree._Element, *, depth: int, out: list[str]) -> None:
    """Walk `<preface>` / `<preamble>` children. Each `<p>` becomes one
    indented body line, a `<table>` its own block; other containers (longTitle,
    formula, recitals, recital) recurse so their inline content is preserved."""
    indent = "  " * depth
    for child in container:
        name = _local(child)
        if name == "p":
            text = _inline_text(child)
            if text:
                out.append(f"{indent}{text}")
                out.append("")
        elif name == "table":
            # Recursing here would emit each cell as a line and lose the table.
            _emit_table(child, depth=depth, out=out)
            out.append("")
        else:
            _emit_frontmatter(child, depth=depth, out=out)


def _emit_block(el: etree._Element, *, depth: int, out: list[str]) -> None:
    """Emit a structural element at `depth*2` indent. The header is `KEYWORD NUM - HEADING`
    with absent segments dropped, and body content and nested blocks follow one level
    deeper. A non-structural element at the top level, which is rare, flattens to a single
    inline body line.
    """
    name = _local(el)
    if name not in _STRUCTURAL_TO_KEYWORD:
        if name == "p":
            # Preserve stray inline content rather than dropping it.
            text = _inline_text(el)
            if text:
                out.append(f"{'  ' * depth}{text}")
                out.append("")
        return

    keyword = _STRUCTURAL_TO_KEYWORD[name]
    indent = "  " * depth
    num = _direct_child_text(el, "num")
    heading = _direct_child_text(el, "heading")
    header_parts = [keyword]
    if num:
        header_parts.append(num)
    line = indent + " ".join(header_parts)
    if heading:
        line = f"{line} - {heading}"
    out.append(line)
    out.append("")

    inner = depth + 1
    inner_indent = "  " * inner

    # Walk children in document order: <intro>/<content>/<wrapUp> contents
    # flatten into body lines; nested structural elements recurse.
    for child in el:
        cname = _local(child)
        if cname in ("num", "heading"):
            continue
        if cname in _TRANSPARENT_CONTAINERS:
            _emit_text_run(child, depth=inner, out=out)
        elif cname == "blockList":
            _emit_blocklist(child, depth=inner, out=out)
        elif cname == "table":
            _emit_table(child, depth=inner, out=out)
        elif cname in _STRUCTURAL_TO_KEYWORD:
            _emit_block(child, depth=inner, out=out)
        elif cname == "p":
            # Bare <p> directly under the block.
            text = _inline_text(child)
            if text:
                out.append(f"{inner_indent}{text}")
                out.append("")


def _emit_text_run(container: etree._Element, *, depth: int, out: list[str]) -> None:
    """Emit `<p>` children of an intro/content/wrapUp wrapper at `depth*2`
    indent. Nested `<blockList>` recurses; unknown elements flatten to text."""
    indent = "  " * depth
    for child in container:
        cname = _local(child)
        if cname in ("num", "heading"):
            continue
        if cname == "p":
            text = _inline_text(child)
            if text:
                out.append(f"{indent}{text}")
                out.append("")
        elif cname == "blockList":
            _emit_blocklist(child, depth=depth, out=out)
        elif cname == "table":
            _emit_table(child, depth=depth, out=out)
        elif cname in _TRANSPARENT_CONTAINERS:
            _emit_text_run(child, depth=depth, out=out)
        elif cname in _STRUCTURAL_TO_KEYWORD:
            _emit_block(child, depth=depth, out=out)
        else:
            text = _inline_text(child)
            if text:
                out.append(f"{indent}{text}")
                out.append("")


def _emit_table(el: etree._Element, *, depth: int, out: list[str]) -> None:
    """Render `<table>` back to `TABLE`/`TR`/`TH`|`TC` blocks. `colspan` and
    `rowspan` fold into the pipe-joined `{attr value|attr value}` syntax the
    grammar reads on input (`table_cell` in akn.peg); a cell's `<p>` children
    join onto its one body line, matching how the factory authors tables."""
    indent = "  " * depth
    out.append(f"{indent}TABLE")
    row_indent = "  " * (depth + 1)
    cell_indent = "  " * (depth + 2)
    body_indent = "  " * (depth + 3)
    for row in el:
        if _local(row) != "tr":
            continue
        out.append(f"{row_indent}TR")
        for cell in row:
            cname = _local(cell)
            if cname not in ("th", "td"):
                continue
            keyword = "TH" if cname == "th" else "TC"
            attrs = [
                f"{name} {cell.get(name)}"
                for name in ("colspan", "rowspan")
                if cell.get(name) not in (None, "1")
            ]
            suffix = "{" + "|".join(attrs) + "}" if attrs else ""
            out.append(f"{cell_indent}{keyword}{suffix}")
            text = " ".join(_inline_text(p) for p in cell if _local(p) == "p")
            if text:
                out.append(f"{body_indent}{text}")


def _emit_blocklist(el: etree._Element, *, depth: int, out: list[str]) -> None:
    """Render `<blockList>` as `ITEMS` / `ITEM <num>` / body block."""
    indent = "  " * depth
    intro = _direct_child(el, "listIntroduction")
    if intro is not None:
        text = _inline_text(intro)
        if text:
            out.append(f"{indent}{text}")
            out.append("")
    out.append(f"{indent}ITEMS")
    item_indent = "  " * (depth + 1)
    body_indent = "  " * (depth + 2)
    for item in el:
        if _local(item) != "item":
            continue
        num = _direct_child_text(item, "num")
        out.append(f"{item_indent}ITEM {num}" if num else f"{item_indent}ITEM")
        for child in item:
            cname = _local(child)
            if cname in ("num",):
                continue
            if cname == "p":
                text = _inline_text(child)
                if text:
                    out.append(f"{body_indent}{text}")
            elif cname == "blockList":
                _emit_blocklist(child, depth=depth + 2, out=out)
    wrap = _direct_child(el, "listWrapUp")
    if wrap is not None:
        text = _inline_text(wrap)
        if text:
            out.append(f"{indent}{text}")
            out.append("")


def bluebell_to_akn(
    text: str,
    *,
    country: str = "xx",
    doctype: str = "act",
    date: str = "2024-01-01",
    number: str = "1",
    language: str = "eng",
    subtype: str | None = None,
) -> tuple[str, list[SourceAnchor], list[BluebellParseError]]:
    """Parse Bluebell text, returning (akn_xml, source_map, errors). On success the source map
    carries one anchor per structural marker in input order and errors is empty; on failure
    akn_xml is empty and errors carries one entry with line, column and message.
    """
    # The source map zips source markers against AKN elements, so both sides must see
    # the same string. `parse_to_akn` rejoins a citation the body-fill broke across a
    # line, removing a marker, so walking the original text against the parsed AKN
    # would bind every later marker to the wrong eId. The rejoin is idempotent.
    prepared, _, origin = rejoin_citation_lines(text, country)
    try:
        akn_xml = parse_to_akn(
            prepared,
            country=country,
            doctype=doctype,
            date=date,
            number=number,
            language=language,
            subtype=subtype,
        )
    except ParseError as exc:
        return "", [], [_parse_error_from_message(str(exc))]
    except Exception as exc:  # noqa: BLE001, surface anything else to the user
        return "", [], [BluebellParseError(line=1, column=1, message=str(exc))]

    source_map = _build_source_map(prepared, akn_xml)
    # Positions go back to the caller's coordinates: the reader is looking at
    # the text they gave us, not the one we parsed.
    if origin:
        source_map = [
            replace(a, line=origin[a.line - 1] + 1) if 0 < a.line <= len(origin) else a
            for a in source_map
        ]
    return akn_xml, source_map, []


# Numbered paragraph, point and subparagraph markers below keyword level.
_NUMBERED_RE = re.compile(r"^[ \t]+\(([0-9a-zA-Z]+)\)", re.MULTILINE)

# A table cell keyword line ("TH", "TC", or either with a `{...}` span suffix);
# the next non-blank line is that cell's content, one anchor point per cell —
# the same granularity numbered markers give ordinary paragraphs.
_TABLE_RE = re.compile(r"^[ \t]*TABLE\b", re.MULTILINE)
_TABLE_CELL_KEYWORD_RE = re.compile(r"^[ \t]*T[HC](\{[^}]*\})?[ \t]*$")
_TABLE_ROW_KEYWORD_RE = re.compile(r"^[ \t]*TR(\{[^}]*\})?[ \t]*$")


def _table_markers(text: str) -> tuple[list[int], list[tuple[int, int]]]:
    """Return table marker lines as (cell keyword lines, row-start markers).

    Row-start markers are tuples of:
    - source line of the `TR` keyword, and
    - 0-based index of the row's first cell within the document's flat cell stream.
    """
    lines = text.split("\n")
    cell_keyword_lines: list[int] = []
    row_markers: list[tuple[int, int]] = []
    open_row_line: int | None = None
    row_has_first_cell = False

    for i, line in enumerate(lines):
        line_no = i + 1
        if _TABLE_ROW_KEYWORD_RE.match(line):
            open_row_line = line_no
            row_has_first_cell = False
            continue
        if _TABLE_CELL_KEYWORD_RE.match(line):
            cell_idx = len(cell_keyword_lines)
            cell_keyword_lines.append(line_no)
            if open_row_line is not None and not row_has_first_cell:
                row_markers.append((open_row_line, cell_idx))
                row_has_first_cell = True

    return cell_keyword_lines, row_markers


def _table_cell_content_lines(text: str, cell_keyword_lines: list[int]) -> set[int]:
    """Line numbers of a table cell's content (the line right after its `TH`/`TC`
    keyword line) — excluded from `numbered_lines`, or a cell body opening with
    something that looks like "(1)" masquerades as a real enumerated marker and
    steals a slot from the next real paragraph in the zip."""
    lines = text.split("\n")
    out: set[int] = set()
    for kw_line in cell_keyword_lines:
        if kw_line < len(lines) and lines[kw_line].strip():
            out.add(kw_line + 1)
    return out


def _build_source_map(text: str, akn_xml: str) -> list[SourceAnchor]:
    """Parallel walk of source markers and AKN elements. Captures numbered-marker
    lines and structural keywords, plus one anchor per table and per cell, so
    linked scroll has reference points at paragraph/cell granularity rather
    than lerping a whole table as if it were uniform-density prose.
    """
    # Keyword markers preserve their element kind for matching.
    keyword_markers: list[tuple[str, int]] = []
    for match in _MARKER_RE.finditer(text):
        kind = _BLUEBELL_TO_AKN[match.group(1).upper()]
        line = text.count("\n", 0, match.start()) + 1
        keyword_markers.append((kind, line))

    table_lines: list[int] = [text.count("\n", 0, m.start()) + 1 for m in _TABLE_RE.finditer(text)]
    cell_lines, row_markers = _table_markers(text)
    table_cell_content_lines = _table_cell_content_lines(text, cell_lines)

    # A cell's own content line is excluded: it can look like a real "(1)" marker.
    numbered_lines: list[int] = [
        line
        for m in _NUMBERED_RE.finditer(text)
        if (line := text.count("\n", 0, m.start()) + 1) not in table_cell_content_lines
    ]

    root = etree.fromstring(akn_xml.encode("utf-8"))
    eids_by_kind: dict[str, list[str]] = {}
    flat_para_eids: list[str] = []
    table_eids: list[str] = []
    # One slot per cell (even an eid-less one), aligned with `cell_lines`.
    table_cell_p_eids: list[str | None] = []
    for tbl in root.iter():
        if isinstance(tbl.tag, str) and etree.QName(tbl).localname == "table":
            eid = tbl.get("eId")
            if eid:
                table_eids.append(eid)
            for cell in tbl.iter():
                if isinstance(cell.tag, str) and etree.QName(cell).localname in ("th", "td"):
                    cell_eid: str | None = None
                    for p in cell:
                        if isinstance(p.tag, str) and etree.QName(p).localname == "p":
                            cell_eid = p.get("eId")
                            break
                    table_cell_p_eids.append(cell_eid)
    table_p_eids = {e for e in table_cell_p_eids if e}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = etree.QName(el).localname
        eid = el.get("eId")
        if eid and tag in set(_BLUEBELL_TO_AKN.values()):
            eids_by_kind.setdefault(tag, []).append(eid)
        # Table cell paragraphs pair against cell_lines below, not numbered_lines.
        if (
            eid
            and tag in {"paragraph", "subparagraph", "point", "item", "p"}
            and eid not in table_p_eids
        ):
            flat_para_eids.append(eid)

    anchors: list[SourceAnchor] = []
    seen: set[tuple[str, int]] = set()

    def _add(eid: str | None, line: int, kind: str) -> None:
        if not eid or (eid, line) in seen:
            return
        seen.add((eid, line))
        anchors.append(SourceAnchor(eid=eid, line=line, kind=kind))

    cursor: dict[str, int] = {}
    for kind, line in keyword_markers:
        idx = cursor.get(kind, 0)
        eids = eids_by_kind.get(kind, [])
        if idx >= len(eids):
            continue
        _add(eids[idx], line, kind)
        cursor[kind] = idx + 1

    for line, eid in zip(table_lines, table_eids, strict=False):
        _add(eid, line, "table")

    # Row keyword lines don't map to dedicated AKN row eIds (Bluebell only emits
    # cell-level eIds under <table>), so anchor each TR to the row's first cell.
    # This prevents row-heavy tables from stretching interpolation between cells.
    for row_line, first_cell_idx in row_markers:
        if first_cell_idx >= len(table_cell_p_eids):
            continue
        _add(table_cell_p_eids[first_cell_idx], row_line, "p")

    for line, eid in zip(cell_lines, table_cell_p_eids, strict=False):
        _add(eid, line, "p")

    # Pair numbered-marker lines with paragraph-like AKN elements in
    # document order. May over- or under-shoot at the tails; the lerp
    # falls back to the bracketing keyword anchor.
    for line, eid in zip(numbered_lines, flat_para_eids, strict=False):
        _add(eid, line, "paragraph")

    anchors.sort(key=lambda a: a.line)
    return anchors


_LINE_RE = re.compile(r"Line\s+(\d+)")


def _parse_error_from_message(message: str) -> BluebellParseError:
    """Extract line number from bluebell's format_error output."""
    m = _LINE_RE.search(message)
    line = int(m.group(1)) if m else 1
    return BluebellParseError(line=line, column=1, message=message)


# Re-export for callers that just want a FrbrUri builder.
__all__ = [
    "AKN_NS",
    "BluebellParseError",
    "FrbrUri",
    "SourceAnchor",
    "akn_xml_to_bluebell",
    "bluebell_to_akn",
]
