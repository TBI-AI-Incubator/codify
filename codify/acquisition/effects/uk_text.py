"""The text an effect put in place, quoted in the amending provision the feed names.
Attached only when the instruction, its lead-in, an opener before it or a heading
over it names the act the effect is recorded against; all else is refused by reason.
"""

from __future__ import annotations

import collections
import re
from collections.abc import Collection
from dataclasses import dataclass

from lxml import etree

_NOTES = {"authorialNote", "noteRef"}
_STRUCTURES = {"quotedStructure", "embeddedStructure"}
_ASIDE = _NOTES | _STRUCTURES | {"quotedText"}
_BLOCKS = {"num", "heading", "subheading", "p"}
_BOUNDARY = {"section", "article", "rule", "part", "chapter", "schedule", "crossheading"}
_WORDED = {"schedule", "part", "chapter"}
_TOP_UNITS = {"section", "regulation", "article", "rule", "paragraph"} | _WORDED
_VERBS = {"substitution": ("substitute",), "insertion": ("insert", "add")}
_VERB = re.compile(r"\b(substitute|insert|add)(?:d|ed)?\b", re.I)
_Q = "\x00"
_SLOT = re.compile(rf"{_Q}(\d+){_Q}")
_FOR_QUOTE = re.compile(rf"\bfor\s+{_Q}(\d+){_Q}(?:(?!\bfor\b)[^{_Q}])*$", re.I)
# All that may stand between the verb and the text it puts in ("insert the following—").
_LEAD = re.compile(
    r"(?:\b(?:the|following|italic|headings?|words?|provisions?|text|at|end)\b|[\s,:;—–−-])*", re.I
)
# Every way a short form is given a meaning, whatever the meaning.
_DEFINES = re.compile(
    rf"(?:referred to as|known as|called|cited as|references? to)\s+{_Q}(\d+){_Q}"
    rf"|{_Q}(\d+){_Q}\s+(?:means|refers|is to be|are to be)\b|\({_Q}(\d+){_Q}\)",
    re.I,
)
_OPENER = re.compile(r"\b(?:is|are)\s+amended\b", re.I)
_BRACKETS = re.compile(r"\([^()]*(?:\([^()]*\)[^()]*)*\)")
# A description in brackets opens lower case; a title's own brackets do not.
_GLOSS = re.compile(r"\((?=[a-z])[^()]*(?:\([^()]*\)[^()]*)*\)")
_LONGER_BEFORE = re.compile(r"([A-Z][\w’-]*),?\s+(?:(?:and|of|for)\s+)?$")
_MENTION = re.compile(r"\breferences?\s+to\s+(?:the\s+)?$", re.I)
_LONGER_AFTER = re.compile(r"\s*(?:\([A-Z][^)]*\)\s*)?(?:of\s+)?[A-Z][a-z]")
# An act as an instruction cites one: a titled year, a short form, itself, a numbered instrument.
_CITED = re.compile(
    r"\b(?:Act|Measure|Regulations|Order|Rules)(?:\s+\(Northern Ireland\))?\s+\d{4}"
    r"|\bthe\s+\d{4}\s+(?:Act|Measure|Regulations|Order|Rules)\b"
    r"|\bth(?:is|ese|at|ose)\s+(?:[A-Z]|instrument|enactment)|\d+/\d+"
)
_CAPITAL = re.compile(r"(?<![\w’-])[A-Z][\w’-]*")
# Capitalised words an instruction uses without naming anything.
_PLAIN = frozenset(
    "In After Before For At On Omit The Insert Substitute Renumber Where And Or But Accordingly "
    "From Until To Of Any Each Every Schedule Schedules Table Tables Heading".split()
)
# A unit word, plain only with its number after it ("Regulation 5", never "the Regulation").
_UNIT_WORD = re.compile(
    r"(?:Sub-?)?(?:[Ss]ection|[Pp]aragraph|Part|Chapter|Article|Regulation|Rule|Group|Annex)s?"
    r"(?=\s+\(?[0-9A-Z])"
)


@dataclass(frozen=True)
class EffectText:
    """`old` is empty where the instruction replaces a unit or a span it only describes."""

    old: tuple[str, ...]
    new: tuple[str, ...]
    instruction: str
    source_eid: str


@dataclass(frozen=True)
class Refused:
    reason: str


@dataclass(frozen=True)
class _Unit:
    number: str
    #: Numbered in brackets, as a subsection or sub-paragraph is and a section is not.
    sub: bool
    #: The word its number carries ("part", "schedule"), else its element's name.
    kind: str
    text: str


@dataclass
class _Quote:
    text: str
    #: A quoted structure's numbered units; empty for quoted words.
    units: list[_Unit]


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _flat(el: etree._Element) -> str:
    """Text with a space where a number, heading or paragraph ends."""
    parts = [el.text or ""]
    for child in el:
        # A schedule's shoulder reference is enacted text; a footnote is not.
        if _local(child) not in _NOTES or child.get("class") == "referenceNote":
            parts.append(_flat(child))
            if _local(child) in _BLOCKS or _local(child) in _NOTES:
                parts.append(" ")
        parts.append(child.tail or "")
    return "".join(parts)


def _units(el: etree._Element) -> list[_Unit]:
    """Numbered units in order, each led by the heading its wrapper puts above it."""
    out: list[_Unit] = []
    lead = ""
    for child in el:
        num = next((c for c in child if _local(c) == "num"), None) if _local(child) else None
        own = _prose(num, []).strip() if num is not None else ""
        tokens = re.findall(r"[0-9A-Za-z]+", own)
        if tokens:
            text = _squash(f"{lead} {_flat(child)}")
            worded = len(tokens) > 1 and tokens[0].isalpha()
            kind = tokens[0].lower() if worded else _local(child)
            out.append(_Unit(tokens[-1].lower(), own.startswith("("), kind, text))
            lead = ""
        elif _local(child) == "heading":
            lead = _flat(child)
        elif _local(child):
            out.extend(_units(child))
    return out


def _prose(el: etree._Element, quotes: list[_Quote]) -> str:
    """Text with notes, numbers and headings dropped and each quoted element a slot."""
    parts = [el.text or ""]
    for child in el:
        name = _local(child)
        if name in _STRUCTURES or name == "quotedText":
            units = _units(child) if name in _STRUCTURES else []
            text = _squash(_flat(child))
            if name == "quotedText" and text[:1] == "“" and text[-1:] == "”":
                text = text[1:-1].strip()
            quotes.append(_Quote(text, units))
            parts.append(f"{_Q}{len(quotes) - 1}{_Q}")
        elif name and name not in _NOTES | {"num", "heading"}:
            parts.append(_prose(child, quotes))
        parts.append(child.tail or "")
    return "".join(parts)


def _slot_inline_quotes(prose: str, quotes: list[_Quote]) -> str | None:
    """Swap each outermost “…” for a slot; None when the marks do not balance."""
    out: list[str] = []
    depth = start = 0
    for i, ch in enumerate(prose):
        if ch == "“":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "”":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                quotes.append(_Quote(_squash(prose[start + 1 : i]), []))
                out.append(f"{_Q}{len(quotes) - 1}{_Q}")
        elif depth == 0:
            out.append(ch)
    return "".join(out) if depth == 0 else None


def _readable(el: etree._Element) -> tuple[str, list[_Quote]] | None:
    quotes: list[_Quote] = []
    prose = _slot_inline_quotes(_prose(el, quotes), quotes)
    if prose is None or any(q.text.count("“") != q.text.count("”") for q in quotes):
        return None
    return _squash(prose), quotes


def _own_paragraphs(el: etree._Element, apart: Collection[str] = _ASIDE) -> list[etree._Element]:
    """Paragraphs of the act's own text: none from a note or a quotation."""
    return [
        p
        for p in el.iter()
        if _local(p) == "p" and not any(_local(a) in apart for a in p.iterancestors())
    ]


def _aliases(root: etree._Element, titles: Collection[str]) -> set[str]:
    """Short forms the amending act itself defines for the target and nothing else."""
    title = "|".join(re.escape(t) for t in titles)
    defines = re.compile(
        rf"{_Q}(\d+){_Q}\s+means\s+the\s+(?P<a>{title})(?=\s*(?:\([^)]*\))?\s*(?:[;.,]|$))"
        rf"|(?P<b>{title})\s*\({_Q}(\d+){_Q}\)"
    )
    ours: collections.Counter[str] = collections.Counter()
    quoted: collections.Counter[str] = collections.Counter()
    unread: list[str] = []
    # Footnotes are read here: some instruments define a short form nowhere else.
    for p in _own_paragraphs(root, _ASIDE - _NOTES):
        read = _readable(p)
        if read is None:
            unread.append("".join(p.itertext()))
            continue
        prose, quotes = read
        for m in defines.finditer(prose):
            if not _part_of_longer_name(prose, *m.span("a" if m.group("a") else "b")):
                ours[quotes[int(m.group(1) or m.group(4))].text] += 1
        quoted.update(quotes[int(a or b or c)].text for a, b, c in _DEFINES.findall(prose))
    return {
        alias
        for alias, n in ours.items()
        if alias.strip() and quoted[alias] == n and not any(f"“{alias}”" in raw for raw in unread)
    }


def _blank(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group()), text)


def _plain(word: str, after: str = " 1") -> bool:
    unit = _UNIT_WORD.match(word + after[:3])
    return (
        word in _PLAIN
        or len(word) == 1
        or any(c.isdigit() for c in word)
        or re.fullmatch("[IVXLC]+", word) is not None
        or (unit is not None and unit.end() == len(word))
    )


def _part_of_longer_name(prose: str, start: int, end: int) -> bool:
    """A title or short form that is only the tail or head of another's, or is
    only mentioned ("for the reference to the X Act substitute")."""
    before = _LONGER_BEFORE.search(prose[:start]) if prose[start].isupper() else None
    return (
        (before is not None and not _plain(before.group(1)))
        or bool(_LONGER_AFTER.match(prose[end:]))
        or bool(_MENTION.search(prose[:start]))
    )


def _names(prose: str, target: re.Pattern[str], kind: str) -> bool | None:
    """True when the first act this text names is the target, False when it is
    another, None when it names none. A sibling instruction never confers the target."""
    prose = _GLOSS.sub("", prose)
    hits = [m.span() for m in target.finditer(prose) if not _part_of_longer_name(prose, *m.span())]
    chars = list(prose)
    for start, end in hits:
        chars[start:end] = " " * (end - start)
    masked = _blank(_blank("".join(chars), _BRACKETS), _SLOT)
    cited = [m.start() for m in _CITED.finditer(masked)]
    words = [m for m in _CAPITAL.finditer(masked) if not _plain(m.group(), prose[m.end() :])]
    if kind == "sibling" and not _OPENER.search(prose):
        verb = _VERB.search(masked)
        located = [m for m in words if verb is None or m.start() < verb.start()]
        return False if cited or located else None
    if kind == "heading" and words and not masked[: words[0].start()].strip():
        words = words[1:]
    other = cited + [m.start() for m in words]
    if hits:
        return not any(start < hits[0][0] for start in other)
    return False if other else None


def _blocks_before(
    node: etree._Element, *, headings_only: bool
) -> list[tuple[str, etree._Element]]:
    """What precedes a node under its parent, nearest first: a lead-in, earlier
    siblings' paragraphs, the parent's own heading. Nothing from inside an earlier
    part, section or group, whose openers govern only what it holds."""
    parent = node.getparent()
    out: list[tuple[str, etree._Element]] = []
    for sibling in reversed(list(parent)[: parent.index(node)]):
        name = _local(sibling)
        if name == "heading":
            out.append(("heading", sibling))
        elif headings_only or not name or name in _ASIDE:
            continue
        elif (sibling.get("name") or name) not in _BOUNDARY:
            kind = "lead" if name == "intro" else "sibling"
            out.extend((kind, p) for p in reversed(_own_paragraphs(sibling)))
    return out


def _out_of_scope(el: etree._Element, own: str, target: re.Pattern[str]) -> str | None:
    """Why the nearest text naming an act is not the target's, or None when it is.
    Nearest is the instruction, then back through its section or part, then headings."""
    verdict = _names(own, target, "own")
    node, bounded = el, False
    while verdict is None and node.getparent() is not None:
        bounded = bounded or (node.get("name") or _local(node)) in _BOUNDARY
        for kind, block in _blocks_before(node, headings_only=bounded):
            read = _readable(block)
            if read is None:
                return "scope_unreadable"
            verdict = _names(read[0], target, kind)
            if verdict is not None:
                break
        node = node.getparent()
    if verdict is None:
        return "target_not_named"
    return None if verdict else "names_another_act"


def _for_unit(quote: _Quote, target_eid: str) -> str | None:
    """A quoted structure cut to the unit the effect names; None when it holds
    numbered units and none of them is that one at that level."""
    if not quote.units:
        return quote.text
    *above, last = target_eid.lower().split("-")
    word = above[-1] if above and above[-1] in _TOP_UNITS else ""
    # A regulation, article or rule is marked up under more than one element name.
    kinds = {word} if word in _WORDED | {"section", "paragraph"} else None
    matched = [
        u.text
        for u in quote.units
        if u.number == last and u.sub == (not word) and (not kinds or u.sub or u.kind in kinds)
    ]
    return matched[0] if len(matched) == 1 else None


def effect_text(
    amender: etree._Element,
    *,
    source_eid: str,
    target_eid: str,
    action: str | None,
    target_titles: Collection[str],
) -> EffectText | Refused:
    """The text one effect put in place, or the reason none is attached.
    `source_eid` is the feed's amending provision, `target_eid` an eId the target holds.
    Pass the amending act as currently revised: some are corrected before taking effect."""
    verbs = _VERBS.get(action or "")
    if verbs is None:
        return Refused("action_carries_no_text")
    titles = [t for t in target_titles if t.strip()]
    if not titles:
        return Refused("target_title_unknown")
    found = amender.xpath("//*[@eId=$eid]", eid=source_eid)
    if len(found) != 1:
        return Refused("pointer_not_found")
    read = _readable(found[0])
    if read is None:
        return Refused("quotes_unbalanced")
    prose, quotes = read
    named = sorted([*titles, *_aliases(amender, titles)], key=len, reverse=True)
    # Either case on the first letter, for a short form opening a sentence.
    either = "|".join(
        f"(?:{re.escape(n[0].lower())}|{re.escape(n[0].upper())}){re.escape(n[1:])}" for n in named
    )
    target = re.compile(rf"(?<![\w’-])(?:{either})(?![\w’-])")
    # Scope starts at the paragraph giving the instruction, not at all the pointer holds.
    told = [
        (para, text)
        for para in ([found[0]] if _local(found[0]) == "p" else _own_paragraphs(found[0]))
        if (text := (_readable(para) or ("",))[0]) and _VERB.search(_GLOSS.sub("", text))
    ]
    start, own = told[0] if len(told) == 1 else (found[0], prose)
    if reason := _out_of_scope(start, own, target):
        return Refused(reason)
    instruction = _SLOT.sub(lambda m: f"“{quotes[int(m.group(1))].text}”", prose)
    prose = _GLOSS.sub("", prose)
    found_verbs = list(_VERB.finditer(prose))
    if len(found_verbs) != 1:
        return Refused("several_instructions" if found_verbs else "no_instruction")
    verb = found_verbs[0]
    if verb.group(1).lower() not in verbs:
        return Refused("verb_differs_from_effect")
    tail = prose[verb.end() :]
    after = [quotes[int(i)] for i in _SLOT.findall(tail)]
    if not after:
        return Refused("no_quoted_text")
    lead, *gaps = _SLOT.sub(_Q, tail).split(_Q)[:-1]
    if not _LEAD.fullmatch(lead) or any(gap.strip(" ,;") for gap in gaps):
        return Refused("quote_not_replacement")
    new = [_for_unit(q, target_eid) for q in after]
    if None in new:
        return Refused("quoted_unit_differs")
    before = prose[: verb.start()]
    one_for = action == "substitution" and len(re.findall(r"\bfor\b", before, re.I)) == 1
    old = _FOR_QUOTE.search(before) if one_for else None
    return EffectText(
        old=(quotes[int(old.group(1))].text,) if old else (),
        new=tuple(t for t in new if t is not None),
        instruction=instruction,
        source_eid=source_eid,
    )
