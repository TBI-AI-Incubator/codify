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
`INSTRUCTS`) carry `refersTo="#nonAdherentDuty"`, as do their paragraphs, so
a lens can leave them out of an alignment pool.

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
    r"^Article\s+(\d{1,3}[A-Za-z]?(?:\s?(?:bis|ter|quater))?)\s*(?:[-:–—]\s*(.+))?$"
)
_SECTION = re.compile(rf"^({_ROMAN})\.\s+(.*)$", re.DOTALL)
_CAPS_RUN = re.compile(r"^([A-Z]{3,}(?:\s+[A-Z]{2,})*)\b")
_NUM_PARA = re.compile(r"^(\d{1,3})\.\s+(.*)$", re.DOTALL)
_LETTER_PARA = re.compile(r"^([a-z])\.\s+(.*)$", re.DOTALL)
_POINT = re.compile(r"^([ivxl]{1,6}|[a-z])\)\s+(.*)$", re.DOTALL)
_INDENT = re.compile(r"^[-–—•●]\s*(.*)$", re.DOTALL)
_FORMULA = re.compile(
    r"^On the proposal of\b.*:$|^HAS (?:ADOPTED|DECIDED)\b.*:$|^Have agreed as follows:$"
    r"|^(?:DECIDES|DECLARES?|AGREES?|RECOMMENDS):$",
    re.IGNORECASE,
)
_CITATION = re.compile(r"^HAVING REGARD\b")
_WE_PARA = re.compile(r"^WE\b")
# Openers that name the enacting body; kept as a preamble line, not a recital.
_OPENER = re.compile(r"^(THE COUNCIL|THE PARTIES|ADHERENTS|THE GOVERNMENTS|WE, THE)\b")
_ALL_CAPS = re.compile(r"^[A-Z0-9][A-Z0-9 ,.;:'’()\-–—/&]+$")
_HEADING_WORDS = 14

# Verbs that open an operative section. A preamble verb (RECOGNISING, NOTING)
# is a participle and never in this set, so a recital never opens the body.
OPERATIVE_VERBS = frozenset(
    {
        "AGREES", "RECOMMENDS", "DECIDES", "DECLARES", "INVITES", "INSTRUCTS",
        "ENCOURAGES", "REQUESTS", "CALLS ON", "URGES", "COMMITS", "RESOLVES",
        "ADOPTS", "APPROVES", "WELCOMES", "NOTES", "CONFIRMS", "REAFFIRMS",
    }
)  # fmt: skip
# Verbs whose operative duty falls on a body other than the Adherents.
NON_ADHERENT_VERBS = frozenset({"INVITES", "INSTRUCTS", "ENCOURAGES", "REQUESTS", "CALLS ON"})

_RANK = {"part": 0, "article": 1, "section": 1, "paragraph": 2, "point": 3, "indent": 4}
_EID_PREFIX = {
    "part": "part",
    "article": "art",
    "section": "sec",
    "paragraph": "para",
    "point": "point",
    "indent": "indent",
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


@dataclass
class _Instrument:
    meta: dict[str, str]
    opener: str | None = None
    citations: list[str] = field(default_factory=list)
    recitals: list[list[str]] = field(default_factory=list)
    formula: str | None = None
    body: list[_Node] = field(default_factory=list)


def is_oecd_instrument_html(text: str) -> bool:
    return 'name="oecd.key"' in text[:2000]


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
            joined = " ".join(el.text_content().split())
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

    def __init__(self) -> None:
        self.roots: list[_Node] = []
        self.stack: list[_Node] = []
        self.pending_heading: str | None = None
        self.seen: dict[tuple[str, str], int] = {}
        self.prose_count = 0

    def _close_to(self, rank: int) -> None:
        while self.stack and _RANK[self.stack[-1].kind] >= rank:
            self.stack.pop()

    def _open(self, kind: str, num: str, key: str, heading: str | None = None) -> _Node:
        self._close_to(_RANK[kind])
        parent = self.stack[-1] if self.stack else None
        scope = parent.eid if parent else ""
        n = self.seen.get((scope, f"{kind}:{key}"), 0) + 1
        self.seen[(scope, f"{kind}:{key}")] = n
        # A repeated number keeps a unique eId, as a repeated article does.
        node = _Node(kind, num, key if n == 1 else f"{key}_dup{n}", heading=heading)
        leaf = f"{_EID_PREFIX[kind]}_{node.key}"
        node.eid = f"{scope}__{leaf}" if scope else leaf
        if parent is not None:
            node.non_adherent = parent.non_adherent
            parent.children.append(node)
        else:
            self.roots.append(node)
        self.stack.append(node)
        if self.pending_heading and kind in ("part", "article", "section", "paragraph"):
            if node.heading is None:
                node.heading = self.pending_heading
            elif kind != "paragraph":
                node.subheading = self.pending_heading
            self.pending_heading = None
        return node

    def _open_prose_paragraph(self, block: str) -> None:
        self._close_to(_RANK["paragraph"])
        self.prose_count += 1
        self._open("paragraph", f"{self.prose_count}.", str(self.prose_count)).text.append(block)

    def feed(self, block: str) -> None:
        m = _CONTAINER.match(block)
        if m:
            word, key, rest = m.groups()
            self._open("part", f"{word.title()} {key}", key.lower(), heading=rest.strip() or None)
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
            node.non_adherent = verb in NON_ADHERENT_VERBS
            if tail:
                node.text.append(tail)
            return
        op = _operative(block)
        if op and not any(n.kind in ("section", "article") for n in self.stack):
            verb, rest = op
            numeral = _to_roman(sum(1 for r in self.roots if r.kind == "section") + 1)
            node = self._open("section", f"{numeral}.", numeral, heading=verb)
            node.non_adherent = verb in NON_ADHERENT_VERBS
            if rest:
                node.text.append(rest)
            return
        m = _NUM_PARA.match(block) or _LETTER_PARA.match(block)
        if m:
            self._open("paragraph", f"{m.group(1)}.", m.group(1)).text.append(m.group(2))
            return
        m = _POINT.match(block)
        if m:
            # `a)` under an open `1.` or `a.` paragraph is a point; straight under
            # a section or article it is a paragraph, and the next `b)` its sibling.
            under_dotted = any(n.kind == "paragraph" and n.num.endswith(".") for n in self.stack)
            kind = "point" if under_dotted else "paragraph"
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
        # A part's or article's heading is the short line right after its number.
        if (
            top is not None
            and top.kind in ("part", "article")
            and not top.text
            and not top.children
            and top.heading is None
            and _is_heading(block)
        ):
            top.heading = block
            return
        # A short unpunctuated line is a heading for what follows; a prose
        # continuation ends in punctuation or runs longer.
        if _is_heading(block) and (top is None or top.text or _ALL_CAPS.match(block)):
            self.pending_heading = block
            return
        if top is not None:
            top.text.append(block)
        else:
            self._open_prose_paragraph(block)


def _segment(blocks: list[str], meta: dict[str, str]) -> _Instrument:
    if not blocks:
        raise OecdHtmlError("no text blocks in the HTML")
    inst = _Instrument(meta=meta)
    i = 0
    # Preamble runs to the enacting line, or to the first structural token when
    # the instrument has none (a declaration's first `WE ...` paragraph).
    while i < len(blocks):
        block = blocks[i]
        if _FORMULA.match(block):
            inst.formula = block
            i += 1
            break
        if _starts_body(block) or (_WE_PARA.match(block) and inst.opener):
            break
        if (
            inst.opener is None
            and not inst.citations
            and not inst.recitals
            and _OPENER.match(block)
        ):
            inst.opener = block
        elif _CITATION.match(block):
            inst.citations.append(block)
        elif _INDENT.match(block) and inst.recitals:
            inst.recitals[-1].append(block)  # a bulleted list under `CONSIDERING that:`
        elif block != "PREAMBLE":
            inst.recitals.append([block])
        i += 1
    builder = _Builder()
    for block in blocks[i:]:
        builder.feed(block)
    inst.body = builder.roots
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
    if node.non_adherent:
        el.set("refersTo", "#nonAdherentDuty")
        if node.kind == "paragraph":
            counts["non_adherent_paragraphs"] += 1
    counts[node.kind + "s"] = counts.get(node.kind + "s", 0) + 1
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
    inst: _Instrument, *, frbr_work_uri: str, language: str, expression_date: str
) -> etree._Element:
    doctype = inst.meta.get("type") or frbr_work_uri.strip("/").split("/")[3]
    meta = etree.Element(f"{{{AKN_NS}}}meta")
    ident = _akn(meta, "identification", source="#codify")
    work = _akn(ident, "FRBRWork")
    _akn(work, "FRBRthis", value=f"{frbr_work_uri}/main")
    _akn(work, "FRBRuri", value=frbr_work_uri)
    _akn(work, "FRBRdate", date=expression_date, name="adoption")
    _akn(work, "FRBRauthor", href="#oecdCouncil")
    _akn(work, "FRBRcountry", value="oecd")
    _akn(work, "FRBRsubtype", value=doctype)
    if inst.meta.get("key"):
        _akn(work, "FRBRnumber", value=inst.meta["key"])
    expr = _akn(ident, "FRBRExpression")
    _akn(expr, "FRBRthis", value=f"{frbr_work_uri}/{language}@{expression_date}/main")
    _akn(expr, "FRBRuri", value=f"{frbr_work_uri}/{language}@{expression_date}")
    _akn(expr, "FRBRdate", date=expression_date, name="adoption")
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
        date=expression_date,
        type="generation",
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
    if inst.opener or inst.citations or inst.recitals or inst.formula:
        preamble = _akn(root_act, "preamble")
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


def instrument_html_to_akn(
    text: str,
    *,
    frbr_work_uri: str,
    language: str = "eng",
    provenance: dict[str, object] | None = None,
) -> str:
    """Deterministic Compendium HTML → AKN `act`; the adoption date is the expression date."""
    blocks, meta = _blocks(text)
    inst = _segment(blocks, meta)
    adopted = meta.get("adopted")
    if not adopted:
        raise OecdHtmlError("no oecd.adopted meta: the acquirer wraps the body with it")
    root = etree.Element(f"{{{AKN_NS}}}akomaNtoso", nsmap={None: AKN_NS})
    doctype = meta.get("type") or frbr_work_uri.strip("/").split("/")[3]
    root_act = etree.SubElement(root, f"{{{AKN_NS}}}act", contains="originalVersion", name=doctype)
    root_act.append(
        _meta(inst, frbr_work_uri=frbr_work_uri, language=language, expression_date=adopted)
    )
    counts = build_body(inst, root_act)
    if provenance is not None:
        provenance["html_source"] = {
            **counts,
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
    "NON_ADHERENT_VERBS",
    "OPERATIVE_VERBS",
    "OecdHtmlError",
    "ingest",
    "instrument_html_to_akn",
    "is_oecd_instrument_html",
]
