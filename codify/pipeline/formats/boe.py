"""Convert BOE Diario Oficial XML to Akoma Ntoso 3.0, with no model in the loop.

The BOE serves each item as a `<documento>`: `<metadatos>` plus a flat `<texto>`
of classed `<p>` paragraphs. Structure is recovered from the classes and the
heading text: books, titles, chapters, sections, articles, the four kinds of
disposition and annexes. Text an article quotes for amendment stays inside it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import structlog
from lxml import etree

from codify.acquisition.adapters.es.boe import (
    GAZETTE_NAME,
    boe_work_uri,
    iso_date,
    item_date,
    item_doctype,
    item_number,
    read_metadata,
)
from codify.akn._schema import AKN_NS, parse_xml, validate_akn
from codify.akn.eids import ensure_unique_eids
from codify.akn.io import parse_akn
from codify.pipeline.events import (
    Complete,
    Failed,
    IngestionEvent,
    MetadataExtracted,
    Parsed,
    ValidationIssued,
)

logger = structlog.get_logger()


class BoeTextMissing(ValueError):
    """The item carries no `<texto>` paragraphs, only its PDF."""


def _q(tag: str) -> str:
    return f"{{{AKN_NS}}}{tag}"


def _sub(parent: etree._Element, tag: str, **attrs: str) -> etree._Element:
    return etree.SubElement(parent, _q(tag), **attrs)


def _fold(text: str) -> str:
    """Lower case without accents, for matching words."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# --- numerals --------------------------------------------------------------

_ORD_SPECIAL = {"undecim": 11, "duodecim": 12, "unic": 1}
_ORD_TENS = {
    "centesim": 100,
    "nonagesim": 90,
    "octogesim": 80,
    "septuagesim": 70,
    "sexagesim": 60,
    "quincuagesim": 50,
    "cuadragesim": 40,
    "trigesim": 30,
    "vigesim": 20,
    "decim": 10,
}
_ORD_UNITS = {
    "primer": 1,
    "segund": 2,
    "tercer": 3,
    "cuart": 4,
    "quint": 5,
    "sext": 6,
    "septim": 7,
    "setim": 7,
    "octav": 8,
    "noven": 9,
    "non": 9,
}
_CARD = {
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
}
_CARD_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60}
_CARD_TENS.update({"setenta": 70, "ochenta": 80, "noventa": 90})


def ordinal_value(word: str) -> int | None:
    """`primera` -> 1, `decimotercera` and `vigésima primera` -> 13 and 21."""
    s = re.sub(r"[\s-]+", "", _fold(word))
    for stem, value in _ORD_SPECIAL.items():
        if s in (stem + "o", stem + "a"):
            return value
    total = 0
    for stem, value in _ORD_TENS.items():
        if s.startswith(stem) and s[len(stem) : len(stem) + 1] in ("o", "a"):
            total, s = value, s[len(stem) + 1 :]
            break
    if not s:
        return total or None
    if total and not s.startswith("o"):
        s = "o" + s if s.startswith("ctav") else s  # decimoctava: the units' "o" elides
    for stem, value in _ORD_UNITS.items():
        if s.startswith(stem) and s[len(stem) :] in ("", "o", "a"):
            if s[len(stem) :] == "" and stem not in ("primer", "tercer"):
                continue
            return total + value
    return None


def cardinal_value(word: str) -> int | None:
    """`Uno` -> 1, `veintitrés` -> 23, `treinta y dos` -> 32."""
    s = _fold(word).strip()
    if s in _CARD:
        return _CARD[s]
    if s.startswith("veinti"):
        unit = _CARD.get({"un": "uno"}.get(s[6:], s[6:]))
        return 20 + unit if unit and unit < 10 else None
    tens, _, unit_word = s.partition(" y ")
    if tens in _CARD_TENS:
        if not unit_word:
            return _CARD_TENS[tens]
        unit = _CARD.get(unit_word)
        return _CARD_TENS[tens] + unit if unit and unit < 10 else None
    return None


_ROMAN = re.compile(r"^[IVXLC]+$")


def _roman_value(token: str) -> int | None:
    if not _ROMAN.match(token):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    for i, ch in enumerate(token):
        v = values[ch]
        total += -v if i + 1 < len(token) and values[token[i + 1]] > v else v
    return total


def _to_roman(n: int) -> str:
    out = ""
    for v, s in ((100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX")):
        while n >= v:
            out, n = out + s, n - v
    for v, s in ((5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + s, n - v
    return out


def _eid_token(num: str) -> str:
    keep_case = bool(num) and _ROMAN.match(num.split()[0]) is not None
    return re.sub(r"[^\w]+", "", num if keep_case else _fold(num)) or "1"


# --- heading grammar -------------------------------------------------------

_LATIN = r"(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|novies|decies)"
_WORD = r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+"
# The number: digits, a Roman numeral, or one or two ordinal words.
_NUM = rf"(?P<n>\d+(?:\.?[ªº°])?|[IVXLC]+\b|{_WORD}(?:\s+{_WORD})?)(?P<x>(?:[\s-]*{_LATIN}\b)?)"
_SEP = r"(?P<sep>\s*\.\s*[-–—]|\s*\.|\s*[-–—]|\s*:)?"
_ARTICLE = re.compile(rf"^(?:Art[íi]culo|ART[ÍI]CULO|Art\.?)\s+{_NUM}{_SEP}\s*(?P<rest>.*)$", re.S)
_DISP_KINDS = ("adicional", "transitoria", "derogatoria", "final")
_DISPOSITION = re.compile(
    rf"^Disposici[óo]n\s+(?P<kind>{'|'.join(_DISP_KINDS)})(?:\s+{_NUM})?{_SEP}\s*(?P<rest>.*)$",
    re.S | re.I,
)
_ORDINAL_HEAD = re.compile(rf"^{_NUM}{_SEP}\s*(?P<rest>.*)$", re.S)
_GROUP = re.compile(
    rf"^DISPOSICI(?:[ÓO]N|ONES)\s+(?P<kind>{'|'.join(_DISP_KINDS)})(?:ES|S)?(?:\s+{_NUM})?{_SEP}(?P<rest>)$",
    re.I,
)
_LEVELS = {"libro": "book", "titulo": "title", "capitulo": "chapter"}
_LEVELS.update({"seccion": "section", "subseccion": "subsection"})
_RANK = {"book": 0, "title": 1, "chapter": 2, "section": 3, "subsection": 4}
_STRUCT = re.compile(
    rf"^(?P<kw>LIBRO|T[ÍI]TULO|CAP[ÍI]TULO|SECCI[ÓO]N|SUBSECCI[ÓO]N)\s+{_NUM}{_SEP}\s*(?P<rest>.*)$",
    re.S | re.I,
)
_ANNEX = re.compile(rf"^ANEXOS?(?:\s+{_NUM})?{_SEP}\s*(?P<rest>.*)$", re.S | re.I)
_MARKER = re.compile(r"^\[(?P<m>[a-z_]+)\]\s*")
_FORMULA = re.compile(
    r"^(?:A todos los que la presente|Sabed\s*:|En su virtud|D\s*I\s*S\s*P\s*O\s*N\s*G\s*O)", re.I
)
_CLOSING = re.compile(
    r"^(?:Por tanto,?|Mando a todos los españoles|Dad[oa] en\b|Palacio (?:de|Real)\b"
    r"|(?:En )?Madrid,? (?:a )?\d)",
)
_BODY_CLASSES = ("parrafo", "sangrado", "cuerpo_tabla", "cabeza_tabla", "atexto", "cita")


def _number(n: str, x: str, *, roman: bool) -> tuple[str, int | None] | None:
    """Display numeral and value for a heading number, or None when not one."""
    suffix = (" " + _fold(x).strip(" -")) if x.strip() else ""
    digits = re.match(r"\d+", n)
    if digits:
        return digits.group() + suffix, int(digits.group())
    if _ROMAN.match(n):
        return n + suffix, _roman_value(n)
    folded = _fold(n)
    if folded in ("unico", "unica"):
        return folded.replace("u", "ú", 1) + suffix, None
    if folded == "preliminar":
        return "preliminar", 0
    if re.fullmatch(r"[A-Z]", n):
        return n + suffix, ord(n) - ord("A") + 1
    value = ordinal_value(n) or cardinal_value(n)
    if value is None:
        return None
    return (_to_roman(value) if roman else str(value)) + suffix, value


def _numbered(m: re.Match[str], *, roman: bool) -> tuple[str, int | None, str, str] | None:
    """(num, value, rest, words) from a heading match. A second word that is not
    part of the number goes back to the rest."""
    n, x, rest = m.group("n"), m.group("x") or "", m.group("rest").strip()
    parsed = _number(n, x, roman=roman)
    if parsed is None and " " in n:
        first, second = n.split(None, 1)
        parsed = _number(first, "", roman=roman)
        if parsed is not None:
            n, rest = first, f"{second}{x}{m.group('sep') or ''} {rest}".strip()
    if parsed is None:
        return None
    return parsed[0], parsed[1], rest, f"{n}{x}"


# --- items -----------------------------------------------------------------


@dataclass
class _Item:
    el: etree._Element
    cls: str
    text: str
    quoted: bool = False
    kind: str = "text"  # text|table|article|disposition|group|struct|annex|title|signature
    num: str = ""
    value: int | None = None
    heading: str = ""
    level: str = ""
    words: str = ""
    drop: int = 0  # leading characters a heading line spends on its label
    # The heading line carries the provision's first sentence ("Primera.- Text").
    inline_body: bool = False


def _plain(el: etree._Element) -> str:
    return " ".join("".join(el.itertext()).split())


def _flatten(texto: etree._Element) -> list[_Item]:
    items: list[_Item] = []
    for el in texto:
        if not isinstance(el.tag, str):
            continue
        if el.tag == "blockquote":
            for child in el:
                if isinstance(child.tag, str) and _plain(child):
                    items.append(_Item(child, child.get("class") or "", _plain(child), True))
            continue
        item = _Item(el, el.get("class") or "", _plain(el))
        if el.tag == "table":
            item.kind = "table"
        m = _MARKER.match(item.text)
        if m:
            # An editorial cue such as `[precepto]`; `[ignorar]` marks quoted text.
            item.text, item.drop = item.text[m.end() :], m.end()
            item.quoted = m.group("m") == "ignorar"
        if item.text or item.kind == "table":
            items.append(item)
    return items


def _is_article_class(cls: str) -> bool:
    low = cls.lower()
    return "articulo" in low and not low.startswith("sangrado")


def _is_heading_class(cls: str) -> bool:
    return bool(cls) and not cls.lower().startswith(_BODY_CLASSES)


def _quote_delta(text: str) -> int:
    return text.count("«") + text.count("“") - text.count("»") - text.count("”")


def _quote_left_open(items: list[_Item], start: int, depth: int) -> bool:
    """Whether a quote open at `start` stays open up to the next real article,
    so it was never closed rather than holding this heading."""
    for it in items[start:]:
        if "articulo" in it.cls.lower():
            return _is_article_class(it.cls)
        depth = max(0, depth + _quote_delta(it.text))
        if depth == 0:
            return False
    return False


def _classify(items: list[_Item]) -> None:
    """Mark heads in place. A heading quoted for amendment stays text."""
    depth = 0
    group: str | None = None
    group_last = 0
    for i, it in enumerate(items):
        opens_quote = it.text.startswith(("«", "“"))
        if it.kind == "table" or it.quoted:
            it.quoted = True
        elif it.cls.lower().startswith("firma"):
            it.kind = "signature"
        elif (_is_article_class(it.cls) or it.cls == "anexo_num") and _head(
            it, group, group_last, strict=False
        ):
            depth = 0
        elif depth == 0 and not opens_quote and _head(it, group, group_last, strict=True):
            pass
        elif (
            depth > 0
            and not opens_quote
            and _is_heading_class(it.cls)
            and _quote_left_open(items, i, depth)
        ):
            # Unbalanced quotes upstream; a real article follows, so this heads.
            if _struct_head(it):
                depth = 0
            else:
                it.quoted = True
        else:
            it.quoted = depth > 0 or opens_quote
        if it.kind == "group":
            group, group_last = it.level, 0
        elif it.kind == "disposition" and _fold(it.text).startswith("disposici"):
            group = None  # a fully named disposition ends implicit numbering
        elif it.kind == "disposition":
            group_last = it.value if it.value is not None else group_last + 1
        elif it.kind in ("article", "struct", "annex"):
            group = None
        if it.kind != "table":
            depth = max(0, depth + _quote_delta(it.text))
    if depth > 0:
        # An unclosed quote may have hidden later headings in body classes.
        logger.warning("boe_quote_unclosed", depth=depth)


def _struct_head(it: _Item) -> bool:
    m = _STRUCT.match(it.text)
    if not m:
        return False
    level = _LEVELS[_fold(m.group("kw"))]
    parsed = _numbered(m, roman=_RANK[level] < 3)
    if parsed is None:
        return False
    it.kind, it.level = "struct", level
    it.num, it.value, it.heading, it.words = parsed
    return True


def _head(it: _Item, group: str | None, group_last: int, *, strict: bool) -> bool:
    """Classify a candidate heading line. `strict` is for body-class lines, which
    must hold the heading alone, or `.-` and the provision's first sentence."""
    text = it.text
    if strict and it.cls.lower().startswith("sangrado"):
        return False
    heading_class = _is_heading_class(it.cls)

    def accept(m: re.Match[str], kind: str, level: str, *, roman: bool = False) -> bool:
        dashed = re.search(r"[-–—]", m.group("sep") or "") is not None
        if strict and m.group("rest").strip() and not dashed:
            return False
        parsed = _numbered(m, roman=roman) if m.group("n") else ("", None, m.group("rest"), "")
        if parsed is None:
            return False
        it.kind, it.level = kind, level
        it.num, it.value, it.heading, it.words = parsed
        # Only a body-class line runs on into its text; a heading class keeps a heading.
        it.inline_body = strict and dashed and bool(it.heading)
        return True

    m = _ARTICLE.match(text)
    if m and accept(m, "article", ""):
        return True
    m = _DISPOSITION.match(text)
    if m and accept(m, "disposition", _fold(m.group("kind"))):
        return True
    if heading_class or text.isupper():
        m = _GROUP.match(text)
        if m and accept(m, "group", _fold(m.group("kind"))):
            return True
    if group is not None:
        m = _ORDINAL_HEAD.match(text)
        if m and _fold(m.group("n")).endswith("a") and accept(m, "disposition", group):
            if it.value is None or it.value == group_last + 1:
                return True
            it.kind, it.quoted = "text", False
    if heading_class or (text.isupper() and len(text) < 120):
        if _struct_head(it):
            return True
        m = _ANNEX.match(text)
        if m and (it.cls.lower().startswith("anexo") or not m.group("rest").strip()):
            if accept(m, "annex", "", roman=True):
                return True
    if it.cls.lower().endswith("_tit"):
        it.kind = "title"
        return True
    return False


# --- inline text -----------------------------------------------------------

_INLINE = {"sup": "sup", "sub": "sub", "em": "i", "i": "i", "strong": "b", "b": "b", "u": "u"}


def _ws(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "")


def _copy_inline(src: etree._Element, dst: etree._Element) -> None:
    def append(chunk: str) -> None:
        if not chunk:
            return
        if len(dst) == 0:
            dst.text = (dst.text or "") + chunk
        else:
            dst[-1].tail = (dst[-1].tail or "") + chunk

    append(_ws(src.text))
    for child in src:
        if not isinstance(child.tag, str):
            append(_ws(child.tail))
            continue
        tag = _INLINE.get(child.tag)
        if child.tag == "a" and child.get("href"):
            ref = _sub(dst, "ref", href=child.get("href", ""))
            ref.text = _plain(child)
        elif tag is not None:
            _copy_inline(child, _sub(dst, tag))
        elif child.tag == "br":
            _sub(dst, "eol")
        else:
            append(" " + _plain(child) + " ")
        append(_ws(child.tail))


def _p(parent: etree._Element, item: _Item, *, strip: int = 0) -> None:
    """One source paragraph as `<p>`, its first `strip` characters dropped."""
    p = _sub(parent, "p")
    if item.el.tag == "table":
        p.text = item.text
        return
    _copy_inline(item.el, p)
    lead = (p.text or "").lstrip()
    if strip and len(lead) >= strip:
        p.text = lead[strip:].lstrip()
    elif strip:
        for child in list(p):
            p.remove(child)
        p.text = _plain(item.el)[strip:].lstrip()
    else:
        p.text = lead
    last = p[-1] if len(p) else None
    if last is not None:
        last.tail = (last.tail or "").rstrip()
    else:
        p.text = (p.text or "").rstrip()


def _table(parent: etree._Element, src: etree._Element) -> None:
    table = _sub(parent, "table")
    for tr in src.iter("tr"):
        row = _sub(table, "tr")
        for cell in tr:
            if cell.tag not in ("td", "th"):
                continue
            attrs = {k: v for k, v in cell.attrib.items() if k in ("rowspan", "colspan")}
            out = _sub(row, cell.tag, **attrs)
            children = [c for c in cell if isinstance(c.tag, str)]
            only_p = children and all(c.tag == "p" for c in children)
            paras = children if only_p else [cell]
            for para in paras:
                p = _sub(out, "p")
                p.text = _plain(para)
        if len(row) == 0:
            table.remove(row)
    if len(table) == 0:
        parent.remove(table)
        _sub(parent, "p").text = _plain(src)


def _block(parent: etree._Element, item: _Item, *, strip: int = 0) -> None:
    if item.kind == "table":
        _table(parent, item.el)
    else:
        _p(parent, item, strip=item.drop + strip)


# --- subdivision of a provision ------------------------------------------


def _letter_value(token: str) -> int | None:
    letters = "abcdefghijklmnñopqrstuvwxyz"
    return letters.index(token) + 1 if len(token) == 1 and token in letters else None


# (pattern, element, value of the label) from outermost to innermost.
_SUBDIVISIONS: tuple[tuple[re.Pattern[str], str, Callable[[str], int | None]], ...] = (
    (re.compile(r"^(\d{1,3})\.(?![º°ª\d])\s+"), "paragraph", int),
    (re.compile(rf"^({_WORD}(?: y {_WORD})?)\.\s*-?\s+"), "paragraph", cardinal_value),
    (re.compile(rf"^({_WORD}(?:\s{_WORD})?)\.\s*-?\s+"), "paragraph", ordinal_value),
    (re.compile(r"^([a-zñ])\)\s*"), "point", _letter_value),
    (re.compile(r"^(\d{1,3})\.?\s?[º°ª]\.?\s*"), "point", int),
)


def _markers(blocks: list[_Item], level: int) -> list[tuple[int, str, str, int]] | None:
    """(index, eId token, marker, marker length) per run marker, or None when the
    level does not divide these blocks. Labels must count up from one."""
    pattern, _, value_of = _SUBDIVISIONS[level]
    found: list[tuple[int, str, str, int]] = []
    expected = 1
    for i, b in enumerate(blocks):
        if b.kind == "table" or b.quoted:
            continue
        m = pattern.match(b.text)
        if not m:
            continue
        value = value_of(m.group(1))
        if value == expected:
            token = m.group(1) if value_of is _letter_value else str(value)
            found.append((i, token, m.group().strip().rstrip("-").strip(), m.end()))
            expected += 1
    return found or None


def _subdivide(
    parent: etree._Element, eid: str, blocks: list[_Item], levels: range, *, lead: int = 0
) -> None:
    """Fill a provision from its blocks, splitting on the first level that divides
    them. `lead` drops characters from the first block (its own marker)."""
    for level in levels:
        found = _markers(blocks[1:] if lead else blocks, level)
        if not found:
            continue
        offset = 1 if lead else 0
        found = [(i + offset, token, marker, end) for i, token, marker, end in found]
        element = _SUBDIVISIONS[level][1]
        prefix = "para" if element == "paragraph" else "point"
        if found[0][0] > 0:
            intro = _sub(parent, "intro")
            for j, b in enumerate(blocks[: found[0][0]]):
                _block(intro, b, strip=lead if j == 0 else 0)
        for k, (start, token, marker, end) in enumerate(found):
            stop = found[k + 1][0] if k + 1 < len(found) else len(blocks)
            child_eid = f"{eid}__{prefix}_{token}"
            child = _sub(parent, element, eId=child_eid)
            _sub(child, "num").text = marker
            _subdivide(
                child, child_eid, blocks[start:stop], range(level + 1, len(_SUBDIVISIONS)), lead=end
            )
        return
    content = _sub(parent, "content")
    for j, b in enumerate(blocks):
        _block(content, b, strip=lead if j == 0 else 0)
    if len(content) == 0:
        _sub(content, "p")


# --- assembly --------------------------------------------------------------

_DISP_ABBREV = {"adicional": "da", "transitoria": "dt", "derogatoria": "dd", "final": "df"}
_PREFIX = {"chapter": "chp", "section": "sec", "subsection": "subsec"}


def _first_sentence(it: _Item) -> _Item:
    """The text after `Primera.-` on a heading line, as a block of its own."""
    return _Item(it.el, it.cls, it.heading, drop=it.drop + len(it.text) - len(it.heading))


class _Builder:
    """Assemble heads and blocks into a body, or an annex's main body."""

    def __init__(self, root: etree._Element, prefix: str, *, loose_as_p: bool) -> None:
        self.root = root
        self.prefix = prefix
        self.loose_as_p = loose_as_p
        self.stack: list[tuple[int, etree._Element, str]] = []
        self.unit: tuple[etree._Element, str, list[_Item]] | None = None
        self.titled: etree._Element | None = None
        self.group: str | None = None
        self.loose = 0

    def _parent(self) -> tuple[etree._Element, str]:
        if self.stack:
            return self.stack[-1][1], self.stack[-1][2] + "__"
        return self.root, self.prefix

    def _close_unit(self) -> None:
        if self.unit is not None:
            el, eid, blocks = self.unit
            _subdivide(el, eid, blocks, range(len(_SUBDIVISIONS)))
            self.unit = None

    def _open_unit(
        self, parent: etree._Element, tag: str, eid: str, num: str, it: _Item, **attrs: str
    ) -> None:
        self._close_unit()
        el = _sub(parent, tag, eId=eid, **attrs)
        _sub(el, "num").text = num
        if it.heading and not it.inline_body:
            _sub(el, "heading").text = it.heading.rstrip(".").strip()
        self.unit = (el, eid, [_first_sentence(it)] if it.inline_body else [])
        self.titled = None

    def add(self, it: _Item) -> None:
        if it.kind == "struct":
            self._close_unit()
            rank = _RANK[it.level]
            while self.stack and self.stack[-1][0] >= rank:
                self.stack.pop()
            parent, base = self._parent()
            eid = f"{base}{_PREFIX.get(it.level, it.level)}_{_eid_token(it.num)}"
            el = _sub(parent, it.level, eId=eid)
            _sub(el, "num").text = it.num
            if it.heading:
                _sub(el, "heading").text = it.heading.rstrip(".").strip()
            self.stack.append((rank, el, eid))
            self.titled = None if it.heading else el
            self.group = None
        elif it.kind == "title" and self.titled is not None:
            _sub(self.titled, "heading").text = it.text.rstrip(".").strip()
            self.titled = None
        elif it.kind == "article":
            parent, base = self._parent()
            self._open_unit(parent, "article", f"{base}art_{_eid_token(it.num)}", it.num, it)
        elif it.kind in ("group", "disposition"):
            self._close_unit()
            self.stack.clear()
            self.group = it.level
            if it.kind == "disposition" or it.num:
                self._disposition(it)
        elif self.unit is not None:
            self.unit[2].append(it)
        elif self.group is not None:
            # A plural group heading followed directly by text: one unnumbered disposition.
            self._disposition(_Item(it.el, it.cls, "", kind="disposition", level=self.group))
            self.add(it)
        else:
            self._loose(it)

    def _disposition(self, it: _Item) -> None:
        kind = it.level
        token = _eid_token(it.num) if it.num else "unica"
        eid = f"{self.prefix}hcontainer_{_DISP_ABBREV[kind]}-{token}"
        label = f"Disposición {kind} {it.words.lower()}".strip()
        self._open_unit(self.root, "hcontainer", eid, label, it, name=f"disposicion-{kind}")

    def _loose(self, it: _Item) -> None:
        parent, base = self._parent()
        self.titled = None
        if self.loose_as_p and not self.stack:
            _block(parent, it)
            return
        self.loose += 1
        hc = _sub(parent, "hcontainer", name="text", eId=f"{base}hcontainer_{self.loose}")
        _block(_sub(hc, "content"), it)

    def finish(self) -> None:
        self._close_unit()


# --- the document ----------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _fold(text)).strip("-") or "boe"


def _identification(
    parent: etree._Element,
    *,
    work: str,
    expression: str,
    date: str,
    author: str,
    language: str,
    number: str = "",
    suffix: str = "",
) -> None:
    ident = _sub(parent, "identification", source="#codify")
    w = _sub(ident, "FRBRWork")
    _sub(w, "FRBRthis", value=f"{work}{suffix}")
    _sub(w, "FRBRuri", value=work)
    _sub(w, "FRBRdate", date=date, name="enacted")
    _sub(w, "FRBRauthor", href=f"#{author}")
    _sub(w, "FRBRcountry", value="es")
    if number:
        _sub(w, "FRBRnumber", value=number)
    e = _sub(ident, "FRBRExpression")
    _sub(e, "FRBRthis", value=f"{expression}{suffix}")
    _sub(e, "FRBRuri", value=expression)
    _sub(e, "FRBRdate", date=date, name="expression")
    _sub(e, "FRBRauthor", href=f"#{author}")
    _sub(e, "FRBRlanguage", language=language)
    m = _sub(ident, "FRBRManifestation")
    _sub(m, "FRBRthis", value=f"{expression}.akn{suffix}")
    _sub(m, "FRBRuri", value=f"{expression}.akn")
    _sub(m, "FRBRdate", date=date, name="generation")
    _sub(m, "FRBRauthor", href="#codify")


def _split(items: list[_Item]) -> tuple[list[_Item], list[_Item], list[_Item], list[list[_Item]]]:
    """(preamble, body, conclusions, annexes)."""
    heads = ("article", "disposition", "group", "struct")
    start = next((i for i, it in enumerate(items) if it.kind in heads), None)
    if start is None:
        start = next((i for i, it in enumerate(items) if it.kind == "annex"), len(items))
        # Nothing structured: everything after any formula is body text.
        formula = [i for i, it in enumerate(items[:start]) if _FORMULA.match(it.text)]
        start = formula[-1] + 1 if formula else 0
    sig = next((i for i in range(start, len(items)) if items[i].kind == "signature"), None)
    annex = next((i for i in range(start, len(items)) if items[i].kind == "annex"), None)
    end = min(x for x in (sig, annex, len(items)) if x is not None)
    last_head = max((i for i in range(start, end) if items[i].kind in heads), default=start)
    close = next(
        (
            i
            for i in range(last_head + 1, end)
            if not items[i].quoted and _CLOSING.match(items[i].text)
        ),
        end,
    )
    tail_start = end
    if sig is not None and (annex is None or sig < annex):
        tail_start = sig
        while tail_start < len(items) and items[tail_start].kind == "signature":
            tail_start += 1
    conclusions = items[close:tail_start]
    rest = items[tail_start:]
    annexes: list[list[_Item]] = []
    for it in rest:
        if it.kind == "annex" or not annexes:
            annexes.append([])
        annexes[-1].append(it)
    return items[:start], items[start:close], conclusions, annexes


def boe_to_akn(
    source_xml: str | bytes, *, frbr_work_uri: str | None = None, language: str = "spa"
) -> tuple[str, dict[str, Any]]:
    """BOE Diario Oficial XML to canonical AKN 3.0; returns (akn_xml, metadata).
    `frbr_work_uri` overrides the URI derived from the item's metadata."""
    doc = parse_xml(source_xml, huge_tree=True)
    meta = read_metadata(doc)
    texto = doc.find("texto")
    items = _flatten(texto) if texto is not None else []
    if not any(it.kind == "text" for it in items):
        raise BoeTextMissing(f"BOE item {meta.get('identificador')!r} carries no text")
    _classify(items)
    work = frbr_work_uri or boe_work_uri(meta)
    doctype, number, date = item_doctype(meta), item_number(meta), item_date(meta)
    year = date[:4]
    expression = f"{work}/{language}@{date}"
    title = " ".join(meta.get("titulo", "").split()).rstrip(".")
    author_name = meta.get("departamento") or GAZETTE_NAME
    author = _slug(author_name)

    root = etree.Element(_q("akomaNtoso"), nsmap={None: AKN_NS})
    act = _sub(root, "act", name=doctype)
    meta_el = _sub(act, "meta")
    _identification(
        meta_el,
        work=work,
        expression=expression,
        date=date,
        author=author,
        language=language,
        number=number,
    )
    pub_date = iso_date(meta.get("fecha_publicacion", "")) or date
    issue = meta.get("diario_numero", "")
    _sub(
        meta_el,
        "publication",
        date=pub_date,
        name="BOE",
        showAs=f"BOE núm. {issue}, de {pub_date}" if issue else f"BOE, de {pub_date}",
        **({"number": issue} if issue else {}),
    )
    refs = _sub(meta_el, "references", source="#codify")
    _sub(refs, "TLCOrganization", eId="codify", href="/ontology/org/codify", showAs="Codify")
    _sub(refs, "TLCOrganization", eId=author, href=f"/ontology/org/es/{author}", showAs=author_name)

    preface = _sub(act, "preface")
    _sub(_sub(preface, "longTitle"), "p").text = title or meta.get("identificador", "")

    preamble_items, body_items, conclusion_items, annexes = _split(items)
    if preamble_items:
        preamble = _sub(act, "preamble")
        formula: etree._Element | None = None
        for it in preamble_items:
            if it.kind != "table" and _FORMULA.match(it.text):
                if formula is None:
                    formula = _sub(preamble, "formula", name="enactingFormula")
                _block(formula, it)
            else:
                formula = None
                _block(preamble, it)

    body = _sub(act, "body")
    builder = _Builder(body, "", loose_as_p=False)
    for it in body_items:
        builder.add(it)
    builder.finish()
    if len(body) == 0:
        hc = _sub(body, "hcontainer", name="text", eId="hcontainer_1")
        _sub(_sub(hc, "content"), "p")

    if conclusion_items:
        conclusions = _sub(act, "conclusions")
        for it in conclusion_items:
            _block(conclusions, it)

    if annexes:
        attachments = _sub(act, "attachments")
        for index, annex_items in enumerate(annexes, start=1):
            att_eid = f"att_{index}"
            att = _sub(attachments, "attachment", eId=att_eid)
            annex_doc = _sub(att, "doc", name="annex")
            _identification(
                _sub(annex_doc, "meta"),
                work=work,
                expression=expression,
                date=date,
                author=author,
                language=language,
                suffix=f"/!{att_eid}",
            )
            head, *content = annex_items
            if head.kind == "annex":
                labels = [f"Anexo {head.num}".strip(), head.heading]
            elif _is_heading_class(head.cls):
                labels = [head.text]
            else:
                labels, content = [], annex_items
            while content and content[0].kind == "title":
                labels.append(content.pop(0).text)
            if any(labels):
                header = _sub(_sub(annex_doc, "preface"), "longTitle")
                for label in filter(None, labels):
                    _sub(header, "p").text = label
            main = _sub(annex_doc, "mainBody")
            annex_builder = _Builder(main, f"{att_eid}__", loose_as_p=True)
            for it in content:
                annex_builder.add(it)
            annex_builder.finish()
            if len(main) == 0:
                _sub(main, "p")

    xml = cast(str, etree.tostring(root, encoding="unicode", pretty_print=True))
    unique_xml, _ = ensure_unique_eids(xml, huge_tree=True)
    gazette_year = int(pub_date[:4]) if pub_date else int(year)
    metadata: dict[str, Any] = {
        "title": title,
        "number": number,
        "year": int(year),
        "date": date,
        "language": language,
        "doctype": doctype,
        "frbr_work_uri": work,
        "frbr_expression_uri": expression,
        "source_id": meta.get("identificador", ""),
        "rank": meta.get("rango", ""),
        "eli": meta.get("url_eli", ""),
        "gazette": {
            "name": GAZETTE_NAME,
            "date": pub_date,
            "number": issue,
            "year": gazette_year,
            "page": meta.get("pagina_inicial", ""),
        },
    }
    return unique_xml, metadata


def is_boe(source: Path | str | bytes) -> bool:
    """Whether the source is a BOE item: a `<documento>` whose first metadata
    names a `BOE-` identifier. Reads a bounded prefix only."""
    try:
        if isinstance(source, bytes):
            prefix = source[:65536]
        elif isinstance(source, str) and source.lstrip().startswith("<"):
            prefix = source[:65536].encode()
        else:
            path = Path(source)
            if len(str(source)) > 4096 or not path.is_file():
                return False
            with path.open("rb") as stream:
                prefix = stream.read(65536)
        if b"<!doctype" in prefix.lower():
            return False
        parser = etree.XMLPullParser(
            events=("start", "end"), resolve_entities=False, load_dtd=False, no_network=True
        )
        parser.feed(prefix)
        for event, el in parser.read_events():
            if event == "start" and el.getparent() is None:
                if el.tag != "documento":
                    return False
            elif event == "end" and el.tag == "identificador":
                return (el.text or "").strip().startswith("BOE-")
        return False
    except Exception:
        return False


async def ingest(
    source: Path | str,
    jurisdiction_code: str = "es",
    *,
    frbr_work_uri: str | None = None,
    language: str = "spa",
) -> AsyncIterator[IngestionEvent]:
    """Ingest one BOE XML item deterministically."""
    from codify.pipeline.enrich.validator import validate_akn as run_validator
    from codify.pipeline.formats.eu_directive import _on_the_cpu_pool

    try:
        raw = Path(source).read_bytes()
        akn_xml, metadata = await _on_the_cpu_pool(
            lambda: boe_to_akn(raw, frbr_work_uri=frbr_work_uri, language=language)
        )
        yield MetadataExtracted(metadata=metadata)
        yield Parsed(akn_xml_len=len(akn_xml))
        try:
            await _on_the_cpu_pool(lambda: validate_akn(akn_xml, huge_tree=True))
        except Exception as exc:
            yield Failed(stage="schema_validation", error=f"{type(exc).__name__}: {exc}")
            return
        issues = await _on_the_cpu_pool(
            lambda: run_validator(akn_xml, huge_tree=True, provenance="native")
        )
        for issue in issues:
            yield ValidationIssued(issue=issue)
        doc = parse_akn(akn_xml, huge_tree=True)
        logger.info("boe_ingested", jurisdiction=jurisdiction_code, frbr=doc.frbr_work_uri)
        yield Complete(document=doc, akn_xml=akn_xml)
    except Exception as exc:
        yield Failed(stage="boe", error=f"{type(exc).__name__}: {exc}")


__all__ = [
    "BoeTextMissing",
    "boe_to_akn",
    "cardinal_value",
    "ingest",
    "is_boe",
    "ordinal_value",
]
