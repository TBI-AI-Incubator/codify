"""Ingest Estonian legislation from Riigi Teataja XML into Akoma Ntoso 3.0.

Deterministic transformation, no LLM required. Maps the official Riigi Teataja
`<oigusakt>` schema to OASIS LegalDocML AKN 3.0 matching `data/jurisdictions/ee/config.json`.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import AsyncIterator
from contextvars import ContextVar
from html import unescape
from pathlib import Path
from typing import Any, cast

import structlog
from lxml import etree
from lxml import html as lxml_html

from codify.acquisition.adapters.ee.identity import read_identity
from codify.akn._schema import AKN_NS, parse_xml, validate_akn
from codify.akn.document import Document
from codify.akn.eids import ensure_unique_eids
from codify.akn.io import parse_akn
from codify.akn.vocabulary import table_row_eid
from codify.pipeline.events import (
    Complete,
    Failed,
    IngestionEvent,
    MetadataExtracted,
    Parsed,
    ValidationIssued,
)
from codify.pipeline.formats.riigi_teataja_annexes import (
    annex_act_id,
    annex_elements,
    annex_url,
)

logger = structlog.get_logger()

NSMAP = {None: AKN_NS}


def _q(tag: str) -> str:
    return f"{{{AKN_NS}}}{tag}"


_SUPERSCRIPT_MAP = {
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
    "⁰": "0",
}

_LEADING_PUNCT = {".", ",", ";", ":", ")", "]", "}", "»", "“", "”"}


def _extract_number(el: etree._Element | None, default: str = "1") -> str:
    """Extract provision number accounting for Estonian ylaIndeks attributes and superscripts."""
    if el is None:
        return default
    text = (el.text or "").strip()
    yla = el.get("ylaIndeks")
    if yla:
        text = f"{text}{yla}"
    for sup, digit in _SUPERSCRIPT_MAP.items():
        text = text.replace(sup, digit)
    cleaned = re.sub(r"[^\w-]", "", text)
    return cleaned or default


def _clean_number_str(raw: str | None, default: str = "1") -> str:
    if not raw:
        return default
    text = raw.strip()
    for sup, digit in _SUPERSCRIPT_MAP.items():
        text = text.replace(sup, digit)
    cleaned = re.sub(r"[^\w-]", "", text)
    return cleaned or default


_TO_SUPERSCRIPT = str.maketrans({digit: sup for sup, digit in _SUPERSCRIPT_MAP.items()})


def _sup_to_unicode(markup: str) -> str:
    """Write ``<sup>N</sup>`` as superscript digits, keeping § 43⁹ apart from § 439."""
    return re.sub(
        r"<sup\b[^>]*>\s*(\d+)\s*</sup\s*>",
        lambda m: m.group(1).translate(_TO_SUPERSCRIPT),
        markup,
        flags=re.I,
    )


def _display_number(raw: str) -> str:
    """A ``kuvatavNr`` as plain text; the source writes its superscripts as HTML."""
    return " ".join(re.sub(r"<[^>]+>", "", _sup_to_unicode(unescape(raw))).split())


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _clean_text(el: etree._Element | None) -> str:
    """All text under ``el`` with its tails, whitespace collapsed; superscript
    digits stay superscript and a line break is a space."""
    if el is None:
        return ""
    parts: list[str] = []

    def walk(node: etree._Element) -> None:
        text = node.text or ""
        if _local(node) == "sup" and text.strip().isdigit():
            text = text.strip().translate(_TO_SUPERSCRIPT)
        parts.append(" " if _local(node) == "reavahetus" else text)
        for child in node:
            walk(child)
            parts.append(child.tail or "")

    walk(el)
    return " ".join("".join(parts).split())


def _rewrite_viide_uri(raw_uri: str) -> str:
    """Rewrite Riigi Teataja internal links to canonical public URLs."""
    m = re.search(r"id=(\d+)(?:!pr(\d+)(?:lg(\d+))?)?", raw_uri)
    if m:
        akt_id = m.group(1)
        pr = m.group(2)
        lg = m.group(3)
        frag = f"#pr{pr}" if pr else ""
        if pr and lg:
            frag = f"#pr{pr}lg{lg}"
        return f"https://www.riigiteataja.ee/akt/{akt_id}{frag}"
    return raw_uri


def is_riigi_teataja(source: Path | str | bytes | etree._Element) -> bool:
    """Check if source is an Estonian Riigi Teataja XML document."""
    try:
        if isinstance(source, etree._Element):
            root = source
        elif isinstance(source, bytes):
            prefix = source[:65536]
        elif isinstance(source, Path):
            with source.open("rb") as stream:
                prefix = stream.read(65536)
        elif isinstance(source, str):
            prefix_text = source[:65536].lstrip()
            if prefix_text.startswith("<"):
                prefix = prefix_text.encode()
            else:
                if len(source) > 4096 or "\n" in source or "\0" in source:
                    return False
                p = Path(source)
                if p.exists():
                    with p.open("rb") as stream:
                        prefix = stream.read(65536)
                else:
                    return False
        else:
            return False
        if not isinstance(source, etree._Element):
            if b"<!doctype" in prefix.lower():
                return False
            parser = etree.XMLPullParser(
                events=("start",), resolve_entities=False, load_dtd=False, no_network=True
            )
            parser.feed(prefix)
            _event, root = next(parser.read_events())
        name = etree.QName(root)
        return bool(name.localname == "oigusakt" and name.namespace == "Juurakt")
    except Exception:
        return False


def _append_text(target: etree._Element, chunk: str) -> None:
    if not chunk:
        return
    if len(target) == 0:
        target.text = (target.text or "") + chunk
    else:
        target[-1].tail = (target[-1].tail or "") + chunk


def _clean_tail(raw_tail: str | None) -> str:
    """Source text with whitespace collapsed; blank text spanning a newline is
    the file's indentation, while a blank space between words is kept."""
    if not raw_tail:
        return ""
    if not raw_tail.strip() and "\n" in raw_tail:
        return ""
    return re.sub(r"\s+", " ", raw_tail)


# Native inline markup and its AKN element.
_INLINE = {"b": "b", "i": "i", "u": "u", "sup": "sup", "sub": "sub"}
_NATIVE_BLOCKS = frozenset({"sisuTekst", "tabel", "tr", "tc"})
# Editorial notes and embedded files: not provision text.
_NOT_TEXT = frozenset({"muutmismarge", "veaparandus", "fail"})
_HTML_INLINE = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "sup": "sup", "sub": "sub"}
# Each starts a new block; anything else unknown is read as inline.
_HTML_BLOCKS = frozenset(
    {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "center"}
    | {"table", "caption", "thead", "tbody", "tfoot", "tr", "td", "th"}
)
# "Signed", as a signature block attests; "allkirjastamise" (signing) is a word in prose.
_SIGNED = re.compile(r"allkirjastatud", re.I)
# Stamps and signature lines run to about 80 characters; a longer line is body text.
_PLACED_TEXT_MAX = 100


class _Blocks:
    """Source text as ``<p>`` blocks under one container: a line break starts the
    next ``<p>``, which Bluebell reads as a line too, and a table goes between."""

    def __init__(
        self,
        container: etree._Element,
        p_eid: str | None,
        owner: etree._Element,
        owner_eid: str,
        *,
        in_cell: bool = False,
    ) -> None:
        self.container = container
        self.p_eid = p_eid
        self.owner = owner
        self.owner_eid = owner_eid
        self.in_cell = in_cell
        self.p: etree._Element | None = None
        self.paras: list[etree._Element] = []
        # Inline elements open in the source, reopened in each new <p>.
        self.open: list[str] = []
        self.chain: list[etree._Element] = []

    def _target(self) -> etree._Element:
        if self.p is None:
            self.p = etree.SubElement(self.container, _q("p"))
            self.paras.append(self.p)
            self.chain = []
        while len(self.chain) < len(self.open):
            parent = self.chain[-1] if self.chain else self.p
            self.chain.append(etree.SubElement(parent, _q(self.open[len(self.chain)])))
        return self.chain[-1] if self.chain else self.p

    def text(self, chunk: str) -> None:
        if not chunk or (self.p is None and not chunk.strip()):
            return
        _append_text(self._target(), chunk)

    def leaf(self, tag: str, text: str, **attrs: str) -> None:
        el = etree.SubElement(self._target(), _q(tag), attrs)
        el.text = text

    def push(self, tag: str) -> None:
        self.open.append(tag)

    def pop(self) -> None:
        self.open.pop()
        if len(self.chain) > len(self.open):
            self.chain.pop()

    def line_break(self) -> None:
        self.p = None

    def table(self, rows: list[list[tuple[str, etree._Element]]]) -> etree._Element:
        """An empty AKN table with eIds, placed between blocks; each cell is
        returned for its caller to fill."""
        self.p = None
        prefix = f"{self.owner_eid}__table_"
        taken = sum(1 for t in self.owner.iter(_q("table")) if t.get("eId", "").startswith(prefix))
        eid = f"{prefix}{taken + 1}"
        table = etree.SubElement(self.container, _q("table"), eId=eid)
        for position, cells in enumerate(rows, start=1):
            tr = etree.SubElement(table, _q("tr"), eId=table_row_eid(eid, position))
            for tag, src in cells:
                spans = {k: str(n) for k in ("colspan", "rowspan") if (n := _span(src, k))}
                etree.SubElement(tr, _q(tag), spans)
        return table

    def finish(self, fallback: str = "") -> None:
        """Trim and number each block; a container left empty gets one ``<p>``
        holding ``fallback``. A block only opens on text, so none is empty."""
        for p in self.paras:
            if len(p):
                # Empty, not absent, or pretty-printing indents inside the <p>.
                p.text = (p.text or "").lstrip()
                p[-1].tail = (p[-1].tail or "").rstrip() or None
            else:
                p.text = (p.text or "").strip()
        if len(self.container) == 0:
            p = etree.SubElement(self.container, _q("p"))
            p.text = fallback
            self.paras.append(p)
        if self.p_eid:
            for index, p in enumerate(self.paras, start=1):
                p.set("eId", f"{self.p_eid}_{index}")


def _span(cell: etree._Element, attr: str) -> int:
    """A cell's span when it covers more than one row or column, else 0."""
    value = (cell.get(attr) or "").strip()
    return int(value) if value.isdecimal() and int(value) > 1 else 0


def _layout_kind(rows: list[list[str]], spanned: bool = False) -> str | None:
    """Why a table only places text, or None for data: ``placed`` short lines beside
    a column blank in every row, or ``signed`` cells. A spanned cell means data."""
    if spanned:
        return None
    width = max((len(r) for r in rows), default=0)
    spacer = any(all(i < len(r) and not r[i] for r in rows) for i in range(width))
    texts = [c for r in rows for c in r if c]
    if spacer and all(sum(1 for c in r if c) <= 1 for r in rows):
        return "placed" if all(len(c) <= _PLACED_TEXT_MAX for c in texts) else None
    return "signed" if all(_SIGNED.search(c) for c in texts) else None


# Tables the document being converted leaves out of its body, by kind.
_LEFT_OUT: ContextVar[Counter[str]] = ContextVar("riigi_teataja_left_out")


def _leave_out(rows: list[list[str]], spanned: bool = False) -> bool:
    """Whether a table stays out of the body; one that does is counted."""
    kind = _layout_kind(rows, spanned)
    if kind is not None:
        _LEFT_OUT.get(Counter())[kind] += 1
    return kind is not None


def _write_native(node: etree._Element, w: _Blocks) -> None:
    """A text element's own text, its children and their tails, in order."""
    w.text(_clean_tail(node.text))
    for child in node:
        tag = _local(child)
        if tag == "viide":
            v_text = child.findtext("{*}kuvatavTekst") or "".join(child.itertext())
            v_uri = child.findtext(".//{*}viideURI")
            if v_uri:
                w.leaf("ref", v_text, href=_rewrite_viide_uri(v_uri))
            else:
                w.leaf("ref", v_text)
        elif tag in _INLINE:
            w.push(_INLINE[tag])
            _write_native(child, w)
            w.pop()
        elif tag == "reavahetus":
            w.line_break()
        elif tag == "HTMLKonteiner":
            _write_html(child.text or "", w)
        elif tag == "tabel" and not w.in_cell:
            _write_native_table(child, w)
        elif tag in _NATIVE_BLOCKS:
            # A table inside a cell: each cell's text its own block.
            w.line_break()
            _write_native(child, w)
            w.line_break()
        elif tag and tag not in _NOT_TEXT:
            # tavatekst, sisuTekst, and markup with no AKN match: keep its text.
            _write_native(child, w)
        w.text(_clean_tail(child.tail))


def _write_native_table(tabel: etree._Element, w: _Blocks) -> None:
    rows = [[tc for tc in tr if _local(tc) == "tc"] for tr in tabel if _local(tr) == "tr"]
    rows = [r for r in rows if r]
    w.line_break()
    if not rows:
        return
    table = w.table([[("td", tc) for tc in r] for r in rows])
    for tr, src_row in zip(table, rows, strict=True):
        for td, tc in zip(tr, src_row, strict=True):
            cell = _Blocks(td, None, w.owner, w.owner_eid, in_cell=True)
            for st in tc:
                if _local(st) not in _NOT_TEXT:
                    _write_source(st, cell)
            cell.finish()
    # Classified on the text as written, so markup inside a cell is not read.
    if _leave_out([[" ".join("".join(td.itertext()).split()) for td in tr] for tr in table]):
        w.container.remove(table)
    w.line_break()


def _html_ws(raw: str | None) -> str:
    return re.sub(r"\s+", " ", raw) if raw else ""


def _write_html(raw: str, w: _Blocks) -> None:
    """An ``HTMLKonteiner``'s markup as blocks: paragraphs and breaks start new
    ones, content tables become AKN tables."""
    if not raw.strip():
        return
    root = lxml_html.fragment_fromstring(_sup_to_unicode(raw), create_parent="div")
    _walk_html(root, w)


def _walk_html(el: etree._Element, w: _Blocks) -> None:
    w.text(_html_ws(el.text))
    for child in el:
        tag = child.tag.lower() if isinstance(child.tag, str) else ""
        if not tag or tag in {"script", "style", "head", "title"}:
            pass  # comments and markup that is not text
        elif tag == "br":
            w.line_break()
        elif tag == "table" and not w.in_cell:
            _write_html_table(child, w)
        elif tag in _HTML_INLINE:
            w.push(_HTML_INLINE[tag])
            _walk_html(child, w)
            w.pop()
        elif tag in _HTML_BLOCKS:
            w.line_break()
            _walk_html(child, w)
            w.line_break()
        else:
            _walk_html(child, w)
        w.text(_html_ws(child.tail))


def _write_html_table(src: etree._Element, w: _Blocks) -> None:
    rows: list[list[etree._Element]] = []
    for tr in src.iter("tr"):
        if next(tr.iterancestors("table"), None) is not src:
            continue
        cells = [c for c in tr if isinstance(c.tag, str) and c.tag.lower() in {"td", "th"}]
        if cells:
            rows.append(cells)
    w.line_break()
    if not rows:
        _walk_html(src, w)
        w.line_break()
        return
    texts = [[" ".join(c.text_content().split()) for c in r] for r in rows]
    spanned = any(_span(c, k) for r in rows for c in r for k in ("colspan", "rowspan"))
    if _leave_out(texts, spanned):
        return
    for caption in src.iterchildren("caption"):
        _walk_html(caption, w)
        w.line_break()
    table = w.table([[(c.tag.lower(), c) for c in r] for r in rows])
    for tr, src_row in zip(table, rows, strict=True):
        for td, cell_src in zip(tr, src_row, strict=True):
            cell = _Blocks(td, None, w.owner, w.owner_eid, in_cell=True)
            _walk_html(cell_src, cell)
            cell.finish()
    w.line_break()


def _write_source(src: etree._Element, w: _Blocks) -> None:
    """One text element as its own run of blocks."""
    if _local(src) == "HTMLKonteiner":
        _write_html(src.text or "", w)
    else:
        _write_native(src, w)
    w.line_break()


def _fill(
    container: etree._Element,
    sources: list[etree._Element],
    *,
    p_eid: str,
    owner: etree._Element,
    owner_eid: str,
    fallback: str = "",
) -> None:
    w = _Blocks(container, p_eid, owner, owner_eid)
    for src in sources:
        _write_source(src, w)
    w.finish(fallback)


_TEXT_TAGS = frozenset({"sisuTekst", "HTMLKonteiner"})


def _split_text(src: etree._Element, unit_tag: str) -> list[list[etree._Element]]:
    """Text elements in runs: before the first ``unit_tag`` child, then after each."""
    runs: list[list[etree._Element]] = [[]]
    for child in src:
        if _local(child) == unit_tag:
            runs.append([])
        elif _local(child) in _TEXT_TAGS:
            runs[-1].append(child)
    return runs


def _fill_unit(
    unit: etree._Element, src: etree._Element, eid: str, trailing: list[etree._Element]
) -> None:
    """A lõige, alampunkt or punkt body and the points under it, then
    ``trailing``: text the source puts between it and the next unit."""
    subs = src.findall("{*}alampunkt")
    runs = _split_text(src, "alampunkt")
    if not subs:
        c = etree.SubElement(unit, _q("content"))
        _fill(c, runs[0] + trailing, p_eid=f"{eid}__p", owner=unit, owner_eid=eid)
        return
    intro = etree.SubElement(unit, _q("intro"))
    _fill(intro, runs[0], p_eid=f"{eid}__intro__p", owner=unit, owner_eid=eid)
    for ap_idx, ap in enumerate(subs, start=1):
        ap_nr = _extract_number(ap.find("{*}alampunktNr"), default=str(ap_idx))
        ap_eid = f"{eid}__point_{ap_nr}"
        pt = etree.SubElement(unit, _q("point"), eId=ap_eid, wId=ap_eid)
        ap_knr = ap.findtext("{*}kuvatavNr")
        if ap_knr and ap_knr.strip():
            pt_num = etree.SubElement(pt, _q("num"))
            pt_num.text = _display_number(ap_knr)
        _fill_unit(pt, ap, ap_eid, runs[ap_idx] if ap_idx < len(subs) else [])
    if tail := runs[-1] + trailing:
        wrap = etree.SubElement(unit, _q("wrapUp"))
        _fill(wrap, tail, p_eid=f"{eid}__wrapup__p", owner=unit, owner_eid=eid)


def _convert_paragrahv(p_src: etree._Element, parent_akn: etree._Element, parent_eid: str) -> None:
    p_nr = _extract_number(p_src.find("{*}paragrahvNr"))
    p_eid = f"{parent_eid}__art_{p_nr}" if parent_eid else f"art_{p_nr}"
    art = etree.SubElement(parent_akn, _q("article"), eId=p_eid, wId=p_eid)

    knr = p_src.findtext("{*}kuvatavNr")
    if knr and knr.strip():
        num = etree.SubElement(art, _q("num"))
        num.text = _display_number(knr)

    h = _clean_text(p_src.find("{*}paragrahvPealkiri"))
    if h:
        heading = etree.SubElement(art, _q("heading"))
        heading.text = h

    loiked = p_src.findall("{*}loige")
    runs = _split_text(p_src, "loige")
    before = runs[0]
    if not loiked:
        c = etree.SubElement(art, _q("content"))
        mm = p_src.find("{*}muutmismarge")
        fallback = "" if before or mm is None else _clean_text(mm)
        _fill(c, before, p_eid=f"{p_eid}__p", owner=art, owner_eid=p_eid, fallback=fallback)
        return

    # Single unnumbered loige: collapse only if it has NO sub-items (alampunkt)
    single_unnumbered = len(loiked) == 1 and not (loiked[0].findtext("{*}loigeNr") or "").strip()
    single_has_points = single_unnumbered and bool(loiked[0].findall("{*}alampunkt"))

    if single_unnumbered and not single_has_points:
        own = _split_text(loiked[0], "")[0]
        c = etree.SubElement(art, _q("content"))
        _fill(c, before + own + runs[-1], p_eid=f"{p_eid}__p", owner=art, owner_eid=p_eid)
        return

    # Text the paragrahv holds beside its lõiked introduces or closes them.
    if before:
        intro = etree.SubElement(art, _q("intro"))
        _fill(intro, before, p_eid=f"{p_eid}__intro__p", owner=art, owner_eid=p_eid)

    for l_idx, loige_el in enumerate(loiked, start=1):
        raw_l_nr = loige_el.find("{*}loigeNr")
        l_nr = _extract_number(raw_l_nr, default=str(l_idx)) if not single_unnumbered else "1"
        l_eid = f"{p_eid}__para_{l_nr}"
        para = etree.SubElement(art, _q("paragraph"), eId=l_eid, wId=l_eid)

        l_knr = loige_el.findtext("{*}kuvatavNr")
        if l_knr and l_knr.strip():
            num = etree.SubElement(para, _q("num"))
            num.text = _display_number(l_knr)

        _fill_unit(para, loige_el, l_eid, runs[l_idx] if l_idx < len(loiked) else [])

    if runs[-1]:
        wrap = etree.SubElement(art, _q("wrapUp"))
        _fill(wrap, runs[-1], p_eid=f"{p_eid}__wrapup__p", owner=art, owner_eid=p_eid)


def _convert_jaotis(jt_src: etree._Element, parent_akn: etree._Element, parent_eid: str) -> None:
    raw_nr = jt_src.find("{*}jaotisNr")
    if raw_nr is None:
        raw_nr = jt_src.find("{*}alljaotisNr")
    jt_nr = _extract_number(raw_nr)
    jt_eid = f"{parent_eid}__subsec_{jt_nr}" if parent_eid else f"subsec_{jt_nr}"
    subsec = etree.SubElement(parent_akn, _q("subsection"), eId=jt_eid, wId=jt_eid)

    knr = jt_src.findtext("{*}kuvatavNr")
    if knr and knr.strip():
        num = etree.SubElement(subsec, _q("num"))
        num.text = _display_number(knr)

    h = _clean_text(jt_src.find("{*}jaotisPealkiri")) or _clean_text(
        jt_src.find("{*}alljaotisPealkiri")
    )
    if h:
        heading = etree.SubElement(subsec, _q("heading"))
        heading.text = h

    for child in jt_src:
        ctag = etree.QName(child).localname
        if ctag == "paragrahv":
            _convert_paragrahv(child, subsec, jt_eid)
        elif ctag in {"jaotis", "alljaotis"}:
            _convert_jaotis(child, subsec, jt_eid)


def _convert_jagu(j_src: etree._Element, parent_akn: etree._Element, parent_eid: str) -> None:
    raw_nr = j_src.find("{*}jaguNr")
    j_nr = _extract_number(raw_nr)
    j_eid = f"{parent_eid}__sec_{j_nr}" if parent_eid else f"sec_{j_nr}"
    sec = etree.SubElement(parent_akn, _q("section"), eId=j_eid, wId=j_eid)

    knr = j_src.findtext("{*}kuvatavNr")
    if knr and knr.strip():
        num = etree.SubElement(sec, _q("num"))
        num.text = _display_number(knr)

    h = _clean_text(j_src.find("{*}jaguPealkiri"))
    if h:
        heading = etree.SubElement(sec, _q("heading"))
        heading.text = h

    for child in j_src:
        ctag = etree.QName(child).localname
        if ctag == "paragrahv":
            _convert_paragrahv(child, sec, j_eid)
        elif ctag in {"jaotis", "alljaotis"}:
            _convert_jaotis(child, sec, j_eid)


def _convert_peatykk(
    p_src: etree._Element, parent_akn: etree._Element, parent_eid: str
) -> tuple[etree._Element, str]:
    prefix = "jagu" if etree.QName(p_src).localname == "jagu" else "peatykk"
    raw_nr = p_src.find(f"{{*}}{prefix}Nr")
    c_nr = _extract_number(raw_nr)
    c_eid = f"{parent_eid}__chp_{c_nr}" if parent_eid else f"chp_{c_nr}"
    chp = etree.SubElement(parent_akn, _q("chapter"), eId=c_eid, wId=c_eid)

    knr = p_src.findtext("{*}kuvatavNr")
    if knr and knr.strip():
        num = etree.SubElement(chp, _q("num"))
        num.text = _display_number(knr)

    h = _clean_text(p_src.find(f"{{*}}{prefix}Pealkiri"))
    if h:
        heading = etree.SubElement(chp, _q("heading"))
        heading.text = h

    for child in p_src:
        ctag = etree.QName(child).localname
        if ctag == "jagu":
            _convert_jagu(child, chp, c_eid)
        elif ctag == "paragrahv":
            _convert_paragrahv(child, chp, c_eid)
        elif ctag in {"jaotis", "alljaotis"}:
            _convert_jaotis(child, chp, c_eid)

    return chp, c_eid


def _convert_osa(o_src: etree._Element, parent_akn: etree._Element) -> tuple[etree._Element, str]:
    raw_nr = o_src.find("{*}osaNr")
    o_nr = _extract_number(raw_nr)
    o_eid = f"part_{o_nr}"
    part = etree.SubElement(parent_akn, _q("part"), eId=o_eid, wId=o_eid)

    knr = o_src.findtext("{*}kuvatavNr")
    if knr and knr.strip():
        num = etree.SubElement(part, _q("num"))
        num.text = _display_number(knr)

    h = _clean_text(o_src.find("{*}osaPealkiri"))
    if h:
        heading = etree.SubElement(part, _q("heading"))
        heading.text = h

    for child in o_src:
        ctag = etree.QName(child).localname
        if ctag == "peatykk":
            _convert_peatykk(child, part, o_eid)
        elif ctag == "jagu":
            _convert_jagu(child, part, o_eid)
        elif ctag == "paragrahv":
            _convert_paragrahv(child, part, o_eid)

    return part, o_eid


def riigi_teataja_to_akn(
    source_xml: str | bytes,
    *,
    frbr_work_uri: str | None = None,
    language: str = "est",
) -> tuple[str, dict[str, Any]]:
    """Transform Riigi Teataja XML to canonical Akoma Ntoso 3.0.

    Returns (akn_xml, metadata).
    """
    root = parse_xml(source_xml, huge_tree=True)
    meta = root.find("{*}metaandmed")

    identity = read_identity(root)
    title, doctype = identity.title, identity.doctype
    enactment_date, publication_date = identity.enacted, identity.published
    primary_date = enactment_date or publication_date
    year = identity.year

    number = ""
    if meta is not None:
        m_vastu = meta.find("{*}vastuvoetud")
        if m_vastu is not None:
            number = (m_vastu.findtext("{*}aktiNr") or "").strip()
        if not number:
            number = (meta.findtext(".//{*}RTartikkel") or "").strip()

    work_uri = frbr_work_uri or identity.work_uri

    exp_date = (
        meta.findtext(".//{*}kehtivuseAlgus") if meta is not None else primary_date
    ) or primary_date
    if "+" in exp_date:
        exp_date = exp_date.split("+")[0].strip()

    exp_uri = f"{work_uri}/{language}@{exp_date}"
    man_uri = f"{exp_uri}.akn"

    # Determine author TLC based on issuing body
    valjaandja = (meta.findtext("{*}valjaandja") if meta is not None else "") or ""
    valjaandja_lower = valjaandja.lower()
    if "valitsus" in valjaandja_lower:
        author_id = "valitsus"
        author_role_type = "TLCOrganization"
        author_href = "/ontology/org/ee/vabariigi-valitsus"
        author_name = "Vabariigi Valitsus"
    elif "minister" in valjaandja_lower:
        author_id = "minister"
        author_role_type = "TLCRole"
        author_href = "/ontology/role/ee/minister"
        author_name = valjaandja or "Minister"
    else:
        author_id = "riigikogu"
        author_role_type = "TLCOrganization"
        author_href = "/ontology/org/ee/riigikogu"
        author_name = "Riigikogu"

    akn_root = etree.Element(_q("akomaNtoso"), nsmap=NSMAP)
    act = etree.SubElement(akn_root, _q("act"), name=doctype)

    # Meta
    meta_el = etree.SubElement(act, _q("meta"))
    ident = etree.SubElement(meta_el, _q("identification"), source="#codify")

    # FRBRWork
    f_work = etree.SubElement(ident, _q("FRBRWork"))
    etree.SubElement(f_work, _q("FRBRthis"), value=work_uri)
    etree.SubElement(f_work, _q("FRBRuri"), value=work_uri)
    etree.SubElement(f_work, _q("FRBRdate"), date=enactment_date or primary_date, name="enacted")
    etree.SubElement(f_work, _q("FRBRauthor"), href=f"#{author_id}")
    etree.SubElement(f_work, _q("FRBRcountry"), value="ee")

    # FRBRExpression
    f_exp = etree.SubElement(ident, _q("FRBRExpression"))
    etree.SubElement(f_exp, _q("FRBRthis"), value=exp_uri)
    etree.SubElement(f_exp, _q("FRBRuri"), value=exp_uri)
    etree.SubElement(f_exp, _q("FRBRdate"), date=exp_date, name="expression")
    etree.SubElement(f_exp, _q("FRBRauthor"), href=f"#{author_id}")
    etree.SubElement(f_exp, _q("FRBRlanguage"), language=language)

    # FRBRManifestation
    f_man = etree.SubElement(ident, _q("FRBRManifestation"))
    etree.SubElement(f_man, _q("FRBRthis"), value=man_uri)
    etree.SubElement(f_man, _q("FRBRuri"), value=man_uri)
    etree.SubElement(f_man, _q("FRBRdate"), date=exp_date, name="generation")
    etree.SubElement(f_man, _q("FRBRauthor"), href="#codify")

    # Publication
    rt_art = meta.findtext(".//{*}RTartikkel") if meta is not None else ""
    rt_osa = meta.findtext(".//{*}RTosa") if meta is not None else "RT I"
    pub_date = publication_date or primary_date
    pub_show = (
        f"{rt_osa}, {pub_date}, {rt_art}"
        if rt_art
        else f"{rt_osa}, {pub_date}"
        if pub_date
        else rt_osa
    )
    etree.SubElement(
        meta_el,
        _q("publication"),
        date=pub_date,
        name=rt_osa or "RT I",
        showAs=pub_show,
    )

    # References
    refs = etree.SubElement(meta_el, _q("references"), source="#codify")
    etree.SubElement(
        refs,
        _q("TLCOrganization"),
        eId="codify",
        href="/ontology/org/codify",
        showAs="Codify",
    )
    etree.SubElement(
        refs,
        _q(author_role_type),
        eId=author_id,
        href=author_href,
        showAs=author_name,
    )

    # Preface
    preface = etree.SubElement(act, _q("preface"))
    p_title = etree.SubElement(preface, _q("p"), eId="preface__p_1")
    p_title.text = title

    left_out: Counter[str] = Counter()
    _LEFT_OUT.set(left_out)

    # Preamble
    sisu = root.find("{*}sisu")
    if sisu is not None:
        preambul = sisu.find("{*}preambul")
        if preambul is not None:
            preamble = etree.SubElement(act, _q("preamble"))
            formula = etree.SubElement(preamble, _q("formula"), name="enactingFormula")
            _fill(
                formula,
                [preambul],
                p_eid="preamble__formula__p",
                owner=preamble,
                owner_eid="preamble",
            )

    # Body
    body = etree.SubElement(act, _q("body"))
    if sisu is not None:
        direct_st_counts: dict[str, int] = defaultdict(int)
        current_osa_akn: etree._Element | None = None
        current_osa_eid: str = ""
        current_peatykk_akn: etree._Element | None = None
        current_peatykk_eid: str = ""

        for child in sisu:
            ctag = etree.QName(child).localname
            if ctag == "osa":
                current_osa_akn, current_osa_eid = _convert_osa(child, body)
                current_peatykk_akn = None
                current_peatykk_eid = ""
            elif ctag == "peatykk":
                current_osa_akn = None
                current_osa_eid = ""
                current_peatykk_akn, current_peatykk_eid = _convert_peatykk(child, body, "")
            elif ctag == "jagu":
                target_parent = (
                    current_peatykk_akn
                    if current_peatykk_akn is not None
                    else (current_osa_akn if current_osa_akn is not None else body)
                )
                target_eid = (
                    current_peatykk_eid
                    if current_peatykk_akn is not None
                    else (current_osa_eid if current_osa_akn is not None else "")
                )
                if doctype == "constitution":
                    _convert_peatykk(child, target_parent, target_eid)
                else:
                    _convert_jagu(child, target_parent, target_eid)
            elif ctag == "paragrahv":
                target_parent = (
                    current_peatykk_akn
                    if current_peatykk_akn is not None
                    else (current_osa_akn if current_osa_akn is not None else body)
                )
                target_eid = (
                    current_peatykk_eid
                    if current_peatykk_akn is not None
                    else (current_osa_eid if current_osa_akn is not None else "")
                )
                _convert_paragrahv(child, target_parent, target_eid)
            elif ctag == "punkt":
                target_parent = (
                    current_peatykk_akn
                    if current_peatykk_akn is not None
                    else (current_osa_akn if current_osa_akn is not None else body)
                )
                target_eid = (
                    current_peatykk_eid
                    if current_peatykk_akn is not None
                    else (current_osa_eid if current_osa_akn is not None else "")
                )
                raw_nr = child.find("{*}punktNr")
                p_nr = _extract_number(raw_nr)
                p_eid = f"{target_eid}__point_{p_nr}" if target_eid else f"point_{p_nr}"
                pt = etree.SubElement(target_parent, _q("point"), eId=p_eid, wId=p_eid)
                knr = child.findtext("{*}kuvatavNr")
                if knr and knr.strip():
                    pt_num = etree.SubElement(pt, _q("num"))
                    pt_num.text = _display_number(knr)
                _fill_unit(pt, child, p_eid, [])
            elif ctag in {"sisuTekst", "HTMLKonteiner"}:
                target_parent = (
                    current_peatykk_akn
                    if current_peatykk_akn is not None
                    else (current_osa_akn if current_osa_akn is not None else body)
                )
                target_eid = (
                    current_peatykk_eid
                    if current_peatykk_akn is not None
                    else (current_osa_eid if current_osa_akn is not None else "")
                )
                direct_st_counts[target_eid] += 1
                st_count = direct_st_counts[target_eid]
                p_eid = f"{target_eid}__para_{st_count}" if target_eid else f"para_{st_count}"
                para = etree.SubElement(target_parent, _q("paragraph"), eId=p_eid, wId=p_eid)
                c_el = etree.SubElement(para, _q("content"))
                _fill(c_el, [child], p_eid=f"{p_eid}__p", owner=para, owner_eid=p_eid)

    # Attachments
    lisad = annex_elements(root)
    if lisad:
        attachments = etree.SubElement(act, _q("attachments"))
        akt_id = annex_act_id(root)
        for l_idx, lisa in enumerate(lisad, start=1):
            att_eid = f"att_{l_idx}"
            att = etree.SubElement(attachments, _q("attachment"), eId=att_eid)

            lp_el = lisa.find(".//{*}lisaPealkiri")
            lisa_title = _clean_text(lp_el) if lp_el is not None else ""
            if not lisa_title:
                fn_el = lisa.find(".//{*}fail")
                lisa_title = (fn_el.get("failNimi") if fn_el is not None else "") or f"Lisa {l_idx}"
            heading_el = etree.SubElement(att, _q("heading"))
            heading_el.text = lisa_title

            doc_att = etree.SubElement(att, _q("doc"), name=f"lisa_{l_idx}")

            doc_meta = etree.SubElement(doc_att, _q("meta"))
            doc_ident = etree.SubElement(doc_meta, _q("identification"), source="#codify")
            f_w = etree.SubElement(doc_ident, _q("FRBRWork"))
            etree.SubElement(f_w, _q("FRBRthis"), value=f"{work_uri}/!{att_eid}")
            etree.SubElement(f_w, _q("FRBRuri"), value=work_uri)
            etree.SubElement(
                f_w, _q("FRBRdate"), date=enactment_date or primary_date, name="enacted"
            )
            etree.SubElement(f_w, _q("FRBRauthor"), href=f"#{author_id}")
            etree.SubElement(f_w, _q("FRBRcountry"), value="ee")

            f_e = etree.SubElement(doc_ident, _q("FRBRExpression"))
            etree.SubElement(f_e, _q("FRBRthis"), value=f"{exp_uri}/!{att_eid}")
            etree.SubElement(f_e, _q("FRBRuri"), value=exp_uri)
            etree.SubElement(f_e, _q("FRBRdate"), date=exp_date, name="expression")
            etree.SubElement(f_e, _q("FRBRauthor"), href=f"#{author_id}")
            etree.SubElement(f_e, _q("FRBRlanguage"), language=language)

            f_m = etree.SubElement(doc_ident, _q("FRBRManifestation"))
            etree.SubElement(f_m, _q("FRBRthis"), value=f"{man_uri}/!{att_eid}")
            etree.SubElement(f_m, _q("FRBRuri"), value=man_uri)
            etree.SubElement(
                f_m,
                _q("FRBRdate"),
                date=exp_date,
                name="generation",
            )
            etree.SubElement(f_m, _q("FRBRauthor"), href="#codify")

            doc_body = etree.SubElement(doc_att, _q("mainBody"))
            lisa_p = etree.SubElement(doc_body, _q("p"), eId=f"{att_eid}__p_1")

            fail_el = lisa.find(".//{*}fail")
            fail_nimi = fail_el.get("failNimi") if fail_el is not None else None
            if fail_nimi and akt_id:
                ref = etree.SubElement(lisa_p, _q("ref"), href=annex_url(akt_id, fail_nimi))
                ref.text = f"{lisa_title} ({fail_nimi})"
            else:
                lisa_p.text = lisa_title

    raw_xml_out = cast(
        str,
        etree.tostring(akn_root, pretty_print=True, xml_declaration=True, encoding="utf-8").decode(
            "utf-8"
        ),
    )
    unique_xml, _ = ensure_unique_eids(raw_xml_out, huge_tree=True)

    metadata: dict[str, Any] = {
        "title": title,
        "number": number or "1",
        "year": int(year),
        "date": enactment_date or primary_date,
        "language": language,
        "doctype": doctype,
        "frbr_work_uri": work_uri,
        "frbr_expression_uri": exp_uri,
        "gazette": {
            "name": rt_osa or "RT I",
            "date": publication_date or primary_date,
            "number": rt_art or number,
            "year": int(publication_date.split("-")[0]) if publication_date else int(year),
        },
    }
    if left_out:
        # A wrong layout call drops body text, so each document says what it left out.
        logger.info("riigi_teataja_tables_left_out", expression=exp_uri, **left_out)

    return unique_xml, metadata


async def ingest(
    source: Path | str,
    jurisdiction_code: str = "ee",
    *,
    frbr_work_uri: str | None = None,
    language: str = "est",
) -> AsyncIterator[IngestionEvent]:
    """Ingest Riigi Teataja XML document deterministically."""
    from codify.pipeline.enrich.validator import validate_akn as run_validator
    from codify.pipeline.formats.eu_directive import _on_the_cpu_pool

    try:
        path = source if isinstance(source, Path) else Path(source)
        raw_xml = path.read_bytes()

        def convert() -> tuple[str, dict[str, Any]]:
            return riigi_teataja_to_akn(
                raw_xml,
                frbr_work_uri=frbr_work_uri,
                language=language,
            )

        akn_xml, metadata = await _on_the_cpu_pool(convert)

        yield MetadataExtracted(metadata=metadata)
        yield Parsed(akn_xml_len=len(akn_xml))

        # Schema validation (offloaded to CPU pool)
        def _validate_huge(xml: str) -> None:
            validate_akn(xml, huge_tree=True)

        try:
            await _on_the_cpu_pool(_validate_huge, akn_xml)
        except Exception as exc:
            logger.warning("akn_schema_validation_failed", error=str(exc))
            yield Failed(stage="schema_validation", error=f"{type(exc).__name__}: {exc}")
            return

        # Pipeline validator findings
        def _run_validator_huge(xml: str) -> list[dict[str, Any]]:
            return run_validator(xml, huge_tree=True, provenance="native")

        try:
            for issue in await _on_the_cpu_pool(_run_validator_huge, akn_xml):
                yield ValidationIssued(issue=issue)
        except Exception as exc:
            logger.warning("validator_failed", error=str(exc))
            yield Failed(stage="validator", error=f"{type(exc).__name__}: {exc}")
            return

        doc: Document = parse_akn(akn_xml, huge_tree=True)

        logger.info(
            "riigi_teataja_ingested",
            jurisdiction=jurisdiction_code,
            frbr=doc.frbr_work_uri,
            provisions_len=len(doc.body),
        )

        yield Complete(document=doc, akn_xml=akn_xml)

    except Exception as exc:
        yield Failed(stage="riigi_teataja", error=f"{type(exc).__name__}: {exc}")


__all__ = [
    "ingest",
    "is_riigi_teataja",
    "riigi_teataja_to_akn",
]
