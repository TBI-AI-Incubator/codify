from __future__ import annotations

from lxml import etree

from codify.akn._schema import AKN_NS, NSMAP
from codify.akn.document import Document
from codify.akn.elements import ElementBase
from codify.akn.frbr import country_of
from codify.akn.references import (
    DERIVED_REF_CLASS,
    AmendmentReference,
    Citation,
    CrossReference,
    InlineReference,
)


def _q(name: str) -> str:
    return f"{{{AKN_NS}}}{name}"


def emit_document(doc: Document) -> bytes:
    root = etree.Element(_q("akomaNtoso"), nsmap=NSMAP)
    act = etree.SubElement(root, _q("act"))
    act.set("name", "act")
    act.append(_emit_meta(doc))
    body = etree.SubElement(act, _q("body"))
    for element in doc.body:
        body.append(_emit_element(element))
    rendered: bytes = etree.tostring(
        root, xml_declaration=True, encoding="utf-8", pretty_print=False
    )
    return rendered


def _emit_meta(doc: Document) -> etree._Element:
    meta = etree.Element(_q("meta"))
    identification = etree.SubElement(meta, _q("identification"))
    identification.set("source", "#codify")

    work = etree.SubElement(identification, _q("FRBRWork"))
    etree.SubElement(work, _q("FRBRthis")).set("value", doc.frbr_work_uri)
    etree.SubElement(work, _q("FRBRuri")).set("value", doc.frbr_work_uri)
    work_date = etree.SubElement(work, _q("FRBRdate"))
    work_date.set("date", (doc.work_date or doc.expression_date).isoformat())
    work_date.set("name", "Generation")
    etree.SubElement(work, _q("FRBRauthor")).set("href", "#codify")
    etree.SubElement(work, _q("FRBRcountry")).set("value", country_of(doc.frbr_work_uri))

    expression = etree.SubElement(identification, _q("FRBRExpression"))
    etree.SubElement(expression, _q("FRBRthis")).set("value", doc.frbr_expression_uri)
    etree.SubElement(expression, _q("FRBRuri")).set("value", doc.frbr_expression_uri)
    expression_date = etree.SubElement(expression, _q("FRBRdate"))
    expression_date.set("date", doc.expression_date.isoformat())
    expression_date.set("name", "Generation")
    etree.SubElement(expression, _q("FRBRauthor")).set("href", "#codify")
    etree.SubElement(expression, _q("FRBRlanguage")).set("language", doc.language)

    manifestation = etree.SubElement(identification, _q("FRBRManifestation"))
    etree.SubElement(manifestation, _q("FRBRthis")).set("value", f"{doc.frbr_expression_uri}.akn")
    etree.SubElement(manifestation, _q("FRBRuri")).set("value", f"{doc.frbr_expression_uri}.akn")
    manifestation_date = etree.SubElement(manifestation, _q("FRBRdate"))
    manifestation_date.set("date", doc.expression_date.isoformat())
    manifestation_date.set("name", "Generation")
    etree.SubElement(manifestation, _q("FRBRauthor")).set("href", "#codify")

    return meta


def _emit_element(element: ElementBase) -> etree._Element:
    tag = element.akn_type or element.kind
    el = etree.Element(_q(tag))
    if element.akn_eid:
        el.set("eId", element.akn_eid)
    if element.number:
        etree.SubElement(el, _q("num")).text = element.number
    if element.heading:
        etree.SubElement(el, _q("heading")).text = element.heading
    eid = element.akn_eid
    if element.intro:
        intro = etree.SubElement(el, _q("intro"))
        intro_p = etree.SubElement(intro, _q("p"))
        intro_p.text = element.intro
        # The container stays bare, as Bluebell leaves it: an eId here has no
        # num to derive from, so the repair pass renumbers it to intro_0 and
        # drags its paragraph's ordinal down with it.
        if eid:
            intro_p.set("eId", f"{eid}__intro__p_1")
    if element.text or element.references:
        content = etree.SubElement(el, _q("content"))
        _render_text(content, element.text, element.references, eid)
    for child in element.children:
        el.append(_emit_element(child))
    if element.wrap_up:
        wrap = etree.SubElement(el, _q("wrapUp"))
        wrap_p = etree.SubElement(wrap, _q("p"))
        wrap_p.text = element.wrap_up
        if eid:
            wrap_p.set("eId", f"{eid}__wrapup__p_1")
    return el


def _render_text(
    content: etree._Element, text: str, refs: list[InlineReference], eid: str = ""
) -> None:
    """One <p> per line, each with its own eId when the element has one.

    An annotation can only target a paragraph that exists as an element.
    """
    sorted_refs = sorted(refs, key=lambda r: r.start_offset)
    cursor = 0  # offset of the current line's start within `text`
    for index, line in enumerate(text.split("\n"), start=1):
        p = etree.SubElement(content, _q("p"))
        if eid:
            p.set("eId", f"{eid}__p_{index}")
        # Refs carry offsets into the whole text; rebase them onto this line.
        line_refs = [
            (r, r.start_offset - cursor, min(r.end_offset, cursor + len(line)) - cursor)
            for r in sorted_refs
            if cursor <= r.start_offset < cursor + len(line)
        ]
        _render_line(p, line, line_refs)
        cursor += len(line) + 1  # +1 for the newline split() removed


def _render_line(
    p: etree._Element, text: str, refs: list[tuple[InlineReference, int, int]]
) -> None:
    if not refs:
        p.text = text
        return
    cursor = 0
    last: etree._Element = p
    for r, start, end in refs:
        # Skip refs that overlap a previously rendered span.
        if start < cursor:
            continue
        _append_text(last, p, text[cursor:start])
        ref_el = etree.SubElement(p, _q("ref"))
        _set_ref_href(ref_el, r)
        # The parser reads provenance off this marker, so the writer has to
        # emit it: without it `parse_akn(to_akn(doc))` turned every reading
        # back into a publisher-authored link.
        if r.origin == "text":
            # Composed, not assigned: `_set_ref_href` writes the amendment
            # operation into the same attribute, and replacing it made the
            # parser read the amendment back as a plain citation, losing the
            # operation on every round trip.
            tokens = (ref_el.get("class") or "").split()
            if DERIVED_REF_CLASS not in tokens:
                ref_el.set("class", " ".join([DERIVED_REF_CLASS, *tokens]))
        ref_el.text = text[start:end]
        cursor = end
        last = ref_el
    _append_text(last, p, text[cursor:])


def _append_text(target: etree._Element, parent: etree._Element, chunk: str) -> None:
    if not chunk:
        return
    if target is parent:
        target.text = (target.text or "") + chunk
    else:
        target.tail = (target.tail or "") + chunk


def _set_ref_href(ref_el: etree._Element, r: InlineReference) -> None:
    if isinstance(r, Citation):
        if r.target_uri:
            ref_el.set("href", r.target_uri)
    elif isinstance(r, CrossReference):
        if r.target_eid:
            ref_el.set("href", f"#{r.target_eid}")
        elif r.target_uri:
            ref_el.set("href", r.target_uri)
    elif isinstance(r, AmendmentReference):
        ref_el.set("href", r.amends_uri)
        ref_el.set("class", f"amendment-{r.operation}")
