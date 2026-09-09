"""Read-side projection of the AKN body for a single version.

Assembles a typed JSON tree from the persisted `Section` + `Provision` rows.
Section structure comes from the row split done at ingest time; per-provision
inline markup + nested subparagraph/point/item structure is recovered by a
one-pass parse of `Version.akn_xml` and looked up by `eId`.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date
from typing import Literal, Union

from lxml import etree
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codify.akn._schema import safe_parser
from codify.akn.vocabulary import TAG_TO_KIND, is_lifted_note, table_row_eid
from codify.storage.models import Jurisdiction, Law, Provision, Section, Version

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# AKN inline element names we map to typed nodes; anything else
# passes through as its children's content.
_NESTED_BLOCK_TAGS = frozenset(
    {"paragraph", "subparagraph", "point", "item", "subpoint", "subitem"}
)

# Inside a quote the walker is the only reader: a quoted structure is not split
# into `Section` rows, so its containers have nowhere else to be found.
_QUOTED_BLOCK_TAGS = _NESTED_BLOCK_TAGS | frozenset(TAG_TO_KIND)

# What `absorb` has a branch for. Anything else is dropped, which is right for a
# document whose text is elsewhere and wrong inside a quote.
_MODELLED_TAGS = frozenset(
    {
        "num",
        "heading",
        "intro",
        "content",
        "wrapUp",
        "p",
        "table",
        "blockList",
        "listIntroduction",
        "mod",
    }
)


class InlineText(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class InlineRef(BaseModel):
    kind: Literal["ref"] = "ref"
    text: str
    href: str | None = None


class InlineTerm(BaseModel):
    kind: Literal["term"] = "term"
    text: str


class InlineCitation(BaseModel):
    kind: Literal["citation"] = "citation"
    text: str
    uri: str
    href: str | None = None


class InlineEmph(BaseModel):
    kind: Literal["emph"] = "emph"
    children: list["InlineNode"] = Field(default_factory=list)


class InlineStrong(BaseModel):
    kind: Literal["strong"] = "strong"
    children: list["InlineNode"] = Field(default_factory=list)


# Its own node rather than emphasis: amended text is marked by a pair of
# markers, and which pair is the reader's decision, not this projection's.
class InlineMod(BaseModel):
    """An amending instruction's quoted words."""

    kind: Literal["mod"] = "mod"
    children: list["InlineNode"] = Field(default_factory=list)


class InlineNote(BaseModel):
    """A footnote bound to the provision it annotates, not part of its prose."""

    kind: Literal["note"] = "note"
    children: list["InlineNode"] = Field(default_factory=list)
    inferred: bool = False


InlineNode = Union[
    InlineText,
    InlineRef,
    InlineTerm,
    InlineCitation,
    InlineEmph,
    InlineStrong,
    InlineMod,
    InlineNote,
]

InlineMod.model_rebuild()
InlineEmph.model_rebuild()
InlineStrong.model_rebuild()
InlineNote.model_rebuild()


class DocumentTableCell(BaseModel):
    """One `<th>`/`<td>`; `content` is the same inline AST as any other cell of
    legal text, so refs/terms/citations inside a table cell still render."""

    content: list[InlineNode] = Field(default_factory=list)
    akn_eid: str | None = None
    header: bool = False
    colspan: int = 1
    rowspan: int = 1


class DocumentTableRow(BaseModel):
    # The `<tr>` eId, which is what a row search cites and what the reader
    # anchors on. Minted from the table's eId and the row's position where the
    # source carries none, the same way the row resolver mints it.
    akn_eid: str | None = None
    # Explicit title: the generic "Cells" would collide with an unrelated
    # array alias elsewhere in the generated TS.
    cells: list[DocumentTableCell] = Field(default_factory=list, title="DocumentTableCells")


class DocumentTable(BaseModel):
    """An AKN `<table>`, eId-keyed like every other citable unit."""

    akn_eid: str
    # Explicit title: the generic "Rows" collides with ConcordanceResponse.rows.
    rows: list[DocumentTableRow] = Field(default_factory=list, title="DocumentTableRows")


class DocumentProvisionBlock(BaseModel):
    """One row in a nested-provision tree (paragraph / subparagraph / …).

    Carries its own number, optional heading, intro inline run, nested
    blocks, and trailing wrap-up so renderers can present a hanging
    numbered hierarchy rather than a flattened paragraph. `table` is set
    (and everything else empty) when `akn_type == "table"`.
    """

    akn_eid: str
    akn_type: str
    num: str | None = None
    heading: str | None = None
    intro: list[InlineNode] = Field(default_factory=list)
    blocks: list["DocumentProvisionBlock"] = Field(default_factory=list)
    wrap_up: list[InlineNode] = Field(default_factory=list)
    table: DocumentTable | None = None


DocumentProvisionBlock.model_rebuild()


class DocumentProvision(BaseModel):
    """Leaf-level legal text, paragraph / subparagraph / point.

    `text` remains the plain-text shadow for search / fallback rendering;
    `blocks` is the rich AST built from the AKN sub-tree.
    """

    id: uuid.UUID
    akn_eid: str
    akn_type: str
    text: str
    position: int
    # False for attachment content: official interpretation, not a legal basis.
    normative: bool = True
    num: str | None = None
    blocks: list[DocumentProvisionBlock] = Field(default_factory=list)


class DocumentSection(BaseModel):
    """Hierarchical container, title / chapter / part / article / etc.

    Holds child sections and provisions in their persisted `position` order.
    Top-level provisions (those without an enclosing section) hang off the
    `VersionDocument.provisions` list instead.
    """

    id: uuid.UUID
    akn_eid: str
    akn_type: str
    title: str | None
    position: int
    num: str | None = None
    sections: list["DocumentSection"] = Field(default_factory=list)
    provisions: list[DocumentProvision] = Field(default_factory=list)


class VersionDocument(BaseModel):
    """Full body of a single version, rendered as a tree."""

    version_id: uuid.UUID
    law_id: uuid.UUID
    # The pair this jurisdiction prints around amended words. Carried on the
    # document because every reader already fetches it, and a prop threaded to
    # each of them is a prop one of them forgets.
    amendment_markers: list[str] = Field(
        default_factory=lambda: ["[", "]"], min_length=2, max_length=2
    )
    frbr_work_uri: str
    frbr_expression_uri: str
    language: str
    expression_date: date
    # Frontmatter, walked off the stored AKN root. Flat inline run with
    # "\n\n" between paragraphs; renderer applies `white-space: pre-line`.
    preface: list[InlineNode] = Field(default_factory=list)
    preamble: list[InlineNode] = Field(default_factory=list)
    # Back matter, read off the root like the front matter: the attestation is
    # not a provision, so it has no row to be reached through.
    conclusions: list[InlineNode] = Field(default_factory=list)
    sections: list[DocumentSection] = Field(default_factory=list)
    provisions: list[DocumentProvision] = Field(default_factory=list)


# AKN walker.


def _local_name(el: etree._Element) -> str:
    tag = el.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag if isinstance(tag, str) else ""


def _direct_child(el: etree._Element, name: str) -> etree._Element | None:
    for child in el:
        if _local_name(child) == name:
            return child
    return None


def _direct_child_text(el: etree._Element, name: str) -> str | None:
    child = _direct_child(el, name)
    if child is None:
        return None
    text = "".join(child.itertext()).strip()
    return text or None


def _walk_inline(el: etree._Element) -> list[InlineNode]:
    out: list[InlineNode] = []
    if el.text:
        out.append(InlineText(text=el.text))
    for child in el:
        _push_inline(child, out)
        if child.tail:
            out.append(InlineText(text=child.tail))
    return _collapse_text(out)


# An href in an ingested document is publisher-supplied, so it is untrusted in
# the same way its text is. Anything outside this set is dropped rather than
# carried into the reading surface, where it would render as a live link.
_SAFE_HREF = re.compile(r"^(https?:|mailto:|/|#|\./|\.\./)", re.I)


def _safe_href(href: str | None) -> str | None:
    if href is None:
        return None
    return href if _SAFE_HREF.match(href.strip()) else None


def _paragraph_segments(
    el: etree._Element,
) -> list[tuple[list[InlineNode], tuple[etree._Element, etree._Element] | None]]:
    """A paragraph split around any quoted hierarchy an inline `<mod>` carries:
    the run up to it and the mod and structure ending it, then a final run with
    none. The instruction lives inside the mod, so the split reaches into it."""
    segments: list[tuple[list[InlineNode], tuple[etree._Element, etree._Element] | None]] = []
    run: list[InlineNode] = []

    def close(mod: etree._Element, quoted: etree._Element) -> None:
        nonlocal run
        segments.append((_collapse_text(run), (mod, quoted)))
        run = []

    if el.text:
        run.append(InlineText(text=el.text))
    for child in el:
        structural = _structural_quote(child) if _local_name(child) == "mod" else None
        if structural is not None:
            if child.text:
                run.append(InlineText(text=child.text))
            for inner in child:
                # Identity, not tag: quoted text beside a structure is still
                # words, and marking it as the structure would promote it.
                if inner is structural:
                    close(child, inner)
                elif _local_name(inner) in _QUOTED_CONTENT_TAGS:
                    # Marked here as `_push_mod` would: the split bypasses it,
                    # and these words were amended just as much as the structure.
                    words = _normalised(_walk_inline(inner))
                    if words:
                        run.append(InlineMod(children=words))
                else:
                    _push_inline(inner, run)
                if inner.tail:
                    run.append(InlineText(text=inner.tail))
        else:
            _push_inline(child, run)
        if child.tail:
            run.append(InlineText(text=child.tail))
    segments.append((_collapse_text(run), None))
    return segments


def _structural_quote(mod: etree._Element) -> etree._Element | None:
    """The quoted hierarchy this mod carries, if any. Quoted *text* is never one,
    whatever its shape, and a structure of a single bare paragraph is a phrase."""
    quoted = _quoted_structure(mod)
    return quoted if quoted is not None and not _is_phrase(quoted) else None


def _push_mod(el: etree._Element, out: list[InlineNode]) -> None:
    """An inline `<mod>` is the whole instruction, of which only the quoted
    words were amended: "in paragraph (f), after "(r)" insert <quotedText>…".
    Marking all of it would bracket the instruction rather than the amendment."""
    if el.text:
        out.append(InlineText(text=el.text))
    for child in el:
        if _local_name(child) in _QUOTED_CONTENT_TAGS:
            words = _normalised(_walk_inline(child))
            if words:
                out.append(InlineMod(children=words))
        else:
            _push_inline(child, out)
        if child.tail:
            out.append(InlineText(text=child.tail))


def _push_inline(el: etree._Element, out: list[InlineNode]) -> None:
    name = _local_name(el)
    text = "".join(el.itertext()).strip()
    if name == "ref":
        out.append(InlineRef(text=text, href=_safe_href(el.get("href"))))
        return
    if name == "rref":
        href = el.get("href")
        # `uri` is display text, not a link target, so it keeps the declared
        # value; only the clickable half is filtered.
        out.append(InlineCitation(text=text, uri=href or text, href=_safe_href(href)))
        return
    if name in ("term", "def"):
        out.append(InlineTerm(text=text))
        return
    if name == "mod":
        _push_mod(el, out)
        return
    if name in ("i", "em"):
        out.append(InlineEmph(children=_walk_inline(el)))
        return
    if name in ("b", "strong"):
        out.append(InlineStrong(children=_walk_inline(el)))
        return
    if is_lifted_note(el):
        # Its own node, or the reader would read the footnote as a clause of
        # the sentence it hangs off. An inline note has no placement and falls
        # through to the flatten below, keeping it inside its sentence.
        children = [n for child in el for n in _walk_inline(child)]
        out.append(
            InlineNote(children=children, inferred=el.get("refersTo") == "#inferred-binding")
        )
        return
    # Unknown element, flatten its inline content.
    if el.text:
        out.append(InlineText(text=el.text))
    for child in el:
        _push_inline(child, out)
        if child.tail:
            out.append(InlineText(text=child.tail))


def _collapse_text(nodes: list[InlineNode]) -> list[InlineNode]:
    out: list[InlineNode] = []
    for n in nodes:
        if isinstance(n, InlineText) and out and isinstance(out[-1], InlineText):
            out[-1] = InlineText(text=out[-1].text + n.text)
        else:
            out.append(n)
    return out


def _span_int(value: str | None) -> int:
    """`colspan`/`rowspan` value, defaulting to 1 for absent or malformed
    attributes rather than raising and failing the whole ingest."""
    try:
        return int(value) if value else 1
    except ValueError:
        return 1


def _walk_table(el: etree._Element) -> DocumentTable:
    """`<table><tr><th|td colspan= rowspan=><p>...</p></th|td></tr></table>` to
    `DocumentTable`. A cell's `<p>` children join on the same "\\n\\n" inline
    rule as everywhere else, so multi-paragraph cells still read as paragraphs."""
    eid = el.get("eId")
    if not eid:
        raise ValueError("AKN <table> has no eId; refusing to project an uncitable table")
    rows: list[DocumentTableRow] = []
    # Position over every `<tr>`, header included, because the row resolver
    # numbers the same way and a row citation has to land on the same id.
    position = 0
    for tr in el:
        if _local_name(tr) != "tr":
            continue
        position += 1
        cells: list[DocumentTableCell] = []
        for cell in tr:
            cname = _local_name(cell)
            if cname not in ("th", "td"):
                continue
            content: list[InlineNode] = []
            cell_anchor_eid: str | None = None
            for p in cell:
                if _local_name(p) != "p":
                    continue
                if cell_anchor_eid is None:
                    cell_anchor_eid = p.get("eId")
                if content:
                    content.append(InlineText(text="\n\n"))
                content.extend(_walk_inline(p))
            cells.append(
                DocumentTableCell(
                    content=content,
                    akn_eid=cell_anchor_eid,
                    header=cname == "th",
                    colspan=_span_int(cell.get("colspan")),
                    rowspan=_span_int(cell.get("rowspan")),
                )
            )
        # Minted where the source has none: search cites the minted id, so
        # the reader must render the same one.
        rows.append(
            DocumentTableRow(akn_eid=tr.get("eId") or table_row_eid(eid, position), cells=cells)
        )
    return DocumentTable(akn_eid=eid, rows=rows)


def _normalised(nodes: list[InlineNode], *, top: bool = True) -> list[InlineNode]:
    """Squeeze the source's own line breaks out of a quoted run, keeping the
    spaces between words. A pretty-printed `<quotedStructure>` indents its
    children, and the reader renders newlines."""
    out: list[InlineNode] = []
    for node in nodes:
        children = getattr(node, "children", None)
        if children is not None:
            out.append(node.model_copy(update={"children": _normalised(children, top=False)}))
            continue
        if not isinstance(node, InlineText):
            out.append(node)
            continue
        # Collapsed, not stripped: the space between two words can be the whole
        # of a text node sitting between two marked-up ones.
        lead = " " if node.text[:1].isspace() else ""
        trail = " " if node.text[-1:].isspace() else ""
        body = " ".join(node.text.split())
        text = f"{lead}{body}{trail}" if body else (lead or trail)
        if text:
            out.append(InlineText(text=text))
    if top and out:
        first = out[0]
        if isinstance(first, InlineText):
            out[0] = InlineText(text=first.text.lstrip())
        if isinstance(out[-1], InlineText):
            out[-1] = InlineText(text=out[-1].text.rstrip())
        out = [n for n in out if not isinstance(n, InlineText) or n.text]
    return out


def _space_before_quote(run: list[InlineNode]) -> None:
    """Restore the space a block boundary swallowed. Appended to the last text
    node where there is one, so the sentence stays one node; a run ending in
    markup gets a node of its own, or the words touch."""
    last = run[-1] if run else None
    if last is None:
        return
    if isinstance(last, InlineText):
        if last.text and not last.text[-1].isspace():
            run[-1] = InlineText(text=last.text + " ")
        return
    run.append(InlineText(text=" "))


_QUOTED_CONTENT_TAGS = frozenset({"quotedText", "quotedStructure", "embeddedStructure"})


def _quoted_structure(mod: etree._Element) -> etree._Element | None:
    for child in mod:
        if _local_name(child) in ("quotedStructure", "embeddedStructure"):
            return child
    return None


def _is_phrase(quoted: etree._Element) -> bool:
    """One bare `<p>` and nothing else: a phrase, not a structure. A `<num>` or
    `<heading>` beside it is structure, and inlining it would lose them."""
    children = [c for c in quoted if isinstance(c.tag, str)]
    return len(children) == 1 and _local_name(children[0]) == "p"


def _walk_block(
    el: etree._Element,
    stop_at_eids: frozenset[str] = frozenset(),
    nested_tags: frozenset[str] = _NESTED_BLOCK_TAGS,
    keep_unknown: bool = False,
) -> DocumentProvisionBlock:
    """Build a nested provision block.

    Each `<p>...<blockList>?` pair under `<content>` becomes a "group". A
    new `<p>` after items closes the current group and opens the next.
    A sole group folds into the outer block; multi-group articles emit
    each group as a synthetic nested block so items render at a uniform
    depth across the article.

    `stop_at_eids` marks eIds that are separately persisted as their own
    `Provision` rows. Nested-block recursion halts at those to avoid the
    SPA rendering the same content twice (once inside the container's
    blocks, once at the section-provisions level).
    """
    intro: list[InlineNode] = []
    blocks: list[DocumentProvisionBlock] = []
    wrap_up: list[InlineNode] = []

    pending_intro: list[InlineNode] = []
    pending_children: list[DocumentProvisionBlock] = []
    pending_wrap_up: list[InlineNode] = []
    pending_active = False
    multi_group = False
    # Set only while the pending group is exactly one bare <p>, so a synthetic
    # block that turns out to be just that keeps its real eid (client parity).
    pending_eid: str | None = None
    pending_p_count = 0
    # The last thing written to the pending run was a quoted phrase, so the
    # text after it finishes that sentence rather than starting one.
    after_phrase = False

    def push(target: list[InlineNode], run: list[InlineNode]) -> None:
        if not run:
            return
        if target:
            target.append(InlineText(text="\n\n"))
        target.extend(run)

    def inline_of(parent: etree._Element) -> list[InlineNode]:
        first_p = _direct_child(parent, "p")
        return _walk_inline(first_p if first_p is not None else parent)

    def flush_pending() -> None:
        nonlocal pending_intro, pending_children, pending_wrap_up, pending_active
        nonlocal pending_eid, pending_p_count
        if not pending_active:
            return
        if multi_group:
            single_p = pending_p_count == 1 and not pending_children and not pending_wrap_up
            blocks.append(
                DocumentProvisionBlock(
                    akn_eid=pending_eid if single_p and pending_eid else "",
                    akn_type="p",
                    num=None,
                    heading=None,
                    intro=pending_intro,
                    blocks=pending_children,
                    wrap_up=pending_wrap_up,
                )
            )
        else:
            intro.extend(pending_intro)
            blocks.extend(pending_children)
            wrap_up.extend(pending_wrap_up)
        pending_intro = []
        pending_children = []
        pending_wrap_up = []
        pending_active = False
        pending_eid = None
        pending_p_count = 0

    def open_new_group() -> None:
        nonlocal pending_active, multi_group
        if pending_active:
            multi_group = True
            flush_pending()
        pending_active = True

    def absorb(parent: etree._Element) -> None:
        nonlocal pending_active, multi_group, pending_eid, pending_p_count, after_phrase
        for child in parent:
            cname = _local_name(child)
            if cname in ("num", "heading"):
                continue
            follows_phrase = after_phrase and cname == "p"
            after_phrase = False
            if cname == "intro":
                push(intro, inline_of(child))
            elif cname == "content":
                absorb(child)
            elif cname == "wrapUp":
                push(wrap_up, inline_of(child))
            elif cname == "p":
                segments = _paragraph_segments(child)
                fresh = not pending_active or pending_children
                if fresh:
                    open_new_group()
                    pending_eid = child.get("eId")
                    pending_p_count = 1
                else:
                    pending_eid = None
                    pending_p_count += 1
                if len(segments) > 1:
                    # The paragraph carries a quoted hierarchy, so it becomes
                    # the text before it, the quote, and the text after.
                    pending_eid = None
                for index, (run, quote) in enumerate(segments):
                    if run:
                        # Only the first segment is a new paragraph; the rest
                        # continue the sentence a quote interrupted.
                        if follows_phrase or index > 0:
                            pending_intro.extend(run)
                        else:
                            push(pending_intro, run)
                    if quote is None:
                        continue
                    mod_el, quoted_el = quote
                    flush_pending()
                    blocks.append(_quote_block(mod_el, quoted_el))
                    multi_group = True
                    # Only if words follow it: a quote closing the paragraph
                    # would otherwise leave an empty row under the reader.
                    if any(later_run for later_run, _ in segments[index + 1 :]):
                        open_new_group()
            elif cname == "mod":
                quoted = _quoted_structure(child)
                if quoted is None:
                    # `<quotedText>`, or bare text. Split by the same rule as an
                    # inline mod, so the instruction stays prose either way.
                    nodes: list[InlineNode] = []
                    _push_mod(child, nodes)
                    phrase = _normalised(nodes)
                    if phrase:
                        if not pending_active:
                            open_new_group()
                            pending_p_count = 1
                        pending_eid = None
                        _space_before_quote(pending_intro)
                        pending_intro.extend(phrase)
                        after_phrase = True
                    continue
                if _is_phrase(quoted):
                    # Stays in the sentence: the words introducing an insertion
                    # and the words inserted are one clause of one provision.
                    if not pending_active:
                        open_new_group()
                        pending_p_count = 1
                    pending_eid = None
                    _space_before_quote(pending_intro)
                    pending_intro.append(InlineMod(children=_normalised(_walk_inline(quoted))))
                    after_phrase = True
                    continue
                flush_pending()
                blocks.append(_quote_block(child, quoted))
                # A boundary like a table: prose after the quote must not fold
                # back into the intro, which renders before `blocks`.
                multi_group = True
            elif cname == "table":
                flush_pending()
                table = _walk_table(child)
                blocks.append(
                    DocumentProvisionBlock(
                        akn_eid=table.akn_eid,
                        akn_type="table",
                        table=table,
                    )
                )
                # A table is a group boundary: prose after it must not fold
                # back into the outer intro (which renders before `blocks`).
                multi_group = True
            elif cname == "blockList":
                if pending_active:
                    pending_eid = None  # merging with prior content
                else:
                    pending_active = True
                for sub in child:
                    sname = _local_name(sub)
                    if sname == "listIntroduction":
                        push(pending_intro, inline_of(sub))
                    elif sname == "item":
                        pending_children.append(_walk_block(sub))
                    elif sname == "listWrapUp":
                        push(pending_wrap_up, inline_of(sub))
            elif cname == "listIntroduction":
                if pending_active:
                    pending_eid = None
                else:
                    pending_active = True
                push(pending_intro, inline_of(child))
            elif keep_unknown and cname not in _MODELLED_TAGS and cname not in nested_tags:
                # Inside a quote nothing else carries these words, so an element
                # this walker does not model keeps its text, in its own place.
                text = " ".join("".join(child.itertext()).split())
                if text:
                    if not pending_active:
                        open_new_group()
                        pending_p_count = 1
                    _space_before_quote(pending_intro)
                    pending_intro.append(InlineText(text=text))
            elif cname in nested_tags:
                flush_pending()
                child_eid = child.get("eId")
                if child_eid and child_eid in stop_at_eids:
                    # Separately persisted; the SPA renders it as its own
                    # section-level DocumentProvision, so leaving it here
                    # would double-render.
                    continue
                blocks.append(_walk_block(child, stop_at_eids, nested_tags, keep_unknown))
                # A boundary like a table: prose after a nested provision must
                # not fold into the intro, which renders before every block.
                multi_group = True

    absorb(el)
    flush_pending()

    return DocumentProvisionBlock(
        akn_eid=el.get("eId") or "",
        akn_type=_local_name(el),
        num=_direct_child_text(el, "num"),
        heading=_direct_child_text(el, "heading"),
        intro=intro,
        blocks=blocks,
        wrap_up=wrap_up,
    )


def _quote_block(mod: etree._Element, quoted: etree._Element) -> DocumentProvisionBlock:
    """A quoted structure carrying its own hierarchy, as a nested block. Keyed
    on the `<mod>`: that is the element with an eId a citation can name, and
    the structure inside it is numbered positionally and is not citable."""
    block = _walk_block(quoted, nested_tags=_QUOTED_BLOCK_TAGS, keep_unknown=True)
    intro = block.intro
    if not (intro or block.blocks or block.wrap_up or block.num or block.heading):
        # Never an empty bordered strip: whatever the walker could not model,
        # the reader still has to show, because nothing else carries these words.
        text = " ".join("".join(quoted.itertext()).split())
        intro = [InlineText(text=text)] if text else []
    return block.model_copy(
        update={
            "akn_eid": mod.get("eId") or block.akn_eid,
            "akn_type": "quote",
            "intro": intro,
        }
    )


def _walk_text_block(el: etree._Element) -> list[InlineNode]:
    """Flatten a `<preface>` / `<preamble>` container: each `<p>` is a
    paragraph, non-`<p>` containers recurse. Joined with `\\n\\n`.

    The translation-notes anchor block (a `<block name="translation-notes">`
    child of `<preface>`) carries the binding glossary for reviewer use; it
    is metadata about the translation, not editorial preface text, so the
    reader skips it. The AKN artifact retains it for downstream tooling.
    """
    out: list[InlineNode] = []
    for child in el:
        cname = _local_name(child)
        if cname == "block" and child.get("name") == "translation-notes":
            continue
        if cname == "p":
            if out:
                out.append(InlineText(text="\n\n"))
            out.extend(_walk_inline(child))
            continue
        inner = _walk_text_block(child)
        if inner:
            if out:
                out.append(InlineText(text="\n\n"))
            out.extend(inner)
    return out


def _parse_version_xml(xml: str) -> etree._Element | None:
    """Safe-default lxml parse. Returns root or None on malformed XML."""
    parser = safe_parser()
    try:
        return etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml, parser)
    except etree.XMLSyntaxError:
        return None


def _eid_index(root: etree._Element) -> dict[str, etree._Element]:
    """Map every element carrying an eId to itself, for O(1) lookup."""
    out: dict[str, etree._Element] = {}
    for el in root.iter():
        eid = el.get("eId") if isinstance(el.tag, str) else None
        if eid:
            out[eid] = el
    return out


# Projection.


_SYNTHETIC_CONTENT_SUFFIX = "__content"


def _to_provision(
    row: Provision,
    xml_index: dict[str, etree._Element],
    stop_at_eids: frozenset[str] = frozenset(),
) -> DocumentProvision:
    el = xml_index.get(row.akn_eid)
    is_synthetic_content = False
    if el is None and row.akn_eid.endswith(_SYNTHETIC_CONTENT_SUFFIX):
        # Mapper-synthesised `<eid>__content` row: walk the parent so
        # blockList items lift into nested blocks.
        parent_eid = row.akn_eid[: -len(_SYNTHETIC_CONTENT_SUFFIX)]
        el = xml_index.get(parent_eid)
        is_synthetic_content = el is not None
    blocks: list[DocumentProvisionBlock] = []
    num: str | None = None
    if el is not None:
        # Never stop at self: excluding own eId lets a leaf that shares
        # its eId with the persisted set still emit its own content.
        stop = stop_at_eids - {row.akn_eid} if stop_at_eids else stop_at_eids
        block = _walk_block(el, stop_at_eids=stop)
        if is_synthetic_content:
            # Section header already shows the container's num/heading
            # and owns the article's eId; drop both on the bridged block.
            block = block.model_copy(update={"num": None, "heading": None, "akn_eid": ""})
        blocks = [block]
        num = block.num
    return DocumentProvision(
        id=row.id,
        akn_eid=row.akn_eid,
        akn_type=row.akn_type,
        text=row.text,
        position=row.position,
        normative=row.normative,
        num=num,
        blocks=blocks,
    )


def _section_num(row: Section, xml_index: dict[str, etree._Element]) -> str | None:
    el = xml_index.get(row.akn_eid)
    if el is None:
        return None
    return _direct_child_text(el, "num")


def _build_section(
    row: Section,
    *,
    children_by_parent: dict[uuid.UUID | None, list[Section]],
    provisions_by_section: dict[uuid.UUID | None, list[Provision]],
    xml_index: dict[str, etree._Element],
    persisted_eids: frozenset[str] = frozenset(),
) -> DocumentSection:
    return DocumentSection(
        id=row.id,
        akn_eid=row.akn_eid,
        akn_type=row.akn_type,
        title=row.title,
        position=row.position,
        num=_section_num(row, xml_index),
        sections=[
            _build_section(
                child,
                children_by_parent=children_by_parent,
                provisions_by_section=provisions_by_section,
                xml_index=xml_index,
                persisted_eids=persisted_eids,
            )
            for child in children_by_parent.get(row.id, [])
        ],
        provisions=[
            _to_provision(p, xml_index, persisted_eids)
            for p in provisions_by_section.get(row.id, [])
        ],
    )


def _amendment_markers(jurisdiction_code: str | None) -> list[str]:
    """The pair this jurisdiction prints around amended words, or brackets."""
    from codify.jurisdictions import try_load_config

    config = try_load_config(jurisdiction_code) if jurisdiction_code else None
    if config is None or config.display is None:
        return ["[", "]"]
    return list(config.display.amendment_markers)


async def get_version_document(
    session: AsyncSession, version_id: uuid.UUID
) -> VersionDocument | None:
    """Return the full body of `version_id` as a typed tree, or None if the
    version doesn't exist."""
    head = (
        await session.execute(
            select(Version, Law, Jurisdiction.code)
            .join(Law, Law.id == Version.law_id)
            .join(Jurisdiction, Jurisdiction.id == Law.jurisdiction_id)
            .where(Version.id == version_id)
        )
    ).first()
    if head is None:
        return None
    version, law, jurisdiction_code = head

    sections = list(
        (
            await session.execute(
                select(Section).where(Section.version_id == version_id).order_by(Section.position)
            )
        )
        .scalars()
        .all()
    )
    provisions = list(
        (
            await session.execute(
                select(Provision)
                .where(Provision.version_id == version_id)
                .order_by(Provision.position)
            )
        )
        .scalars()
        .all()
    )

    markers = _amendment_markers(jurisdiction_code)
    root_xml = _parse_version_xml(version.akn_xml)
    xml_index = _eid_index(root_xml) if root_xml is not None else {}

    preface_nodes: list[InlineNode] = []
    preamble_nodes: list[InlineNode] = []
    conclusions_nodes: list[InlineNode] = []
    if root_xml is not None:
        doc_root = next(iter(root_xml), None)  # <act>/<bill>/…
        if doc_root is not None:
            preface_el = _direct_child(doc_root, "preface")
            preamble_el = _direct_child(doc_root, "preamble")
            if preface_el is not None:
                preface_nodes = _walk_text_block(preface_el)
            if preamble_el is not None:
                preamble_nodes = _walk_text_block(preamble_el)
            conclusions_el = _direct_child(doc_root, "conclusions")
            if conclusions_el is not None:
                conclusions_nodes = _walk_text_block(conclusions_el)

    children_by_parent: dict[uuid.UUID | None, list[Section]] = defaultdict(list)
    for s in sections:
        children_by_parent[s.parent_section_id].append(s)

    provisions_by_section: dict[uuid.UUID | None, list[Provision]] = defaultdict(list)
    for p in provisions:
        provisions_by_section[p.section_id].append(p)

    # Every persisted Provision's eId is a stop point for the walker. When a
    # container (paragraph) and its subparagraphs are both persisted, the
    # container's block tree would otherwise carry a copy of the subparagraphs
    # that the SPA then double-renders alongside their standalone rows.
    persisted_eids = frozenset(p.akn_eid for p in provisions)

    root_sections = [
        _build_section(
            s,
            children_by_parent=children_by_parent,
            provisions_by_section=provisions_by_section,
            xml_index=xml_index,
            persisted_eids=persisted_eids,
        )
        for s in children_by_parent.get(None, [])
    ]
    root_provisions = [
        _to_provision(p, xml_index, persisted_eids) for p in provisions_by_section.get(None, [])
    ]

    return VersionDocument(
        version_id=version.id,
        law_id=law.id,
        amendment_markers=markers,
        frbr_work_uri=law.frbr_work_uri,
        frbr_expression_uri=version.expression_uri,
        language=version.language,
        expression_date=version.expression_date,
        preface=preface_nodes,
        preamble=preamble_nodes,
        conclusions=conclusions_nodes,
        sections=root_sections,
        provisions=root_provisions,
    )


__all__ = [
    "DocumentProvision",
    "DocumentProvisionBlock",
    "DocumentSection",
    "InlineCitation",
    "InlineEmph",
    "InlineNode",
    "InlineRef",
    "InlineStrong",
    "InlineTerm",
    "InlineText",
    "VersionDocument",
    "get_version_document",
]
