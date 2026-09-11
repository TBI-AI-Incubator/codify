from __future__ import annotations

from datetime import date
from typing import Literal, cast

from lxml import etree

from codify.akn._schema import AKN_NS, parse_xml
from codify.akn.analysis import (
    AknAction,
    EventType,
    LifecycleEvent,
    QuotedContent,
    TextualMod,
    category_for,
)
from codify.akn.document import Document
from codify.akn.elements import (
    Article,
    BodyElement,
    Chapter,
    ElementBase,
    Paragraph,
    Point,
    Section,
    Subparagraph,
    Title,
)
from codify.akn.references import (
    DERIVED_REF_CLASS,
    AmendmentReference,
    Citation,
    CrossReference,
    InlineReference,
)
from codify.akn.vocabulary import (
    PRESENTATION_CONTAINER_NAMES,
    TAG_TO_KIND,
    is_lifted_note,
    provision_text,
)

# Map AKN tags to model kinds; preserve the original tag for emission.
_KIND_TO_CLS: dict[str, type[ElementBase]] = {
    "title": Title,
    "chapter": Chapter,
    "section": Section,
    "article": Article,
    "paragraph": Paragraph,
    "subparagraph": Subparagraph,
    "point": Point,
}

AmendmentOp = Literal["insert", "delete", "replace", "renumber"]
_AMENDMENT_OPS: tuple[AmendmentOp, ...] = ("insert", "delete", "replace", "renumber")


def _local(el: etree._Element) -> str:
    return str(etree.QName(el).localname)


def _akn_child(parent: etree._Element, name: str) -> etree._Element | None:
    found: etree._Element | None = parent.find(f"{{{AKN_NS}}}{name}")
    return found


def _akn_findall(parent: etree._Element, name: str) -> list[etree._Element]:
    return list(parent.findall(f"{{{AKN_NS}}}{name}"))


def parse_document(xml: str | bytes, *, huge_tree: bool = False) -> Document:
    root = parse_xml(xml, huge_tree=huge_tree)
    if _local(root) != "akomaNtoso":
        raise ValueError(f"Expected <akomaNtoso> root, got <{_local(root)}>")

    doc_root = next(iter(root), None)
    if doc_root is None:
        raise ValueError("akomaNtoso has no document child")

    meta = _akn_child(doc_root, "meta")
    body = _akn_child(doc_root, "body")
    if meta is None or body is None:
        raise ValueError("Document missing <meta> or <body>")

    frbr = _extract_frbr(meta)
    body_elements = _structural_children(body)
    textual_mods = _extract_analysis(meta)
    lifecycle_events = _extract_lifecycle(meta)

    return Document(
        frbr_work_uri=cast(str, frbr["work_uri"]),
        frbr_expression_uri=cast(str, frbr["expression_uri"]),
        language=cast(str, frbr["language"]),
        expression_date=cast(date, frbr["expression_date"]),
        work_date=cast("date | None", frbr["work_date"]),
        body=body_elements,
        attachments=_extract_attachments(doc_root),
        textual_mods=textual_mods,
        lifecycle_events=lifecycle_events,
    )


def carried_ids(root: etree._Element) -> set[str]:
    """Every name a document identifies one of its own elements by.

    Read from the tree rather than the model: an id can sit on an element the
    model never carries, and a reference to one of those is still a reference
    the document can answer for.
    """
    return {value for el in root.iter() for value in (el.get("eId"), el.get("wId")) if value}


def _extract_attachments(doc_root: etree._Element) -> list[BodyElement]:
    """Each attachment as one container carrying its inner document's content.

    A container rather than loose children, for two reasons. Its heading names
    the attachment ("PENJELASAN"), and loose roots would land at top level with
    positions restarting at zero, tying with the body's chapters: the reader
    orders sections by position alone, so an elucidation article would render
    between two chapters, which is the mixed copy this separation exists to end.

    A generic `<doc>` holds `<mainBody>`; an inner act would hold `<body>`."""
    out: list[BodyElement] = []
    container = _akn_child(doc_root, "attachments")
    if container is None:
        return out
    for i, attachment in enumerate(_akn_findall(container, "attachment")):
        children: list[BodyElement] = []
        for tag in ("mainBody", "body"):
            for inner in attachment.iter(f"{{{AKN_NS}}}{tag}"):
                children.extend(_structural_children(inner))
        if not children:
            continue
        heading = _akn_child(attachment, "heading")
        eid = attachment.get("eId") or f"att_{i + 1}"
        out.append(
            Section(
                akn_eid=eid,
                akn_type="attachment",
                position=i,
                heading=("".join(heading.itertext()).strip() if heading is not None else None),
                children=children,
            )
        )
    return out
    for attachment in _akn_findall(container, "attachment"):
        for tag in ("mainBody", "body"):
            for inner in attachment.iter(f"{{{AKN_NS}}}{tag}"):
                out.extend(_structural_children(inner))
    return out


def _extract_frbr(meta: etree._Element) -> dict[str, str | date | None]:
    work = meta.find(f".//{{{AKN_NS}}}FRBRWork")
    expr = meta.find(f".//{{{AKN_NS}}}FRBRExpression")

    work_uri = ""
    expr_uri = ""
    language = "eng"
    expression_date: date | None = None
    work_date = _sole_date(work)

    if work is not None:
        work_uri_el = _akn_child(work, "FRBRuri")
        if work_uri_el is not None:
            work_uri = work_uri_el.get("value", "")

    if expr is not None:
        expr_uri_el = _akn_child(expr, "FRBRuri")
        if expr_uri_el is not None:
            expr_uri = expr_uri_el.get("value", "")
        lang_el = _akn_child(expr, "FRBRlanguage")
        if lang_el is not None:
            language = lang_el.get("language", language)
        expression_date = _first_date(expr)

    if expression_date is None:
        raise ValueError(
            "Document is missing a parseable <FRBRExpression><FRBRdate date=...> entry"
        )

    return {
        "work_uri": work_uri,
        "expression_uri": expr_uri,
        "language": language,
        "expression_date": expression_date,
        "work_date": work_date,
    }


def _first_date(block: etree._Element | None) -> date | None:
    """The first `FRBRdate` in an FRBR block that parses.

    `maxOccurs="unbounded"`, so a block can carry several dates under different
    `@name`s; the unparseable ones are skipped rather than ending the search."""
    if block is None:
        return None
    for date_el in _akn_findall(block, "FRBRdate"):
        try:
            return date.fromisoformat(date_el.get("date", "")[:10])
        except ValueError:
            continue
    return None


def _sole_date(block: etree._Element | None) -> date | None:
    """The block's date when it states exactly one, else None.

    Document order is not a ranking. A publisher's AKN routinely carries an
    enactment date beside a generation date, and taking whichever came first
    would put the wrong one in `Document.work_date`, from where the emitter
    writes it back out as the work's date. Optional either way, so declining to
    choose costs a fallback rather than a failure."""
    if block is None:
        return None
    parsed = [_first_date(block)] if len(_akn_findall(block, "FRBRdate")) == 1 else []
    return parsed[0] if parsed else None


_NON_STRUCTURAL_CHILDREN = frozenset({"num", "heading", "content", "intro", "wrapUp"})


def _structural_children(parent: etree._Element) -> list[BodyElement]:
    """Recognized BodyElements under `parent`, flattening through unknown wrappers.

    AKN bodies may carry tags that aren't in `TAG_TO_KIND` (Bluebell's TLC*
    metadata leak, hcontainer wrappers, jurisdiction-specific containers).
    Without this, an unknown root drops its entire structural subtree."""
    out: list[BodyElement] = []

    def visit(el: etree._Element) -> None:
        for child in el:
            tag = _local(child)
            if tag in _NON_STRUCTURAL_CHILDREN:
                continue
            parsed = _parse_element(child, position=len(out))
            if parsed is not None:
                out.append(cast(BodyElement, parsed))
            else:
                visit(child)

    visit(parent)
    return out


def _parse_element(el: etree._Element, position: int) -> ElementBase | None:
    tag = _local(el)
    kind = TAG_TO_KIND.get(tag)
    if kind is None:
        return None
    cls = _KIND_TO_CLS[kind]

    eid = el.get("eId", "")
    # ``wId`` is work-scoped and defaults to ``eid`` when absent.
    wid = (el.get("wId") or "").strip()
    num_el = _akn_child(el, "num")
    heading_el = _akn_child(el, "heading")
    subheading_el = _akn_child(el, "subheading")
    content_el = _akn_child(el, "content")
    intro_el = _akn_child(el, "intro")
    wrap_up_el = _akn_child(el, "wrapUp")

    text = ""
    references: list[InlineReference] = []
    # A presentation wrapper carries its title, never its flattened content,
    # which the row index already holds and which is too long to embed.
    if el.get("name") in PRESENTATION_CONTAINER_NAMES:
        text = " ".join(_akn_text(e) for e in (num_el, heading_el, subheading_el) if _akn_text(e))
    elif content_el is not None:
        text, references = _extract_text_and_refs(content_el)

    intro = _extract_plain(intro_el) if intro_el is not None else ""
    wrap_up = _extract_plain(wrap_up_el) if wrap_up_el is not None else ""

    children = _structural_children(el)

    return cls(
        kind=kind,
        akn_eid=eid,
        akn_wid=wid,
        akn_type=tag,
        position=position,
        number=num_el.text if num_el is not None and num_el.text else None,
        heading=heading_el.text if heading_el is not None and heading_el.text else None,
        text=text,
        intro=intro,
        wrap_up=wrap_up,
        references=references,
        children=children,
    )


def _akn_text(el: etree._Element | None) -> str:
    """An element's own text, stripped; empty when it has none."""
    return el.text.strip() if el is not None and el.text else ""


def _extract_plain(el: etree._Element) -> str:
    """Container prose, with lifted footnotes left out.

    `intro` and `wrap_up` feed `provisions.text` the same way `text` does, so a
    note folded in here reaches search, the lens and the comparator identically.
    """
    return provision_text(el)


def _extract_text_and_refs(content: etree._Element) -> tuple[str, list[InlineReference]]:
    parts: list[str] = []
    refs: list[InlineReference] = []
    cursor = 0

    def push(s: str | None) -> None:
        nonlocal cursor
        if s:
            parts.append(s)
            cursor += len(s)

    def indentation(s: str | None) -> bool:
        """Blank and spanning a line break, so it is pretty-printing.

        The space between two inline refs is blank too, but never carries a
        newline, and dropping it fuses the words either side of it.
        """
        return s is not None and not s.strip() and "\n" in s

    def visit(el: etree._Element) -> None:
        push(None if indentation(el.text) else el.text)
        for child in el:
            tag = _local(child)
            if is_lifted_note(child):
                # Notes are not provision text or provision references.
                pass
            elif tag == "ref":
                start = cursor
                snippet = "".join(child.itertext())
                push(snippet)
                ref = _build_ref(child, start, cursor, snippet)
                if ref is not None:
                    refs.append(ref)
            else:
                visit(child)
            push(None if indentation(child.tail) else child.tail)

    # A block boundary is a line boundary; the emitter splits on the same "\n".
    # Every block is visited, not just the <p>s: a sibling blockList or table
    # holds provision text too.
    blocks = [child for child in content if isinstance(child.tag, str)]
    if len(blocks) > 1:
        for index, block in enumerate(blocks):
            if index:
                push("\n")
            visit(block)
    else:
        visit(content)
    return "".join(parts), refs


def _build_ref(
    ref_el: etree._Element, start: int, end: int, snippet: str
) -> InlineReference | None:
    href = ref_el.get("href", "")
    cls = ref_el.get("class", "")
    # The inline-markup pass marks what it mints from prose. Absent the mark the
    # link was in the document as published, which is the case that must not be
    # put through the resolver's confirmation gate.
    origin: Literal["href", "text", "registry"] = (
        "text"
        if DERIVED_REF_CLASS in cls.split()
        # `/go/{ref}` identifies a link resolved by an upstream register.
        else "registry"
        if href.startswith("/go/")
        else "href"
    )
    if "amendment" in cls:
        op: AmendmentOp = "replace"
        for candidate in _AMENDMENT_OPS:
            if candidate in cls:
                op = candidate
                break
        return AmendmentReference(
            start_offset=start,
            end_offset=end,
            text_snippet=snippet,
            amends_uri=href,
            operation=op,
            origin=origin,
        )
    if href.startswith("#") and len(href) > 1:
        return CrossReference(
            start_offset=start,
            end_offset=end,
            text_snippet=snippet,
            target_eid=href[1:],
            origin=origin,
        )
    if href and href != "#":
        return Citation(
            start_offset=start,
            end_offset=end,
            text_snippet=snippet,
            target_uri=href,
            origin=origin,
        )
    return Citation(start_offset=start, end_offset=end, text_snippet=snippet, origin=origin)


_VALID_ACTIONS: frozenset[str] = frozenset(
    (
        "repeal",
        "substitution",
        "insertion",
        "replacement",
        "renumbering",
        "split",
        "join",
        "variation",
        "termModification",
        "authenticInterpretation",
        "exceptionOfScope",
        "extensionOfScope",
        "entryIntoForce",
        "endOfEnactment",
        "postponementOfEntryIntoForce",
        "prorogationOfForce",
        "reEnactment",
        "unconstitutionality",
        "entryIntoEfficacy",
        "endOfEfficacy",
        "inapplication",
        "retroactivity",
        "extraefficacy",
        "postponementOfEfficacy",
        "prorogationOfEfficacy",
    )
)


_VALID_EVENT_TYPES: frozenset[str] = frozenset(("generation", "amendment", "repeal"))


def _quoted_content(mod_el: etree._Element) -> QuotedContent | None:
    previous_el = _akn_child(mod_el, "previous")
    old_els = _akn_findall(mod_el, "old")
    new_els = _akn_findall(mod_el, "new")
    if previous_el is None and not old_els and not new_els:
        return None
    return QuotedContent(
        previous=_inner_xml_or_text(previous_el) if previous_el is not None else None,
        old=[_inner_xml_or_text(el) for el in old_els],
        new=[_inner_xml_or_text(el) for el in new_els],
    )


def _inner_xml_or_text(el: etree._Element) -> str:
    """Flatten an <old> / <new> / <previous> block to a string. If the block
    holds a `<quotedText>`, take its text; if it holds a `<quotedStructure>`,
    serialise the inner XML so structural round-tripping is preserved."""
    quoted_text = _akn_child(el, "quotedText")
    if quoted_text is not None and quoted_text.text:
        return str(quoted_text.text)
    quoted_structure = _akn_child(el, "quotedStructure")
    if quoted_structure is not None:
        return str(etree.tostring(quoted_structure, encoding="unicode")).strip()
    return (el.text or "").strip()


def _extract_analysis(meta: etree._Element) -> list[TextualMod]:
    analysis = _akn_child(meta, "analysis")
    if analysis is None:
        return []
    out: list[TextualMod] = []
    for container_name in ("passiveModifications", "activeModifications"):
        container = _akn_child(analysis, container_name)
        if container is None:
            continue
        for mod_el in _akn_findall(container, "textualMod"):
            action_raw = (mod_el.get("type") or "").strip()
            if action_raw not in _VALID_ACTIONS:
                continue
            action = cast(AknAction, action_raw)
            source_href = mod_el.get("source", "").lstrip("#")
            destination_el = _akn_child(mod_el, "destination")
            target_href = ""
            target_wid: str | None = None
            if destination_el is not None:
                target_href = destination_el.get("href", "")
                target_wid = destination_el.get("wId") or None
            authority = mod_el.get("authority") or None
            # Prefer schema-conformant source/destination children; retain the
            # attribute form for existing documents.
            source_el = _akn_child(mod_el, "source")
            if source_el is not None and not source_href:
                authority = authority or source_el.get("href") or None
                if target_href:
                    source_href = target_href.rsplit("#", 1)[-1]
                if target_wid is None and "#" in target_href:
                    target_wid = target_href.rsplit("#", 1)[-1]
            for_ref = mod_el.get("for") or None
            if for_ref and for_ref.startswith("#"):
                for_ref = for_ref[1:]
            if not source_href or not target_href:
                continue
            out.append(
                TextualMod(
                    akn_category=category_for(action),
                    akn_action=action,
                    source_akn_wid=source_href,
                    target_frbr_uri=target_href,
                    target_akn_wid=target_wid,
                    quoted=_quoted_content(mod_el),
                    authority_uri=authority,
                    mod_eid_ref=for_ref,
                )
            )
    return out


def _extract_lifecycle(meta: etree._Element) -> list[LifecycleEvent]:
    lifecycle = _akn_child(meta, "lifecycle")
    if lifecycle is None:
        return []
    out: list[LifecycleEvent] = []
    for event_el in _akn_findall(lifecycle, "eventRef"):
        date_raw = (event_el.get("date") or "").strip()[:10]
        type_raw = (event_el.get("type") or "").strip()
        if not date_raw or type_raw not in _VALID_EVENT_TYPES:
            continue
        out.append(
            LifecycleEvent(
                event_date=date_raw,
                event_type=cast(EventType, type_raw),
                source_uri=event_el.get("source") or None,
                refers_uri=event_el.get("refers") or None,
                originating_uri=event_el.get("originating") or None,
            )
        )
    return out
