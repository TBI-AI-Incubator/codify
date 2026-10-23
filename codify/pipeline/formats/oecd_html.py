"""Compendium body text → AKN, for OECD legal instruments.

The Compendium serves each instrument as one `<p>` per block. Four shapes
recur, and one segmenter reads them all by classifying each block:

- A Council act: `THE COUNCIL,`, `HAVING REGARD` citations and `RECOGNISING`
  recitals, the enacting line `On the proposal of the X Committee:`, then
  Roman-numbered operative sections headed by their verb (`I. AGREES`,
  `II. RECOMMENDS`, `III. INVITES`, `IV. INSTRUCTS`), numbered paragraphs
  continuing across sections, `i)` points and dashed indents.
- The same with lettered items `a)` straight under a section, and all-caps
  `SECTION 1. ...` lines between sections.
- A code or convention: `DECIDES:` or `Have agreed as follows:`, then
  `PART I`, `Article N` with a heading line, `a.` or `1.` paragraphs.
- A declaration: `WE, THE MINISTERS ...`, `WE RECOGNISE ...` paragraphs with
  bulleted indents under title-case headings, or `DECLARE:` then `I. That
  Adherents should:` sections.

Regular enough to segment deterministically, so an instrument takes the
validator and embedding without the LLM lane. Sections headed by a verb
whose duty falls on someone other than the Adherents (`INVITES`,
`INSTRUCTS`) carry `refersTo="#nonAdherentDuty"`, as do their paragraphs, in
either official language; a jurisdiction listing that value in
`pool_excluded_refers_to` keeps them out of the pool. An `Annex` line ends the
operative text: each annex becomes an attachment of its own.

The acquirer (`codify.acquisition.adapters.oecd.compendium`) wraps the body
in a head whose `<meta name="oecd.*">` tags name the instrument; this module
reads them back for the preface and the FRBR meta.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from lxml import etree, html

from codify.akn import Document, parse_akn, validate_akn
from codify.pipeline.events import (
    Complete,
    Failed,
    IngestionEvent,
    MetadataExtracted,
    Parsed,
    ValidationIssued,
)

logger = structlog.get_logger()

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_ROMAN = r"[IVXLC]+"
_CONTAINER = re.compile(rf"^(PART|CHAPTER|TITLE)\s+({_ROMAN}|\d+)\b\.?\s*(.*)$", re.DOTALL)
_ARTICLE = re.compile(
    r"^Article\s+(\d{1,3}[A-Za-z]?(?:\s?(?:bis|ter|quater))?|[IVXLC]+)\s*(?:[-:–—]\s*(.+))?$"
)
_SECTION = re.compile(rf"^({_ROMAN})\.\s+(.*)$", re.DOTALL)
_CAPS_RUN = re.compile(r"^([A-ZÀ-ÖØ-Þ]{3,}(?:\s+[A-ZÀ-ÖØ-Þ]{2,})*)\b")
_ANNEX = re.compile(
    rf"^(?:ANNEXE?|Annexe?|APPENDI(?:X|CE)|Appendi(?:x|ce))(?:\s+({_ROMAN}|\d+|[A-Z])\b)?\.?\s*(.*)$",
    re.DOTALL,
)
_NUM_PARA = re.compile(r"^(\d{1,3})\.\s+(.*)$", re.DOTALL)
_NUM_PAREN = re.compile(r"^(\d{1,3})\)\s+(.*)$", re.DOTALL)
_LETTER_PARA = re.compile(r"^([a-z])\.\s+(.*)$", re.DOTALL)
_POINT = re.compile(r"^([ivxl]{1,6}|[a-z])\)\s+(.*)$", re.DOTALL)
_INDENT = re.compile(r"^[-–—•●]\s*(.*)$", re.DOTALL)
_FORMULA = re.compile(
    r"^On the proposal of\b.*:$|^HAS (?:ADOPTED|DECIDED)\b.*:$|^Have agreed as follows:$"
    r"|^(?:DECIDES|DECLARES?|AGREES?|RECOMMENDS):$",
    re.IGNORECASE,
)
_CITATION = re.compile(r"^HAVING REGARD\b")
_WE_PARA = re.compile(r"^(?:WE|NOUS)\b")
# Openers that name the enacting body; kept as a preamble line, not a recital.
_OPENER = re.compile(
    r"^(?:(?:THE COUNCIL|THE PARTIES|ADHERENTS|THE GOVERNMENTS"
    r"|LE CONSEIL|LES PARTIES|LES GOUVERNEMENTS)\b|(?:WE|NOUS),"
    r"|(?:WE|NOUS)\s+(?:THE\s+|LES\s+)?(?:MINIST|REPRESENT|REPRÉSENT))"
)
# A quotation mark opening an excerpt that starts on a numbered unit.
_QUOTED_UNIT = re.compile(r"^[“\"«‘]\s*(?=(?:[IVXLC]+|\d+)\.\s)")
_ALL_CAPS = re.compile(r"^[A-Z0-9][A-Z0-9 ,.;:'’()\-–—/&]+$")
_HEADING_WORDS = 14

# Verbs that open an operative section, English then French. A preamble verb
# (RECOGNISING, NOTANT) is a participle and never in this set, so a recital
# never opens the body.
OPERATIVE_VERBS = frozenset(
    {
        "AGREES", "RECOMMENDS", "DECIDES", "DECLARES", "INVITES", "INSTRUCTS",
        "ENCOURAGES", "REQUESTS", "CALLS ON", "CALLS UPON", "URGES", "COMMITS", "RESOLVES",
        "ADOPTS", "APPROVES", "WELCOMES", "NOTES", "CONFIRMS", "REAFFIRMS",
        "UNDERLINES",
        "CONVIENT", "RECOMMANDE", "DÉCIDE", "DÉCLARE", "INVITE", "CHARGE",
        "ENCOURAGE", "DEMANDE", "APPELLE", "EXHORTE", "ADOPTE", "APPROUVE",
        "SALUE", "PREND NOTE", "CONFIRME", "RÉAFFIRME", "SOULIGNE",
        # A declaration speaks in the first or third person plural.
        "AGREE", "RECOMMEND", "DECIDE", "DECLARE", "INSTRUCT", "REQUEST", "CALL ON",
        "CALL UPON", "URGE", "COMMIT", "RESOLVE", "ADOPT", "APPROVE", "WELCOME",
        "CONFIRM", "REAFFIRM", "UNDERTAKE", "ENDORSE",
        "DÉCLARENT", "DÉCLARONS", "CONVIENNENT", "CONVENONS", "DÉCIDENT", "DÉCIDONS",
        "RECOMMANDENT", "RECOMMANDONS", "ADOPTENT", "ADOPTONS", "APPROUVENT",
        "INVITENT", "INVITONS", "CHARGENT", "CHARGEONS", "DEMANDENT", "DEMANDONS",
        "APPELLENT", "APPELONS", "ENCOURAGENT", "ENCOURAGEONS", "RÉAFFIRMENT",
        "RÉAFFIRMONS", "SALUENT", "ACKNOWLEDGE", "UNDERLINE", "EXPECT",
        # Ministerial conclusions report in the past tense.
        "WELCOMED", "RECOGNISED", "NOTED", "SUPPORTED", "ACKNOWLEDGED", "AGREED",
        "DECIDED", "DECLARED", "ENDORSED", "ADOPTED", "REAFFIRMED", "CONFIRMED",
        "STRESSED", "UNDERLINED", "COMMITTED", "URGED", "INVITED", "REQUESTED",
        "CALLED ON", "CALLED UPON", "INSTRUCTED", "ENCOURAGED",
    }
)  # fmt: skip
# Verbs that may put a duty on a body other than the Adherents; the addressee decides.
NON_ADHERENT_VERBS = frozenset(
    {
        "INVITES", "INSTRUCTS", "ENCOURAGES", "REQUESTS", "CALLS ON", "CALLS UPON",
        "INVITE", "INSTRUCT", "REQUEST", "CALL ON", "CALL UPON",
        "CHARGE", "ENCOURAGE", "DEMANDE", "APPELLE",
        "INVITENT", "INVITONS", "CHARGENT", "CHARGEONS", "DEMANDENT", "DEMANDONS",
        "APPELLENT", "APPELONS", "ENCOURAGENT", "ENCOURAGEONS",
        "INVITED", "REQUESTED", "CALLED ON", "CALLED UPON", "INSTRUCTED", "ENCOURAGED",
    }
)  # fmt: skip
NON_ADHERENT_DUTY = "#nonAdherentDuty"
# The acquirer's two-letter `oecd.lang` → the ISO 639-3 code an expression carries.
_WRAPPER_LANGUAGES = {"en": "eng", "fr": "fra"}
# The addressee runs to the infinitive (`to`, `à`, or `de`/`d’` before a verb in
# -er, -ir, -re or -oir, so `Comité de la gouvernance` stays whole) or a list's colon.
_ADDRESSEE_END = re.compile(r"\s(?:to|à)\s|\s(?:de\s+|d[’']\s*)(?=\w+(?:er|ir|re)\b)|:")
# The declarants as the subject of a section's items.
_DECLARANTS = re.compile(r"\b(?:we|they|nous|ils)\b", re.IGNORECASE)
# Non-Members or non-Adherents named alongside the Adherents make it their duty.
_NON_ADHERENTS = re.compile(
    r"\bnon[-‐\s]+(?:OECD[-‐\s]+)?(?:Adherents?|Members?|Adhérents?|Membres?)\b", re.IGNORECASE
)
# Encouraging an activity names no addressee, so an item opening on it is never marked.
_ENCOURAGING = frozenset({"ENCOURAGES", "ENCOURAGE", "ENCOURAGENT", "ENCOURAGEONS", "ENCOURAGED"})
# Adherents or Members, in either language, but not non-Adherents.
_ADHERENTS = re.compile(r"(?<![\w‐-])(?:Adherents?|Members?|Adhérents?|Membres?)\b")

# Containers nest in this order; a code may skip a level but never inverts one.
_CONTAINER_KIND = {"PART": "part", "TITLE": "title", "CHAPTER": "chapter"}
_RANK = {
    "part": 0, "title": 1, "chapter": 2, "article": 3, "section": 3,
    "paragraph": 4, "point": 5, "indent": 6,
}  # fmt: skip
_HEADED = frozenset({"part", "title", "chapter", "article"})
_EID_PREFIX = {
    "part": "part",
    "title": "title",
    "chapter": "chp",
    "article": "art",
    "section": "sec",
    "paragraph": "para",
    "point": "point",
    "indent": "indent",
    "hcontainer": "hcontainer",
}


class OecdHtmlError(ValueError):
    """The HTML is not an instrument this converter can segment."""


@dataclass
class _Node:
    kind: str
    num: str  # printed number: `I.`, `Article 3`, `1.`, `a)`, `-`
    key: str  # eId leaf without the prefix: `I`, `3`, `1`, `a`, `1`
    eid: str = ""
    heading: str | None = None
    subheading: str | None = None
    text: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)
    non_adherent: bool = False
    name: str | None = None  # an `hcontainer`'s name


@dataclass
class _Annex:
    eid: str
    caption: str
    title: str | None = None
    body: list[_Node] = field(default_factory=list)


@dataclass
class _Instrument:
    meta: dict[str, str]
    label: str | None = None  # a `PREAMBLE` line naming the part it opens
    opener: str | None = None
    citations: list[str] = field(default_factory=list)
    recitals: list[list[str]] = field(default_factory=list)
    formula: str | None = None
    body: list[_Node] = field(default_factory=list)
    annexes: list[_Annex] = field(default_factory=list)


_SNIFF_BYTES = 4096


def is_oecd_instrument_html(source: Path | str) -> bool:
    """Whether the source opens with the head the acquirer wraps a body in: HTML
    text, or a local file of which only a bounded prefix is read."""
    if isinstance(source, str) and source.lstrip().startswith("<"):
        prefix = source[:_SNIFF_BYTES]
    else:
        try:
            with Path(source).open("rb") as stream:
                prefix = stream.read(_SNIFF_BYTES).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return False
    return 'name="oecd.key"' in prefix


def _blocks(text: str) -> tuple[list[str], dict[str, str]]:
    root = html.fromstring(text)
    meta = {
        (m.get("name") or "")[5:]: m.get("content") or ""
        for m in root.iter("meta")
        if (m.get("name") or "").startswith("oecd.")
    }
    title = root.find(".//title")
    if title is not None and title.text and "title" not in meta:
        meta["title"] = title.text.strip()
    body = root.find("body")
    if body is None:
        raise OecdHtmlError("no body in the HTML")
    for br in body.iter("br"):
        br.tail = " " + (br.tail or "")
    blocks: list[str] = []
    for el in body.iter("p", "li", "h1", "h2", "h3", "tr"):
        if any(a.tag in ("p", "li", "tr") for a in el.iterancestors()):
            continue
        if el.tag == "tr":
            cells = [" ".join(c.text_content().split()) for c in el if c.tag in ("td", "th")]
            joined = " | ".join(c for c in cells if c)
        else:
            joined = _QUOTED_UNIT.sub("", " ".join(el.text_content().split()))
        if joined:
            blocks.append(joined)
    return blocks, meta


def _operative(block: str) -> tuple[str, str] | None:
    """(verb, rest) when the block opens with an operative verb; longest match first."""
    m = _CAPS_RUN.match(block)
    if not m:
        return None
    words = m.group(1).split()
    for n in range(len(words), 0, -1):
        verb = " ".join(words[:n])
        if verb in OPERATIVE_VERBS:
            return verb, block[len(verb) :].strip()
    return None


def _is_heading(block: str) -> bool:
    """A short line with no sentence end: a heading for what follows."""
    return len(block.split()) <= _HEADING_WORDS and not block.rstrip().endswith((".", ";", ","))


def _is_unit(block: str) -> bool:
    """Whether the block opens a unit of its own: a container, article, section,
    numbered or lettered paragraph, point or indent, never a caption."""
    units = (_NUM_PARA, _NUM_PAREN, _LETTER_PARA, _POINT, _INDENT)
    return _starts_body(block) or any(p.match(block) for p in units)


def _roman_value(numeral: str) -> int:
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
    total = 0
    for here, after in zip(numeral, [*numeral[1:], ""], strict=True):
        value = values[here]
        total += -value if after and values[after] > value else value
    return total


def _follows(previous: str, current: str) -> bool:
    """Whether `current` is the item after `previous` in one list: `a` then `b`,
    or `iv` then `v`."""
    if len(previous) == 1 and len(current) == 1 and ord(current) == ord(previous) + 1:
        return True
    roman = re.compile(r"[ivxlc]+")
    if roman.fullmatch(previous) and roman.fullmatch(current):
        return _roman_value(current) == _roman_value(previous) + 1
    return False


def _numbered(node: _Node) -> bool:
    """A paragraph numbered `1.`, under which `a.` items are its points."""
    return node.kind == "paragraph" and node.num[:-1].isdigit() and node.num.endswith(".")


def _lead_in(node: _Node) -> bool:
    """Whether the node's own text ends in a colon, opening a list."""
    return bool(node.text) and node.text[-1].rstrip().endswith(":")


def _verb_paragraph(block: str) -> bool:
    """A numbered paragraph opening on an operative verb: `1. DECLARE that …`."""
    m = _NUM_PARA.match(block)
    if m is None or _CAPS_RUN.match(m.group(2)) is None:
        return False
    return _operative(m.group(2)) is not None or _lead_verb(m.group(2)) is not None


def _starts_body(block: str) -> bool:
    return bool(_CONTAINER.match(block) or _ARTICLE.match(block) or _SECTION.match(block)) or (
        _operative(block) is not None
    )


def _to_roman(n: int) -> str:
    out = ""
    for value, sym in ((50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += sym
            n -= value
    return out


class _Builder:
    """A stack of open nodes: each block opens a node at some rank or continues
    the deepest open one."""

    def __init__(self, scope: str = "") -> None:
        # An annex's nodes carry its eId as their prefix and bind no one by verb.
        self.scope = scope
        self.roots: list[_Node] = []
        self.stack: list[_Node] = []
        # Heading lines waiting for the unit they head; consecutive ones all wait.
        self.pending_headings: list[str] = []
        self.seen: dict[tuple[str, str], int] = {}
        self.prose_count = 0

    def _close_to(self, rank: int) -> None:
        while self.stack and _RANK[self.stack[-1].kind] >= rank:
            self.stack.pop()

    def _open(self, kind: str, num: str, key: str, heading: str | None = None) -> _Node:
        self._close_to(_RANK[kind])
        parent = self.stack[-1] if self.stack else None
        scope = parent.eid if parent else self.scope
        n = self.seen.get((scope, f"{kind}:{key}"), 0) + 1
        self.seen[(scope, f"{kind}:{key}")] = n
        # A repeated number keeps a unique eId, as a repeated article does.
        node = _Node(kind, num, key if n == 1 else f"{key}_dup{n}", heading=heading)
        leaf = f"{_EID_PREFIX[kind]}_{node.key}"
        node.eid = f"{scope}__{leaf}" if scope else leaf
        if parent is not None:
            parent.children.append(node)
        else:
            self.roots.append(node)
        self.stack.append(node)
        if self.pending_headings:
            lines = self.pending_headings
            if node.heading is None:
                node.heading, lines = lines[0], lines[1:]
            if lines:
                node.subheading = " / ".join(
                    [*([node.subheading] if node.subheading else []), *lines]
                )
            self.pending_headings = []
        return node

    def _nested_number(self, number: int) -> bool:
        """Whether `number.` continues a numbered list inside the open paragraph:
        `1.` under a numbered paragraph ending in a colon, or the next number of
        such a list already open. The outer sequence resumes at the lead-in's
        next number once the list's last item has closed with a full stop, and a
        heading between items ends the list."""
        if self.pending_headings:
            return False
        inner = next(
            (n for n in reversed(self.stack) if n.kind == "point" and n.num[:-1].isdigit()), None
        )
        outer = next((n for n in reversed(self.stack) if n.kind == "paragraph"), None)
        if inner is not None:
            resumes = (
                outer is not None and outer.num[:-1].isdigit() and number == int(outer.num[:-1]) + 1
            )
            closed = bool(inner.text) and inner.text[-1].rstrip().endswith(".")
            if resumes and (closed or number != int(inner.num[:-1]) + 1):
                return False
            return number == int(inner.num[:-1]) + 1
        return (
            number == 1
            and outer is not None
            and outer.num[:-1].isdigit()
            and int(outer.num[:-1]) > 1
            and _lead_in(outer)
        )

    def feed_prose(self, block: str) -> None:
        """Keep `block` as a paragraph of its own, whatever it looks like."""
        self.flush()
        self._open_prose_paragraph(block)

    def flush(self) -> None:
        """A heading left with nothing after it is kept as a paragraph of its own."""
        if self.pending_headings:
            block = " / ".join(self.pending_headings)
            self.pending_headings = []
            self._open_prose_paragraph(block)

    def _verb_may_open_section(self) -> bool:
        """No section or article is open, or the open one is itself a verb
        section, whose next verb starts a sibling rather than continuing it."""
        open_ = [n for n in self.stack if n.kind in ("section", "article")]
        return not open_ or (open_[-1].kind == "section" and open_[-1].heading in OPERATIVE_VERBS)

    def _open_prose_paragraph(self, block: str) -> None:
        self._close_to(_RANK["paragraph"])
        self.prose_count += 1
        # The source prints no number, so none is invented; the eId keeps the order.
        self._open("paragraph", "", str(self.prose_count)).text.append(block)

    def feed(self, block: str) -> None:
        m = _CONTAINER.match(block)
        if m:
            word, key, rest = m.groups()
            kind = _CONTAINER_KIND[word]
            self._open(kind, f"{word.title()} {key}", key.lower(), heading=rest.strip() or None)
            return
        m = _ARTICLE.match(block)
        if m:
            key = m.group(1).replace(" ", "").lower()
            self._open(
                "article", f"Article {m.group(1)}", key, heading=(m.group(2) or "").strip() or None
            )
            return
        m = _SECTION.match(block)
        if m:
            numeral, rest = m.groups()
            op = _operative(rest)
            verb, tail = op if op else (None, rest.strip())
            node = self._open("section", f"{numeral}.", numeral, heading=verb)
            if tail:
                node.text.append(tail)
            return
        op = _operative(block)
        if op and self._verb_may_open_section():
            verb, rest = op
            numeral = _to_roman(sum(1 for r in self.roots if r.kind == "section") + 1)
            node = self._open("section", f"{numeral}.", numeral, heading=verb)
            if rest:
                node.text.append(rest)
            return
        m = _NUM_PARA.match(block)
        if m and self._nested_number(int(m.group(1))):
            # `9. … should:` then `1.`, `2.`: a list inside paragraph 9, not paragraph 1.
            self._open("point", f"{m.group(1)}.", m.group(1)).text.append(m.group(2))
            return
        if m:
            self._open("paragraph", f"{m.group(1)}.", m.group(1)).text.append(m.group(2))
            return
        m = _LETTER_PARA.match(block)
        if m:
            # `a.` under an open `1.` is a point of it; straight under an article it
            # is the article's own paragraph.
            numbered = any(_numbered(n) for n in self.stack)
            kind = "point" if numbered and not self.pending_headings else "paragraph"
            self._open(kind, f"{m.group(1)}.", m.group(1)).text.append(m.group(2))
            return
        m = _NUM_PAREN.match(block)
        if m:
            self._open("paragraph", f"{m.group(1)})", m.group(1)).text.append(m.group(2))
            return
        m = _POINT.match(block)
        if m:
            # `a)` under an open `1.` or `a.` paragraph is a point; straight under
            # a section or article it is a paragraph, and the next `b)` its sibling.
            # A lead-in ending in a colon takes the list that follows, but not its
            # own next sibling (`a) … shall:` / `i)` / `ii)` / `b)`), and a heading
            # between items ends the list.
            item = next((n for n in reversed(self.stack) if n.kind == "paragraph"), None)
            sibling = (
                item is not None and item.num.endswith(")") and _follows(item.num[:-1], m.group(1))
            )
            under_dotted = any(
                n.kind == "paragraph" and (n.num.endswith(".") or _lead_in(n)) for n in self.stack
            )
            listed = under_dotted and not sibling and not self.pending_headings
            kind = "point" if listed else "paragraph"
            self._open(kind, f"{m.group(1)})", m.group(1)).text.append(m.group(2))
            return
        m = _INDENT.match(block)
        if m and self.stack:
            # A bullet is a child of the deepest non-indent node; bullets are siblings.
            self._close_to(_RANK["indent"])
            parent = self.stack[-1]
            nth = sum(1 for c in parent.children if c.kind == "indent") + 1
            self._open("indent", "-", str(nth)).text.append(m.group(1))
            return
        if _WE_PARA.match(block):
            self._open_prose_paragraph(block)
            return
        top = self.stack[-1] if self.stack else None
        # A container's or article's heading is the short line right after its number.
        if (
            top is not None
            and top.kind in _HEADED
            and not top.text
            and not top.children
            and top.heading is None
            and _is_heading(block)
        ):
            top.heading = block
            return
        # Under a lead-in ending in a colon, each sentence is an item of its own.
        section = next((n for n in reversed(self.stack) if n.kind == "section"), None)
        item = next((n for n in reversed(self.stack) if n.kind == "paragraph"), None)
        if (
            section is not None
            and _lead_in(section)
            and block[:1].isupper()
            and (not _is_heading(block) or block.rstrip().endswith(":"))
            and (top is section or (item is not None and not item.num))
        ):
            self._open_prose_paragraph(block)
            return
        # A short unpunctuated line is a heading for what follows; a prose
        # continuation ends in punctuation or runs longer.
        if _is_heading(block) and (top is None or top.text or _ALL_CAPS.match(block)):
            self.pending_headings.append(block)
            return
        # A heading waiting for its text, or a new sentence after a finished
        # unnumbered paragraph, opens the next paragraph rather than vanishing
        # into the one before it.
        prose = top is not None and top.kind == "paragraph" and not top.num
        finished = (
            prose
            and top is not None
            and bool(top.text)
            and top.text[-1].rstrip().endswith((".", "!", "?"))
        )
        new_sentence = finished and block[:1].isupper()
        if top is not None and not self.pending_headings and not (prose and new_sentence):
            top.text.append(block)
        else:
            self._open_prose_paragraph(block)


def _addressee(text: str) -> str | None:
    """Who a verb section's lead-in addresses, or None when it names no one."""
    lead = text.strip().lstrip("*").strip()
    m = _ADDRESSEE_END.search(lead)
    head = (lead[: m.start()] if m else lead).strip()
    return head or None


def _binds_adherents(addressee: str) -> bool:
    return bool(_ADHERENTS.search(addressee)) and not _NON_ADHERENTS.search(addressee)


def _mark_subtree(node: _Node) -> None:
    node.non_adherent = True
    for child in node.children:
        _mark_subtree(child)


def _lead_verb(text: str) -> tuple[str, str] | None:
    """A verb opening the text that may bind someone other than the Adherents,
    after a `WE`/`NOUS` subject if there is one, with the words that follow it."""
    words = re.sub(r"^(?:WE|NOUS(?:\s+NOUS)?)\s+", "", text.strip()).split()
    for n in (2, 1):
        verb = " ".join(words[:n]).upper().rstrip(",:")
        if verb in NON_ADHERENT_VERBS:
            return verb, " ".join(words[n:])
    return None


def _decide(node: _Node, lead: str) -> None:
    """Mark `node` when its lead-in addresses someone other than the Adherents; a
    lead-in naming no one leaves each item to name its own, and the node is
    marked only when every item is."""
    addressee = _addressee(lead)
    if addressee is not None:
        if not _binds_adherents(addressee):
            _mark_subtree(node)
        return
    for child in node.children:
        item = _addressee(child.text[0]) if child.text else None
        if item is not None and not _binds_adherents(item):
            _mark_subtree(child)
    node.non_adherent = bool(node.children) and all(c.non_adherent for c in node.children)


def _mark_non_adherent_duties(
    nodes: list[_Node], speaking: bool = False, adherents: bool = False
) -> None:
    """Mark the duties on someone other than the Adherents: a verb section, a
    paragraph opening on such a verb (`WE CALL ON …`, `5. CALL on …`), or an item
    under a section in the declarants' own voice (`DECLARE that we:` … `Call on
    the OECD to:`). Under a lead-in addressed to the Adherents (`RECOMMENDS that
    Adherents should:`), an item's verb is the Adherents' own act and stays."""
    for node in nodes:
        text = node.text[0] if node.text else ""
        if node.kind == "section" and node.heading in NON_ADHERENT_VERBS:
            _decide(node, text)
            continue
        if (
            node.kind == "paragraph"
            and not adherents
            and (
                _WE_PARA.match(text)
                or _CAPS_RUN.match(text) is not None
                or (speaking and text[:1].isupper())
            )
        ):
            lead = _lead_verb(text)
            # An item names its addressee before an infinitive or a colon
            # (`Call on the OECD to:`); `ENCOURAGE green investment` names none.
            if lead is not None and lead[0] not in _ENCOURAGING and _ADDRESSEE_END.search(lead[1]):
                _decide(node, lead[1])
                continue
        verb_section = node.kind == "section" and node.heading in OPERATIVE_VERBS
        # Items speak for the declarants only under `… that we:`.
        voiced = verb_section and _DECLARANTS.search(text) is not None
        addressed = adherents or (verb_section and _ADHERENTS.search(text) is not None)
        _mark_non_adherent_duties(node.children, voiced, addressed)


def _rescope(node: _Node, scope: str, group: str) -> None:
    """Move a node's eId, and its descendants', from under `scope` to under `group`."""
    tail = node.eid[len(scope) + 2 :] if scope else node.eid
    node.eid = f"{group}__{tail}"
    for child in node.children:
        _rescope(child, scope, group)


def _group_loose_paragraphs(
    nodes: list[_Node], scope: str = "", untitled: str = "section"
) -> list[_Node]:
    """Gather paragraphs lying straight under the body, a declaration's shape,
    into unnumbered sections that open at each heading line, so every paragraph
    sits in a container as the body's hierarchy expects. A group with no heading
    is an `untitled` container: an `hcontainer` where the text has articles,
    whose sections are expected to carry a title."""
    if not any(n.kind == "paragraph" for n in nodes):
        return nodes
    out: list[_Node] = []
    group: _Node | None = None
    for node in nodes:
        if node.kind != "paragraph":
            out.append(node)
            group = None
            continue
        if group is None or node.heading:
            kind = "section" if node.heading else untitled
            key = str(sum(1 for n in out if n.kind == kind and not n.num) + 1)
            leaf = f"{_EID_PREFIX[kind]}_{key}"
            eid = f"{scope}__{leaf}" if scope else leaf
            group = _Node(kind, "", key, eid=eid, heading=node.heading)
            group.name = "group" if kind == "hcontainer" else None
            node.heading = None
            out.append(group)
        _rescope(node, scope, group.eid)
        group.children.append(node)
    return out


def _has_articles(nodes: list[_Node]) -> bool:
    return any(n.kind == "article" or _has_articles(n.children) for n in nodes)


def _bare_caption(caption: str) -> bool:
    """`ANNEX` alone, with no number or title of its own."""
    m = _ANNEX.match(caption)
    return m is not None and m.group(1) is None and not m.group(2)


def _annex_caption(block: str) -> bool:
    """`Annex`, `ANNEXE II` or a short `Annex to the Recommendation` line."""
    m = _ANNEX.match(block)
    if m is None:
        return False
    return not m.group(2) or _is_heading(block)


def _segment(blocks: list[str], meta: dict[str, str]) -> _Instrument:
    if not blocks:
        raise OecdHtmlError("no text blocks in the HTML")
    inst = _Instrument(meta=meta)
    kept: set[int] = set()  # the blocks the preamble holds as opener or citation
    i = 0
    # Preamble runs to the enacting line, or to the first structural token when
    # the instrument has none (a declaration's first `WE ...` paragraph).
    while i < len(blocks):
        block = blocks[i]
        if _FORMULA.match(block):
            inst.formula = block
            i += 1
            break
        if _starts_body(block) or (_WE_PARA.match(block) and inst.opener) or _verb_paragraph(block):
            break
        if (
            inst.opener is None
            and not inst.citations
            and not inst.recitals
            and _OPENER.match(block)
        ):
            inst.opener = block
            kept.add(i)
        elif _CITATION.match(block):
            inst.citations.append(block)
            kept.add(i)
        elif _INDENT.match(block) and inst.recitals:
            inst.recitals[-1].append(block)  # a bulleted list under `CONSIDERING that:`
        elif block == "PREAMBLE" and inst.label is None:
            inst.label = block
            kept.add(i)
        else:
            inst.recitals.append([block])
        i += 1
    body_blocks = blocks[i:]
    if i == len(blocks):
        # No operative token anywhere: a text of numbered or plain paragraphs. The
        # body is all of it but the opener and citations, not a long preamble.
        body_blocks = [b for k, b in enumerate(blocks) if k not in kept]
        inst.recitals = []
    builder = _Builder()
    inst.body = builder.roots
    annex: _Annex | None = None
    previous = builder

    def fold_empty() -> None:
        # A caption with nothing under it (a contents line) is text, not an annex.
        if annex is not None and not annex.body:
            inst.annexes.remove(annex)
            previous.feed_prose(" / ".join(filter(None, [annex.caption, annex.title])))

    for block in body_blocks:
        # An annex ends whatever section was open, so its text inherits no verb.
        if _annex_caption(block):
            builder.flush()
            if (
                annex is not None
                and _bare_caption(annex.caption)
                and not builder.roots
                and annex.title is None
            ):
                annex.title = block  # `ANNEX` then `ANNEX TO THE RECOMMENDATION …`
                continue
            fold_empty()
            if annex is None or annex.body:
                previous = builder
            annex = _Annex(eid=f"att_{len(inst.annexes) + 1}", caption=block)
            inst.annexes.append(annex)
            builder = _Builder(scope=annex.eid)
            annex.body = builder.roots
            continue
        if (
            annex is not None
            and annex.title is None
            and not builder.roots
            and _is_heading(block)
            and not _is_unit(block)
        ):
            annex.title = block
            continue
        builder.feed(block)
    builder.flush()
    fold_empty()
    _mark_non_adherent_duties(inst.body)
    untitled = (
        "hcontainer"
        if _has_articles([*inst.body, *(n for a in inst.annexes for n in a.body)])
        else "section"
    )
    inst.body = _group_loose_paragraphs(inst.body, untitled=untitled)
    for annex in inst.annexes:
        annex.body = _group_loose_paragraphs(annex.body, annex.eid, untitled)
    if not inst.body:
        raise OecdHtmlError("no operative text: not an instrument body, or an empty page")
    return inst


def _akn(parent: etree._Element, tag: str, **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{AKN_NS}}}{tag}")
    for k, v in attrs.items():
        if v is not None:
            el.set(k, v)
    return el


def _emit(parent: etree._Element, node: _Node, counts: dict[str, int]) -> None:
    el = _akn(parent, node.kind, eId=node.eid)
    if node.name:
        el.set("name", node.name)
    if node.non_adherent:
        el.set("refersTo", NON_ADHERENT_DUTY)
        if node.kind == "paragraph":
            counts["non_adherent_paragraphs"] += 1
    counts[node.kind + "s"] = counts.get(node.kind + "s", 0) + 1
    if node.num:
        _akn(el, "num").text = node.num
    if node.heading:
        _akn(el, "heading").text = node.heading
    if node.subheading:
        _akn(el, "subheading").text = node.subheading
    if node.children:
        if node.text:
            intro = _akn(el, "intro")
            for block in node.text:
                _akn(intro, "p").text = block
        for child in node.children:
            _emit(el, child, counts)
    else:
        content = _akn(el, "content")
        for block in node.text or [""]:
            _akn(content, "p").text = block


def _meta(
    inst: _Instrument, *, frbr_work_uri: str, language: str, work_date: str, expression_date: str
) -> etree._Element:
    doctype = inst.meta.get("type") or frbr_work_uri.strip("/").split("/")[3]
    meta = etree.Element(f"{{{AKN_NS}}}meta")
    ident = _akn(meta, "identification", source="#codify")
    work = _akn(ident, "FRBRWork")
    _akn(work, "FRBRthis", value=f"{frbr_work_uri}/main")
    _akn(work, "FRBRuri", value=frbr_work_uri)
    _akn(work, "FRBRdate", date=work_date, name="adoption")
    _akn(work, "FRBRauthor", href="#oecdCouncil")
    _akn(work, "FRBRcountry", value="oecd")
    _akn(work, "FRBRsubtype", value=doctype)
    if inst.meta.get("key"):
        _akn(work, "FRBRnumber", value=inst.meta["key"])
    expr = _akn(ident, "FRBRExpression")
    _akn(expr, "FRBRthis", value=f"{frbr_work_uri}/{language}@{expression_date}/main")
    _akn(expr, "FRBRuri", value=f"{frbr_work_uri}/{language}@{expression_date}")
    expression_event = "adoption" if expression_date == work_date else "revision"
    _akn(expr, "FRBRdate", date=expression_date, name=expression_event)
    _akn(expr, "FRBRauthor", href="#oecdCouncil")
    _akn(expr, "FRBRlanguage", language=language)
    manif = _akn(ident, "FRBRManifestation")
    _akn(manif, "FRBRthis", value=f"{frbr_work_uri}/{language}@{expression_date}/main.xml")
    _akn(manif, "FRBRuri", value=f"{frbr_work_uri}/{language}@{expression_date}.xml")
    _akn(manif, "FRBRdate", date=expression_date, name="generation")
    _akn(manif, "FRBRauthor", href="#codify")
    _akn(manif, "FRBRformat", value="xml")
    lifecycle = _akn(meta, "lifecycle", source="#codify")
    _akn(
        lifecycle,
        "eventRef",
        eId="evt_adoption",
        date=work_date,
        type="generation",
        source="#oecdCouncil",
    )
    if expression_date != work_date:
        _akn(
            lifecycle,
            "eventRef",
            eId="evt_revision",
            date=expression_date,
            type="amendment",
            source="#oecdCouncil",
        )
    refs = _akn(meta, "references", source="#codify")
    _akn(
        refs,
        "TLCOrganization",
        eId="oecdCouncil",
        href="/ontology/org/oecd/council",
        showAs="OECD Council",
    )
    _akn(refs, "TLCOrganization", eId="codify", href="/ontology/org/codify", showAs="Codify")
    _akn(
        refs,
        "TLCConcept",
        eId="nonAdherentDuty",
        href="/ontology/concept/oecd/non-adherent-duty",
        showAs="Operative duty on a body other than the Adherents",
    )
    return meta


def build_body(inst: _Instrument, root_act: etree._Element) -> dict[str, int]:
    preface = _akn(root_act, "preface")
    _akn(_akn(preface, "longTitle"), "p").text = inst.meta.get("title") or inst.opener or ""
    if inst.label or inst.opener or inst.citations or inst.recitals or inst.formula:
        preamble = _akn(root_act, "preamble")
        if inst.label:
            _akn(preamble, "p").text = inst.label
        if inst.opener:
            _akn(preamble, "p").text = inst.opener
        if inst.citations:
            citations = _akn(preamble, "citations")
            for n, block in enumerate(inst.citations, start=1):
                _akn(_akn(citations, "citation", eId=f"citations__cit_{n}"), "p").text = block
        if inst.recitals:
            recitals = _akn(preamble, "recitals")
            for n, blocks in enumerate(inst.recitals, start=1):
                recital = _akn(recitals, "recital", eId=f"recitals__rec_{n}")
                for block in blocks:
                    _akn(recital, "p").text = block
        if inst.formula:
            _akn(_akn(preamble, "formula", name="enactingFormula"), "p").text = inst.formula
    body = _akn(root_act, "body")
    counts: dict[str, int] = {"non_adherent_paragraphs": 0}
    for node in inst.body:
        _emit(body, node, counts)
    return counts


def _attachment_meta(
    *, frbr_work_uri: str, language: str, expression_date: str, eid: str
) -> etree._Element:
    """The identification an annex's `<doc>` must carry, naming it as a component."""
    meta = etree.Element(f"{{{AKN_NS}}}meta")
    ident = _akn(meta, "identification", source="#codify")
    expression = f"{frbr_work_uri}/{language}@{expression_date}"
    for block, this, uri, author in (
        ("FRBRWork", f"{frbr_work_uri}/!{eid}", frbr_work_uri, "#oecdCouncil"),
        ("FRBRExpression", f"{expression}/!{eid}", expression, "#oecdCouncil"),
        ("FRBRManifestation", f"{expression}/!{eid}.xml", f"{expression}.xml", "#codify"),
    ):
        frbr = _akn(ident, block)
        _akn(frbr, "FRBRthis", value=this)
        _akn(frbr, "FRBRuri", value=uri)
        _akn(frbr, "FRBRdate", date=expression_date, name="expression")
        _akn(frbr, "FRBRauthor", href=author)
        if block == "FRBRWork":
            _akn(frbr, "FRBRcountry", value="oecd")
        elif block == "FRBRExpression":
            _akn(frbr, "FRBRlanguage", language=language)
        else:
            _akn(frbr, "FRBRformat", value="xml")
    return meta


def build_attachments(
    inst: _Instrument,
    root_act: etree._Element,
    *,
    frbr_work_uri: str,
    language: str,
    expression_date: str,
) -> int:
    """Each annex as an `<attachment>` holding a `<doc name="annex">`; the count emitted."""
    annexes = [a for a in inst.annexes if a.body]
    if not annexes:
        return 0
    attachments = _akn(root_act, "attachments")
    counts: dict[str, int] = {"non_adherent_paragraphs": 0}
    for annex in annexes:
        attachment = _akn(attachments, "attachment", eId=annex.eid)
        _akn(attachment, "heading").text = annex.caption
        if annex.title:
            _akn(attachment, "subheading").text = annex.title
        doc = _akn(attachment, "doc", name="annex")
        doc.append(
            _attachment_meta(
                frbr_work_uri=frbr_work_uri,
                language=language,
                expression_date=expression_date,
                eid=annex.eid,
            )
        )
        main = _akn(doc, "mainBody")
        for node in annex.body:
            _emit(main, node, counts)
    return len(annexes)


def instrument_html_to_akn(
    text: str,
    *,
    frbr_work_uri: str,
    language: str = "eng",
    provenance: dict[str, object] | None = None,
) -> str:
    """Deterministic Compendium HTML → AKN `act`: the adoption date is the work's,
    the latest revision's (`oecd.revised`) the expression's. The wrapper's
    `oecd.lang` names the body's language; `language` stands in where it is absent."""
    blocks, meta = _blocks(text)
    language = _WRAPPER_LANGUAGES.get(meta.get("lang", ""), language)
    inst = _segment(blocks, meta)
    adopted = meta.get("adopted")
    if not adopted:
        raise OecdHtmlError("no oecd.adopted meta: the acquirer wraps the body with it")
    # The latest amendment of the text dates the expression, so a revised text is
    # a new expression of the same work rather than a clash with the adopted one.
    revised = max(meta.get("revised") or adopted, adopted)
    root = etree.Element(f"{{{AKN_NS}}}akomaNtoso", nsmap={None: AKN_NS})
    doctype = meta.get("type") or frbr_work_uri.strip("/").split("/")[3]
    root_act = etree.SubElement(root, f"{{{AKN_NS}}}act", contains="originalVersion", name=doctype)
    root_act.append(
        _meta(
            inst,
            frbr_work_uri=frbr_work_uri,
            language=language,
            work_date=adopted,
            expression_date=revised,
        )
    )
    counts = build_body(inst, root_act)
    annexes = build_attachments(
        inst, root_act, frbr_work_uri=frbr_work_uri, language=language, expression_date=revised
    )
    if provenance is not None:
        provenance["html_source"] = {
            **counts,
            "annexes": annexes,
            "citations": len(inst.citations),
            "recitals": len(inst.recitals),
            "verbs": [n.heading for n in inst.body if n.kind == "section" and n.heading],
        }
    serialised: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    return serialised.decode("utf-8")


async def ingest(
    source: Path | str,
    jurisdiction_code: str = "oecd",
    *,
    frbr_work_uri: str | None = None,
    language: str = "eng",
) -> AsyncIterator[IngestionEvent]:
    """Ingest one wrapped Compendium body deterministically; the events the PDF lane emits."""
    from codify.pipeline.enrich.validator import validate_akn as run_validator
    from codify.pipeline.formats.eu_directive import _on_the_cpu_pool

    try:
        text = Path(source).read_text(encoding="utf-8")
        if frbr_work_uri is None:
            raise OecdHtmlError("frbr_work_uri is required: the acquirer mints it from the key")
        provenance: dict[str, object] = {}

        def convert() -> str:
            return instrument_html_to_akn(
                text, frbr_work_uri=frbr_work_uri, language=language, provenance=provenance
            )

        akn_xml = await _on_the_cpu_pool(convert)
        yield MetadataExtracted(metadata={"jurisdiction": jurisdiction_code, **provenance})
        yield Parsed(akn_xml_len=len(akn_xml))
        try:
            await _on_the_cpu_pool(validate_akn, akn_xml)
        except Exception as exc:
            yield Failed(stage="schema_validation", error=f"{type(exc).__name__}: {exc}")
            return
        run: Callable[[str], list[dict[str, Any]]] = run_validator
        try:
            for issue in await _on_the_cpu_pool(run, akn_xml):
                yield ValidationIssued(issue=issue)
        except Exception as exc:
            yield Failed(stage="validator", error=f"{type(exc).__name__}: {exc}")
            return
        doc: Document = parse_akn(akn_xml)
        yield Complete(document=doc, akn_xml=akn_xml)
    except Exception as exc:
        yield Failed(stage="oecd_html", error=f"{type(exc).__name__}: {exc}")


__all__ = [
    "NON_ADHERENT_DUTY",
    "NON_ADHERENT_VERBS",
    "OPERATIVE_VERBS",
    "OecdHtmlError",
    "ingest",
    "instrument_html_to_akn",
    "is_oecd_instrument_html",
]
