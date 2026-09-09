"""Deterministic AKN write path: source clone + per-eId text patch.

Replaces the Bluebell round-trip that used to sit at translation write time.
Element count is source count by construction, so structural drift is
impossible; the LLM's per-eId `TranslatedBlock` supplies heading + line
strings only and cannot introduce structure.
"""

from __future__ import annotations

import re
from datetime import date
from typing import cast

import structlog
from lxml import etree

from codify.akn import AKN_NS
from codify.akn.vocabulary import is_lifted_note, provision_text
from codify.frbr import build_frbr_expression_uri
from codify.lang import to_iso639_3
from codify.pipeline.enrich.arabic_normalise import (
    AR_ORDINAL_TO_INT,
    JOINER_STRIP_TABLE,
    latinise_arabic_ordinal,
)
from codify.translate.translate_bodies import TranslatedBlock

logger = structlog.get_logger()

# Sanitiser regexes for LLM text, applied to paragraph and heading writes.
# Paired Markdown emphasis is removed; single markers are preserved.
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_UNDERLINE_BOLD = re.compile(r"__(.+?)__")

# Remove invisible separators from target text, restoring a hyphen when one
# occurs between word characters.
_INVISIBLE_SEPARATORS = "­​‌‍"
_INVISIBLE_BETWEEN_WORDS = re.compile(rf"(?<=\w)[{_INVISIBLE_SEPARATORS}](?=\w)")
_INVISIBLE_ANY = re.compile(rf"[{_INVISIBLE_SEPARATORS}]")

# Log unpaired markers after sanitisation; do not remove them blindly.
_RESIDUAL_MARKDOWN = re.compile(r"\*\*|__")


def _sanitise_target_text(text: str) -> str:
    """Strip Markdown emphasis and repair invisible-character separators
    in LLM-emitted target text. Assumes single-line input, a `<p>` never
    carries a raw newline mid-token, so the non-greedy paired-match does
    not need `re.DOTALL`."""
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_UNDERLINE_BOLD.sub(r"\1", text)
    text = _INVISIBLE_BETWEEN_WORDS.sub("-", text)
    text = _INVISIBLE_ANY.sub("", text)
    residual = _RESIDUAL_MARKDOWN.findall(text)
    if residual:
        # Include a short preview to locate the offending provision.
        logger.warning(
            "sanitise_target_text_residual_markdown",
            markers=residual,
            preview=text[:80],
        )
    return text


# Arabic-Indic digits are folded to ASCII in `<num>` text for Latin and Hebrew
# targets.
_DIGIT_TO_ASCII = {
    **{0x0660 + i: str(i) for i in range(10)},
    **{0x06F0 + i: str(i) for i in range(10)},
}

# Arabic abjad letters used as single-character sub-list enumerators. Longer
# ordinal words are handled by the render-time normaliser.
_AR_ABJAD_TO_LATIN: dict[str, str] = {
    "أ": "a",
    "ا": "a",
    "إ": "a",
    "آ": "a",
    "ب": "b",
    "ج": "c",
    "د": "d",
    "ه": "e",
    "ة": "e",
    # Presentation forms of hāʾ map to the same enumerator.
    "ﻩ": "e",
    "ﻪ": "e",
    "ﻫ": "e",
    "ﻬ": "e",
    "و": "f",
    "ز": "g",
    "ح": "h",
    "ط": "i",
    "ي": "j",
    "ك": "k",
    "ل": "l",
    # Presentation forms of lām map to the same enumerator.
    "ﻟ": "l",
    "ﻠ": "l",
    "ﻞ": "l",
    "ﻝ": "l",
    "م": "m",
    "ن": "n",
    "س": "o",
    "ع": "p",
    "ف": "q",
    "ص": "r",
    "ق": "s",
    "ر": "t",
    "ش": "u",
    "ت": "v",
    "ث": "w",
    "خ": "x",
    "ذ": "y",
    "ض": "z",
}

# Israeli legal typography numbers structural units with letter numerals
# (פרק א = chapter aleph, סעיף ה = section he), and sub-list enumerators follow
# the same alphabet. Ordinals 1..20 emit Hebrew letter numerals; 11..19 avoid the
# religious taboo pairs (15 → טו not יה, 16 → טז not יו).
_AR_ABJAD_TO_HEBREW: dict[str, str] = {
    "أ": "א",
    "ا": "א",
    "إ": "א",
    "آ": "א",
    "ب": "ב",
    "ج": "ג",
    "د": "ד",
    "ه": "ה",
    "ة": "ה",
    "ﻩ": "ה",
    "ﻪ": "ה",
    "ﻫ": "ה",
    "ﻬ": "ה",
    "و": "ו",
    "ز": "ז",
    "ح": "ח",
    "ط": "ט",
    "ي": "י",
    "ك": "כ",
    "ل": "ל",
    "ﻟ": "ל",
    "ﻠ": "ל",
    "ﻞ": "ל",
    "ﻝ": "ל",
    "م": "מ",
    "ن": "נ",
    "س": "ס",
    "ع": "ע",
    "ف": "פ",
    "ص": "צ",
    "ق": "ק",
    "ر": "ר",
    "ش": "ש",
    "ت": "ת",
}


# Letters in `_AR_ABJAD_TO_LATIN` that lack a direct Hebrew equivalent.
# We fall back to their Latin form on the Hebrew lane so the reader
# never sees a raw Arabic glyph inside a `<num>`.
_HEBREW_ABJAD_FALLBACK = {
    letter: _AR_ABJAD_TO_LATIN[letter]
    for letter in _AR_ABJAD_TO_LATIN
    if letter not in _AR_ABJAD_TO_HEBREW
}
_AR_ABJAD_TO_HEBREW.update(_HEBREW_ABJAD_FALLBACK)

_INT_TO_HEBREW_NUMERAL: dict[int, str] = {
    1: "א",
    2: "ב",
    3: "ג",
    4: "ד",
    5: "ה",
    6: "ו",
    7: "ז",
    8: "ח",
    9: "ט",
    10: "י",
    11: "יא",
    12: "יב",
    13: "יג",
    14: "יד",
    15: "טו",
    16: "טז",
    17: "יז",
    18: "יח",
    19: "יט",
    20: "כ",
    21: "כא",
    22: "כב",
    23: "כג",
    24: "כד",
    25: "כה",
    26: "כו",
    27: "כז",
    28: "כח",
    29: "כט",
    30: "ל",
}

# Load-time invariant: every Arabic ordinal we know how to latinise must
# have a Hebrew form, or the Hebrew lane silently emits raw Arabic when
# a maintainer extends `AR_ORDINAL_TO_INT` without adding the Hebrew pair.
_missing_hebrew_ordinals = set(AR_ORDINAL_TO_INT.values()) - _INT_TO_HEBREW_NUMERAL.keys()
assert not _missing_hebrew_ordinals, (
    f"_INT_TO_HEBREW_NUMERAL lags AR_ORDINAL_TO_INT: {sorted(_missing_hebrew_ordinals)}"
)


# Legacy Hebrew AKN shipped with Latin `<num>` text (`1`, `a.`) from the
# target-blind write pass, so the Hebrew backfill converts those Latin values
# too: re-running the canonicaliser on a legacy delivery produces `א`, `א.`.
# Values are the inverse of the Arabic → Latin fold below.
_LATIN_DIGIT_TO_HEBREW: dict[str, str] = {
    str(n): letter for n, letter in _INT_TO_HEBREW_NUMERAL.items()
}
_LATIN_ABJAD_TO_HEBREW: dict[str, str] = {
    latin: hebrew
    for arabic, latin in _AR_ABJAD_TO_LATIN.items()
    if (hebrew := _AR_ABJAD_TO_HEBREW.get(arabic)) is not None and len(latin) == 1
}

_HEBREW_TARGET_TAGS = frozenset({"he", "heb", "hebrew", "iw"})


def _is_hebrew_target(target_language: str | None) -> bool:
    """Case-insensitive check against ISO-639-1 (`he`), ISO-639-2 (`heb`),
    the legacy tag (`iw`), and the human-readable name (`Hebrew`).
    Everything else, including `None`, routes to the Latin default."""
    if not target_language:
        return False
    return target_language.strip().lower() in _HEBREW_TARGET_TAGS


def _localname(el: etree._Element) -> str:
    return cast(str, etree.QName(el).localname)


def _is_akn(el: etree._Element, name: str) -> bool:
    """Is this the named element? Comments and PIs carry a non-string tag that
    `etree.QName` refuses, so the isinstance check has to come first."""
    return isinstance(el.tag, str) and _localname(el) == name


def _clear_children(el: etree._Element) -> None:
    for child in list(el):
        el.remove(child)


def _set_p_text(p: etree._Element, text: str) -> None:
    """Replace `<p>` inner with plain text. Inline `<ref>`/`<def>` typing is
    lost; the resolve_refs workflow re-derives it against the persisted AKN.

    A bound `<authorialNote>` is carried over: it is not part of the line the
    translator was given, so clearing it would delete the footnote outright.
    Collected at any depth, matching what `provision_text` leaves out."""
    notes = [el for el in p.iter() if el is not p and is_lifted_note(el)]
    _clear_children(p)
    p.text = _sanitise_target_text(text)
    for note in notes:
        note.tail = None
        p.append(note)


def _p_has_text(p: etree._Element) -> bool:
    return bool(provision_text(p).strip())


def _descendant_ps(el: etree._Element) -> list[etree._Element]:
    return [
        p for p in el.iter() if isinstance(p.tag, str) and _localname(p) == "p" and _p_has_text(p)
    ]


def _plain_text_p(p: etree._Element) -> bool:
    """No element children: safe for `_set_p_text`, which flattens inline
    markup. Semantic children (`<docType>`, `<docNumber>` in AKN4EU long
    titles) must survive, so mixed-content `<p>`s are never patch slots."""
    return not any(isinstance(c.tag, str) for c in p)


def preface_p_slots(root: etree._Element) -> list[etree._Element]:
    """Non-empty `<p>` slots under the first `<preface>`, document order:
    direct `<p>` children plus `<longTitle>` descendant plain-text `<p>`s.
    Shared by the extract and patch sides so their positional zips cannot
    diverge."""
    for el in root.iter():
        if not isinstance(el.tag, str) or _localname(el) != "preface":
            continue
        slots: list[etree._Element] = []
        for child in el:
            if not isinstance(child.tag, str):
                continue
            name = _localname(child)
            if name == "p":
                if _p_has_text(child):
                    slots.append(child)
            elif name == "longTitle":
                slots.extend(p for p in _descendant_ps(child) if _plain_text_p(p))
        return slots
    return []


def preamble_p_slots(root: etree._Element) -> list[etree._Element]:
    """Non-empty `<p>` slots under the first `<preamble>`, document order:
    enacting-formula `<p>`s, `<citations>/<citation>` and
    `<recitals>/<recital>` `<p>`s, direct `<p>`s."""
    for el in root.iter():
        if not isinstance(el.tag, str) or _localname(el) != "preamble":
            continue
        slots: list[etree._Element] = []
        for child in el:
            if not isinstance(child.tag, str):
                continue
            name = _localname(child)
            if name == "p":
                if _p_has_text(child):
                    slots.append(child)
            elif (name == "formula" and child.get("name") == "enactingFormula") or name in (
                "citations",
                "recitals",
            ):
                slots.extend(_descendant_ps(child))
        return slots
    return []


def conclusions_p_slots(root: etree._Element) -> list[etree._Element]:
    """Non-empty `<p>` slots under the first `<conclusions>`, document order: direct
    `<p>`s (place and date) plus `<blockContainer>` descendants (the signatory block).

    `<blockContainer>` children go through `_plain_text_p` as the preface's
    `<longTitle>` does: `_set_p_text` flattens inline markup, so a mixed-content `<p>`
    must never become a patch slot.
    """
    for el in root.iter():
        if not isinstance(el.tag, str) or _localname(el) != "conclusions":
            continue
        slots: list[etree._Element] = []
        for child in el:
            if not isinstance(child.tag, str):
                continue
            name = _localname(child)
            if name == "p":
                if _p_has_text(child):
                    slots.append(child)
            elif name == "blockContainer":
                slots.extend(p for p in _descendant_ps(child) if _plain_text_p(p))
        return slots
    return []


def _patch_preface(root: etree._Element, preface_lines: list[str]) -> None:
    """Replace preface `<p>` slots with the translated lines. Empty lines =
    caller opted out of patching; otherwise counts must match (clamped
    upstream against the same slot function), so strict zip fails loudly on
    drift instead of silently shipping half-source text."""
    if preface_lines:
        for p, line in zip(preface_p_slots(root), preface_lines, strict=True):
            _set_p_text(p, line)


def _patch_preamble(root: etree._Element, preamble_lines: list[str]) -> None:
    """Replace preamble `<p>` slots with the translated lines. Same strict
    contract as `_patch_preface`."""
    if preamble_lines:
        for p, line in zip(preamble_p_slots(root), preamble_lines, strict=True):
            _set_p_text(p, line)


def _patch_conclusions(root: etree._Element, conclusions_lines: list[str]) -> None:
    """Replace conclusions `<p>` slots with the translated lines. Same strict
    contract as `_patch_preface`."""
    if conclusions_lines:
        for p, line in zip(conclusions_p_slots(root), conclusions_lines, strict=True):
            _set_p_text(p, line)


def _find_local(el: etree._Element, name: str) -> etree._Element | None:
    return next((c for c in el if isinstance(c.tag, str) and _localname(c) == name), None)


def _patch_frbr_meta(
    root: etree._Element, target_language: str, expression_date: str | None = None
) -> None:
    """Rewrite FRBRExpression + FRBRManifestation to the target language, so the
    translated XML stops carrying the source expression's identity. Element shapes
    mirror `akn/_emitter._emit_meta`. A hand-authored fragment without
    identification or a work URI, or a target language that folds to nothing, is a
    no-op with a warning; the workflow persist step raises for those, and this path
    only patches what it can name.

    Every ``<identification>`` in the tree is rewritten, not only the outer
    document's: an attachment is emitted as a nested ``<doc>`` carrying its own FRBR
    block, so a consumer that trusts a nested document's own metadata would read a
    translated annex as still being in the source language. Each block is patched
    from its own work URI, so an annex keeps its own identity.
    """
    try:
        iso = to_iso639_3(target_language)
    except ValueError:
        logger.warning("frbr_meta_patch_skipped", reason="unknown_language", raw=target_language)
        return
    idents = [
        el for el in root.iter() if isinstance(el.tag, str) and _localname(el) == "identification"
    ]
    if not idents:
        logger.warning("frbr_meta_patch_skipped", reason="no_identification")
        return
    for ident in idents:
        _patch_identification(ident, iso, expression_date)


# The agent a translated expression is `by`, matching the `FRBRauthor href` the
# emitter writes.
TRANSLATION_AGENT = "#codify"


def _patch_identification(
    ident: etree._Element, iso: str, expression_date: str | None = None
) -> None:
    """Rewrite one ``<identification>`` block's expression and manifestation to ``iso``,
    derived from that block's own work URI, and declare what it is a translation of.

    ``expression_date`` is the date the translation was produced, which is the date
    this expression came into being. Absent it the source's date is kept, which is
    what the pre-``FRBRtranslation`` write path did.
    """
    work = _find_local(ident, "FRBRWork")
    expression = _find_local(ident, "FRBRExpression")
    work_uri_el = _find_local(work, "FRBRuri") if work is not None else None
    work_uri = work_uri_el.get("value") if work_uri_el is not None else None
    if not work_uri or expression is None:
        logger.warning("frbr_meta_patch_skipped", reason="no_work_uri_or_expression")
        return
    # Both read before the rewrites below overwrite them: they are the source
    # expression's, and naming the source is the whole point of the declaration.
    source_uri_el = _find_local(expression, "FRBRuri")
    source_uri = source_uri_el.get("value") if source_uri_el is not None else None
    lang_el = _find_local(expression, "FRBRlanguage")
    source_language = lang_el.get("language") if lang_el is not None else None

    date_el = _find_local(expression, "FRBRdate")
    if expression_date and date_el is not None:
        # Keeping the source's date would have this English text existing in
        # 1999, and Cobalt rebuilds the expression URI's `@date` from here, so a
        # stale element also reverts the URI on the next write.
        date_el.set("date", expression_date)
        date_el.set("name", "translation")
    stated = date_el.get("date") if date_el is not None else expression_date
    expr_uri = build_frbr_expression_uri(work_uri, iso, stated)
    # An attachment's own block addresses a component of the work, so its
    # FRBRthis carries a `/!name` tail that the work-level URI does not. Rebase
    # the tail onto the new expression rather than dropping it: without this the
    # annex would claim to be the whole document.
    _rebase(expression, "FRBRthis", expr_uri)
    _rebase(expression, "FRBRuri", expr_uri)
    if lang_el is not None:
        lang_el.set("language", iso)
    _declare_translation(expression, source_language, source_uri, iso)
    manifestation = _find_local(ident, "FRBRManifestation")
    if manifestation is not None:
        # The URI names the expression this manifests, so it carries the
        # expression's date. The manifestation's own date is when this file was
        # produced, which on a force retranslate days later is today, not the day
        # the expression was first created. The one place a clock belongs here:
        # it is a fact about the file, not part of any identity.
        _rebase(manifestation, "FRBRthis", expr_uri, suffix=".akn")
        _rebase(manifestation, "FRBRuri", expr_uri, suffix=".akn")
        manif_date = _find_local(manifestation, "FRBRdate")
        if expression_date and manif_date is not None:
            manif_date.set("date", date.today().isoformat())
            manif_date.set("name", "Generation")


def _declare_translation(
    expression: etree._Element, source_language: str | None, source_uri: str | None, iso: str
) -> None:
    """Say in the AKN that this expression is a translation, and of what.

    The standard has an element for exactly this, so the fact belongs in the document
    rather than in a column only this system knows to read. Position is part of the
    contract: ``exprProperties`` orders ``FRBRtranslation`` last, after
    ``FRBRlanguage``, so appending is right only by accident of what the emitter puts
    at the end of the block.

    Built before any inherited declaration is removed. The write path clones the
    source tree, so retranslating stacks one declaration per pass unless the old one
    goes, but removing it first would strip the provenance a block already had from
    one this then refuses to declare.
    """
    if not source_language or not source_uri:
        logger.warning(
            "frbr_translation_not_declared",
            reason="no_source_language_or_uri",
            language=source_language,
            uri=source_uri,
        )
        return
    if source_language == iso:
        # Not a translation. Declaring one would have the expression cite its own
        # previous URI as the thing it was translated from.
        logger.warning("frbr_translation_not_declared", reason="same_language", language=iso)
        return
    el = etree.Element(f"{{{AKN_NS}}}FRBRtranslation")
    el.set("fromLanguage", source_language)
    el.set("href", source_uri)
    el.set("by", TRANSLATION_AGENT)
    # These are machine translations. The source expression stays the one a
    # reader cites.
    el.set("authoritative", "false")
    for stale in [c for c in expression if _is_akn(c, "FRBRtranslation")]:
        expression.remove(stale)
    langs = [i for i, c in enumerate(expression) if _is_akn(c, "FRBRlanguage")]
    expression.insert(langs[-1] + 1 if langs else len(expression), el)


def _rebase(parent: etree._Element, name: str, base: str, *, suffix: str = "") -> None:
    """Point ``parent``'s ``name`` child at ``base``, preserving any trailing
    ``/!component`` the old value addressed.

    ``suffix`` is the manifestation format, which belongs after the component
    (``/eng@date/!schedule_1.akn``); appending it to the base would put the format
    before the component and emit an invalid FRBR URI.
    """
    el = _find_local(parent, name)
    if el is None:
        return
    old = el.get("value") or ""
    _, sep, component = old.partition("/!")
    if sep:
        component = component.removesuffix(suffix) if suffix else component
        el.set("value", f"{base}/!{component}{suffix}")
    else:
        el.set("value", f"{base}{suffix}")


def _patch_element(el: etree._Element, block: TranslatedBlock) -> None:
    """Replace `<heading>` text and each direct non-empty `<p>` under
    intro/content/wrapUp with the block's translated heading + lines. Empty
    `<p>` slots are skipped because the source extractor skips them too, so
    `block.lines[i]` maps positionally onto the i-th non-empty source `<p>`.
    Children with their own eId (`<paragraph>`, `<blockList>/<item>`) are
    patched under their own iteration."""
    from codify.translate.anchors import _BODY_WRAPPERS

    # A block keyed on a bare <p> (flat annex prose under <mainBody>) has no
    # wrappers to walk; patch the element's own text.
    if _localname(el) == "p":
        if block.lines:
            _set_p_text(el, block.lines[0])
        return

    if block.heading is not None:
        for child in el:
            if isinstance(child.tag, str) and _localname(child) == "heading":
                _clear_children(child)
                child.text = _sanitise_target_text(block.heading)
                break

    idx = 0
    for wrapper in el:
        if not isinstance(wrapper.tag, str) or _localname(wrapper) not in _BODY_WRAPPERS:
            continue
        for p in wrapper:
            if not isinstance(p.tag, str) or _localname(p) != "p":
                continue
            if not _p_has_text(p):
                continue
            if idx >= len(block.lines):
                return
            _set_p_text(p, block.lines[idx])
            idx += 1


def _hebrew_ordinal(text: str) -> str | None:
    """Return the Hebrew letter-numeral form of an Arabic ordinal word,
    or None if not a recognised ordinal. Same source table as the Latin
    path so both targets stay in lockstep on which ordinals they handle."""
    latin = latinise_arabic_ordinal(text)
    if latin is None:
        return None
    # Contract: `latinise_arabic_ordinal` returns a str(int) drawn from
    # `AR_ORDINAL_TO_INT.values()`; the module-level assertion above
    # guarantees every such int has a Hebrew mapping.
    return _INT_TO_HEBREW_NUMERAL[int(latin)]


def _canonicalise_num_text(root: etree._Element, target_language: str | None) -> None:
    """In-place rewrite of every ``<num>`` text into the reader's script.

    English / other Latin-script targets get a fully Latin fold: Arabic
    ordinal words → digits (``الأول → 1``), Arabic-Indic digits → Latin
    (``٥ → 5``), single-letter Arabic abjad enumerators → Latin
    (``أ. → a.``). Hebrew targets get the same digit fold (Israeli legal
    typography uses Latin digits) but ordinals and enumerators emit the
    Hebrew letter-numeral form (``الأول → א``, ``أ. → א.``) so the
    reader sees consistent script rather than the source's Arabic.
    Unknown targets fall back to the Latin path.
    """
    hebrew = _is_hebrew_target(target_language)
    abjad_map = _AR_ABJAD_TO_HEBREW if hebrew else _AR_ABJAD_TO_LATIN

    for el in root.iter():
        if not isinstance(el.tag, str) or _localname(el) != "num":
            continue
        if not el.text:
            continue
        # Ordinal words first. Multi-character path is unambiguous and
        # matches structural containers ("الباب الأول" → num=الأول).
        ordinal = _hebrew_ordinal(el.text) if hebrew else latinise_arabic_ordinal(el.text)
        if ordinal is not None:
            el.text = ordinal
            continue
        el.text = el.text.translate(_DIGIT_TO_ASCII)
        # Strip joiners (TATWEEL + ZWNJ/ZWJ) so `هـ` matches the bare `ه`
        # lookup regardless of typographic elongation.
        stripped = el.text.translate(JOINER_STRIP_TABLE).strip()
        if len(stripped) >= 2 and stripped[0] in abjad_map and stripped[1] in ".-":
            el.text = f"{abjad_map[stripped[0]]}{stripped[1]}"
        elif stripped in abjad_map:
            el.text = abjad_map[stripped]
        elif hebrew:
            # The legacy-Latin branch, per `_LATIN_ABJAD_TO_HEBREW` and
            # `_LATIN_DIGIT_TO_HEBREW` above.
            if len(stripped) >= 2 and stripped[0] in _LATIN_ABJAD_TO_HEBREW and stripped[1] in ".-":
                el.text = f"{_LATIN_ABJAD_TO_HEBREW[stripped[0]]}{stripped[1]}"
            elif stripped in _LATIN_ABJAD_TO_HEBREW:
                el.text = _LATIN_ABJAD_TO_HEBREW[stripped]
            elif stripped in _LATIN_DIGIT_TO_HEBREW:
                el.text = _LATIN_DIGIT_TO_HEBREW[stripped]


def apply_translation_to_akn(
    source_xml: str,
    blocks: list[TranslatedBlock],
    preface_lines: list[str],
    target_language: str | None = None,
    *,
    preamble_lines: list[str] | None = None,
    conclusions_lines: list[str] | None = None,
    expression_date: str | None = None,
) -> str:
    """Clone source AKN and patch per-eId heading + `<p>` text in place.

    Element count and eId set are source's by construction. Missing blocks
    leave source text intact (the fallback path already surfaces a flag).
    ``target_language`` selects the num-canonicalisation table so Hebrew
    targets emit Hebrew letter numerals and enumerators rather than Latin.
    ``preamble_lines``, when given, patch the preamble slots (enacting
    formula + recitals); None leaves the preamble untouched. ``conclusions_lines``
    does the same for the attestation, which sits after `<body>` and so is
    reached by neither the per-eId body loop nor the front-matter patchers.
    ``expression_date`` is the date the translation was produced, which becomes
    the translated expression's own date and the `@date` in its URI."""
    root = etree.fromstring(source_xml.encode("utf-8"))
    blocks_by_eid = {b.eid: b for b in blocks}

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        eid = el.get("eId")
        if not eid:
            continue
        block = blocks_by_eid.get(eid)
        if block is not None:
            _patch_element(el, block)

    _patch_preface(root, preface_lines)
    if preamble_lines:
        _patch_preamble(root, preamble_lines)
    if conclusions_lines:
        _patch_conclusions(root, conclusions_lines)
    if target_language:
        _patch_frbr_meta(root, target_language, expression_date)
    _canonicalise_num_text(root, target_language)

    return cast(str, etree.tostring(root, encoding="unicode"))


__all__ = [
    "apply_translation_to_akn",
    "conclusions_p_slots",
    "preamble_p_slots",
    "preface_p_slots",
]
