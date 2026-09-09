"""Bluebell parser wrapper: plain text → valid AKN XML."""

import re
from functools import lru_cache
from typing import cast

import structlog
from bluebell.parser import AkomaNtosoParser
from cobalt.uri import FrbrUri
from lxml import etree

from codify.frbr import UNKNOWN_YEAR
from codify.lang import normalise_digits
from codify.pipeline.enrich.arabic_normalise import (
    ARABIC_JOINERS,
    latinise_arabic_ordinal,
    normalise_arabic_in_tree,
)
from codify.pipeline.enrich.demote_duplicate_points import demote_duplicate_points
from codify.pipeline.enrich.scripts.arabic import abjad_index_to_latin

logger = structlog.get_logger()

# XML 1.0 forbids C0 control chars except tab/newline/CR. They reach the parser
# from OCR or LLM body-fill output and crash lxml; strip at the parser boundary
# so every caller is covered regardless of upstream sanitisation.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# Bluebell only accepts canonical AKN roots; localised names map to "act"
# (Cobalt's StructuredDocument.for_document_type only resolves canonicals).
# Derived from bluebell ≥ 3.0; revisit if the grammar grows new top-level roots.
_BLUEBELL_ROOTS = frozenset({"act", "bill", "judgment", "debateReport", "doc"})


def _bluebell_root(doctype: str) -> str:
    return doctype if doctype in _BLUEBELL_ROOTS else "act"


def parse_to_akn(
    bluebell_text: str,
    country: str,
    doctype: str = "act",
    date: str = "",
    number: str = "",
    language: str = "eng",
    subtype: str | None = None,
) -> str:
    """Parse Bluebell plain text to valid AKN XML string. The resulting FRBR URI
    uses the canonical AKN root; the localised label belongs in `laws.doctype`."""
    canonical = _bluebell_root(doctype)
    if canonical != doctype:
        logger.info("doctype_normalised", from_=doctype, to=canonical, country=country)
    # An empty date mints `/akn/{country}/act//{number}`, which Cobalt then
    # refuses to parse, so every later pass that touches the FRBR URI dies on a
    # document we structured perfectly well.
    frbr_uri = FrbrUri(
        country=country,
        locality=None,
        doctype=canonical,
        subtype=subtype,
        actor=None,
        date=date or UNKNOWN_YEAR,
        number=number,
        language=language,
    )
    rejoined, joins, _ = rejoin_citation_lines(bluebell_text, country)
    if joins:
        logger.info("citation_continuation_rejoined", count=joins, country=country)
    cleaned = _CONTROL_CHARS_RE.sub("", _fix_quote_indentation(rejoined))
    parser = AkomaNtosoParser(frbr_uri)
    xml_element = parser.parse_to_xml(cleaned, root=canonical)
    demoted = demote_duplicate_points(xml_element)
    if demoted:
        logger.info("duplicate_points_demoted", count=demoted)
    # eId normalisation runs AFTER demotion so freshly-merged eIds stay
    # ASCII-folded; arabic_normalise runs last because its <p>-walk doesn't
    # care about structural changes upstream.
    normalise_eid_digits(xml_element)
    arabic_fixes = normalise_arabic_in_tree(xml_element, country=country)
    if arabic_fixes:
        logger.info("arabic_text_normalised", count=arabic_fixes)
    return cast(str, etree.tostring(xml_element, pretty_print=True, encoding="unicode"))


# The bis marker and its index, kept apart from the anchored form so a caller
# detecting an eId this arm folds builds from the same source it matches on.
_BIS_SEPARATORS = "-–_"
_BIS_MARK = f"مكرر(?:ة)?[{_BIS_SEPARATORS}]?"
_BIS_IDX = "(?:[0-9]*|[ء-ي])"
_BIS_EID_RE = re.compile(f"^(?P<base>[0-9]+){_BIS_MARK}(?P<idx>[0-9]*|[ء-ي])$")


def bis_num_pattern(digit_class: str, *, without: str = "") -> str:
    """The bis num as a regex. Takes the digit class because a stored eId may carry
    either script, while this arm sees ASCII only: digits normalise before it.
    ``without`` drops separators, for a caller reaching this arm through a path
    that cannot carry them: punctuation inside a trimmed num vetoes the trim."""
    seps = [c for c in _BIS_SEPARATORS if c not in without]
    mark = f"مكرر(?:ة)?[{''.join(seps)}]?" if seps else "مكرر(?:ة)?"
    return f"{digit_class}+{mark}{_BIS_IDX}"


# `_2` from the digit fold, `_dup2` from `ensure_unique_eids`.
_DEDUP_SUFFIX_RE = re.compile(r"_(?:dup)?[0-9]+$")


def _split_dedup(num: str) -> tuple[str, str]:
    """Split a collision suffix off a num. Every fold has to put it back, or `(1)_2`
    trims to `1)_2` with the bracket no longer at an edge."""
    m = _DEDUP_SUFFIX_RE.search(num)
    return (num[: m.start()], m.group()[1:]) if m else (num, "")


# Every arm below needs a matching pattern in the normalise-eids resolver: an arm
# added without one folds eIds no backfill will ever find.
def _latinise_num(num: str) -> str | None:
    """Ordinal word to Latin digit, tolerating a Bluebell ``_<n>`` dedup
    suffix (``الأول_2``). Bis articles ("6 مكرر" → eid segment "6مكرر")
    fold to the ASCII form the anchor scanner uses ("6bis", "15bisa")."""
    # Split the collision suffix once, so every branch below tolerates it. A
    # branch that missed it left the eId non-ASCII while the selector, which
    # reads the whole num, still booked the version.
    base, dedup = _split_dedup(num)
    tail = f"_{dedup}" if dedup else ""

    latin = latinise_arabic_ordinal(base)
    if latin is not None:
        return latin + tail
    bis = _BIS_EID_RE.match(base)
    if bis is not None:
        idx = bis.group("idx")
        if idx and not idx.isascii():
            idx = abjad_index_to_latin(idx) or "x"
        return f"{bis.group('base')}bis{idx}{tail}"
    # A marker letter can arrive bare or trailed by a decorative joiner the point
    # parser allows. The ordinal lookup above already strips the same set.
    bare = base.rstrip(ARABIC_JOINERS)
    if len(bare) == 1 and not bare.isascii():
        letter = abjad_index_to_latin(bare)
        if letter is not None:
            return letter + tail
    return None


# Punctuation a printed marker carries into its eId. The dash span is U+2010 to
# U+2015 because the point parser accepts every one as a terminator.
EID_NUM_EDGE = "()[].,:;-" + "".join(chr(c) for c in range(0x2010, 0x2016)) + "\u060c\u00ab\u00bb"


def _strip_marker_punctuation(eid: str) -> str:
    """Trim marker punctuation from each `__` segment's num part, keeping any
    trailing `_<n>` disambiguator: without that, `(1)_2` trims to `1)_2` because
    the closing bracket is no longer at the segment's edge."""
    segments = eid.split("__")
    for i, segment in enumerate(segments):
        prefix, sep, num = segment.partition("_")
        if not sep or not num:
            continue
        base, dedup = _split_dedup(num)
        trimmed = base.strip(EID_NUM_EDGE)
        # Only a marker whose punctuation is wholly at the edges. A compound num
        # keeps punctuation inside it, and stripping the outer half alone leaves
        # `sec_1(a)` as `sec_1(a`: an unbalanced eId that names nothing.
        if trimmed and trimmed != base and set(trimmed).isdisjoint(EID_NUM_EDGE):
            segments[i] = f"{prefix}_{trimmed}" + (f"_{dedup}" if dedup else "")
    return "__".join(segments)


def _restore_unfoldable_segments(eid: str, folded: str) -> str:
    """Restore any segment the fold could not bring to ASCII. Trimming `(ة)` to `ة`
    reaches no further: the letter carries no abjad rank, so a stripped marker would
    read as repaired while still naming the provision in a script citations cannot
    match. No pass adds or drops a `__`, so the two segment lists align."""
    original, out = eid.split("__"), folded.split("__")
    if len(original) != len(out):
        return folded
    return "__".join(o if not f.isascii() else f for o, f in zip(original, out))


def _ascii_fold_eid(eid: str) -> str:
    """Fold an eId to ASCII: marker punctuation first, then non-ASCII digits, one
    marker letter, and ordinal words. Container eIds derived from ordinal-word ``<num>``
    text (``chp_الأول``) violate AKN 3.0's URL-safe naming, so each ``__``-segment's num
    part folds via the ordinal table, matching compounds like ``الحاديعشر`` too.
    """
    folded = normalise_digits(_strip_marker_punctuation(eid))
    if not folded.isascii():
        parts = folded.split("__")
        for i, part in enumerate(parts):
            prefix, _, num = part.partition("_")
            if not num:
                continue
            latin = _latinise_num(num)
            if latin is not None:
                parts[i] = f"{prefix}_{latin}"
        folded = "__".join(parts)
    # A Latin ordinal word is ASCII, so it survives the script-specific pass
    # above untouched and a mixed-script eId (`chp_الأول__part_Kesatu`) needs
    # both. Runs unconditionally rather than as an else-branch for that reason.
    return _restore_unfoldable_segments(eid, _fold_ordinal_words(folded))


@lru_cache(maxsize=1)
def _ordinal_fold_table() -> dict[str, str]:
    """Declared ordinal words plus their space-collapsed forms.

    Bluebell derives the eId from the `<num>` text with spaces removed, so a
    compound arrives as `KeduaBelas` and never matches the declared `Kedua Belas`.
    """
    from codify.jurisdictions import ordinal_word_folds

    table = dict(ordinal_word_folds())
    for word, value in list(table.items()):
        table.setdefault(word.replace(" ", ""), value)
    return table


def ordinal_eid_words() -> tuple[str, ...]:
    """Every ordinal word an eId can carry, for callers that must detect what the
    fold repairs. Sharing the table stops detection drifting narrower than the fix."""
    return tuple(_ordinal_fold_table())


def _fold_ordinal_words(eid: str) -> str:
    """Fold declared ordinal words in an ASCII eId (`part_Kesatu` to `part_1`)."""
    table = _ordinal_fold_table()
    parts = eid.split("__")
    for i, part in enumerate(parts):
        prefix, sep, num = part.partition("_")
        if not sep or not num:
            continue
        # Bluebell suffixes a duplicate (`part_Kesatu_2`); fold the word, keep the suffix.
        base, dup_sep, dup = num.partition("_")
        folded = table.get(base)  # the table carries each word verbatim and uppercased
        if folded is not None:
            parts[i] = f"{prefix}_{folded}{dup_sep}{dup}"
    return "__".join(parts)


def normalise_eid_digits(root: etree._Element) -> None:
    """Fold non-ASCII digits and ordinal words in eIds so cross-references resolve and eIds
    stay URL-safe. Mutates in place; ``<num>`` keeps its original script.

    Two passes: the first collects the original namespace, the second remaps and
    disambiguates against it and the newly allocated. With one pass an existing ``art_7``
    and a normalising ``art_۷`` both become ``art_7``, the ``eid_uniqueness`` regression.
    Collisions take an ``_2`` suffix
    rather than merging.
    """
    # Pass 1: collect every existing eId so we know what's already taken.
    existing_eids: set[str] = set()
    for el in root.iter():
        eid = el.get("eId")
        if eid:
            existing_eids.add(eid)

    # Pass 2: remap non-ASCII eIds. `taken` holds the originals and the newly
    # allocated, so later collisions suffix cleanly. root.iter() is document order,
    # so a parent's remap is recorded before its descendants are visited.
    taken: set[str] = set(existing_eids)
    eid_remap: dict[str, str] = {}
    for el in root.iter():
        eid = el.get("eId")
        if not eid:
            continue
        # A collision-suffixed parent (chp_الأول beside chp_1 becomes
        # chp_1_2) must carry its suffix into descendant prefixes, else
        # chp_الأول__art_2 folds to chp_1__art_2 under the wrong parent.
        for k in range(eid.count("__"), 0, -1):
            prefix = "__".join(eid.split("__")[:k])
            if prefix in eid_remap:
                eid_with_parent = eid_remap[prefix] + eid[len(prefix) :]
                break
        else:
            eid_with_parent = eid
        normalised = _ascii_fold_eid(eid_with_parent)
        if normalised == eid:
            # Unchanged: either already ASCII, or the fold could not resolve
            # it (the residual sweep below reports the latter).
            continue
        candidate = normalised
        suffix = 2
        # Disambiguate against the ORIGINAL namespace and any allocated remaps.
        while candidate in taken and candidate != eid:
            candidate = f"{normalised}_{suffix}"
            suffix += 1
        # Free the old eId from `taken` so we can take its normalised slot.
        taken.discard(eid)
        taken.add(candidate)
        eid_remap[eid] = candidate
        el.set("eId", candidate)
    # An eId the fold could not resolve (ordinal word outside the table, unexpected
    # script) ships an AKN naming violation that stays invisible until a
    # cross-reference or URL breaks, so surface it now.
    residual = sorted({e for e in taken if not e.isascii()})
    if residual:
        logger.warning("eid_non_ascii_residual", count=len(residual), eids=residual[:20])
    if not eid_remap:
        return
    for el in root.iter():
        href = el.get("href", "")
        if href.startswith("#"):
            target = href[1:]
            if target in eid_remap:
                el.set("href", f"#{eid_remap[target]}")
    logger.info("eid_digits_normalised", count=len(eid_remap))


# Top-level structural keywords that terminate a same-indent QUOTE block
# when no explicit END QUOTE marker is present.
_STRUCT_KEYWORDS = frozenset(
    {
        "PREFACE",
        "BODY",
        "PREAMBLE",
        "CONCLUSIONS",
        "PART",
        "CHAPTER",
        "SUBCHAPTER",
        "TITLE",
        "BOOK",
        "TOME",
        "SECTION",
        "SUBSECTION",
        "ARTICLE",
        "SCHEDULE",
        "APPENDIX",
        "ANNEXURE",
    }
)


def _first_word(line: str) -> str:
    stripped = line.lstrip()
    if not stripped:
        return ""
    return stripped.split()[0]


# Bluebell writes a subdivision as its keyword and number on one line, with the
# body on the lines beneath: `POINT (3)` then `huruf b dilakukan ...`.
_SUBDIVISION_MARKER_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<kw>POINT|PARAGRAPH|SUBPARAGRAPH|INDENT|ITEM)"
    r"[ \t]+\((?P<num>[0-9A-Za-z]{1,4})\)[ \t]*$"
)


@lru_cache(maxsize=32)
def _reference_nouns(country: str) -> tuple[str, ...]:
    from codify.jurisdictions import try_load_config

    config = try_load_config(country) if country else None
    structuring = config.structuring if config else None
    return tuple(n.lower() for n in (structuring.reference_nouns if structuring else ()))


def rejoin_citation_lines(text: str, country: str) -> tuple[str, int, list[int]]:
    """Rejoin a citation the body-fill broke into a subdivision, with a line map.

    "sebagaimana dimaksud dalam Pasal 41 ayat (3) huruf b" splits so the number becomes
    its own subdivision: the body ends mid-sentence at `ayat`, `POINT (3)` opens, and the
    rest becomes its body, colliding with the article's real (3). A genuine subdivision
    never opens directly under a line ending in the noun that introduces a citation's
    number, which separates the two. The marker line is dropped and its number spliced
    back; deeper subdivisions keep their indent and reparent onto the provision that now
    owns the text.
    """
    nouns = _reference_nouns(country)
    if not nouns:
        return text, 0, list(range(len(text.split("\n"))))
    lines = text.split("\n")
    out: list[str] = []
    # Prepared line index to the original it came from, so a caller can report
    # positions in the coordinates of the text its reader is looking at.
    origin: list[int] = []
    joined = 0
    i = 0
    while i < len(lines):
        marker = _SUBDIVISION_MARKER_RE.match(lines[i])
        prev_tail = (
            out[-1].rstrip().split()[-1].lower().strip(".,;:") if out and out[-1].strip() else ""
        )
        if marker and prev_tail in nouns and i + 1 < len(lines) and lines[i + 1].strip():
            out[-1] = f"{out[-1].rstrip()} ({marker.group('num')}) {lines[i + 1].strip()}"
            joined += 1
            i += 2
            continue
        out.append(lines[i])
        origin.append(i)
        i += 1
    return "\n".join(out), joined, origin


def _fix_quote_indentation(text: str) -> str:
    """Rescue QUOTE blocks where the LLM flushed content at the same indent as QUOTE, which
    Bluebell needs one level deeper. Walks each QUOTE/END QUOTE pair and re-indents
    content flush with or shallower than the opener.

    End-of-block priority: `END QUOTE` at the matching indent, per the prompt contract;
    then the first line indented strictly less, being outer scope; then a structural
    keyword at the same indent, as a fallback when the LLM forgets END QUOTE.
    """
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if stripped == "QUOTE":
            quote_indent = len(line) - len(stripped)
            result.append(line)
            i += 1

            end_idx = len(lines)
            ended_by_sentinel = False
            for j in range(i, len(lines)):
                js = lines[j].lstrip()
                if not js:
                    continue
                j_indent = len(lines[j]) - len(js)
                if js == "END QUOTE" and j_indent == quote_indent:
                    end_idx = j
                    ended_by_sentinel = True
                    break
                if j_indent < quote_indent:
                    end_idx = j
                    break
                if j_indent == quote_indent and _first_word(js) in _STRUCT_KEYWORDS:
                    end_idx = j
                    break

            # Re-indent content between QUOTE and end_idx
            for j in range(i, end_idx):
                block_line = lines[j]
                block_stripped = block_line.lstrip()
                if not block_stripped:
                    result.append("")
                    continue
                block_indent = len(block_line) - len(block_stripped)
                if block_indent <= quote_indent:
                    extra = (quote_indent + 2) - block_indent
                    result.append(" " * extra + block_line)
                else:
                    result.append(block_line)

            # Skip past the END QUOTE sentinel if that's what terminated the block
            i = end_idx + 1 if ended_by_sentinel else end_idx
            continue

        # Strip any orphan END QUOTE lines (not paired with a QUOTE above)
        if stripped == "END QUOTE":
            i += 1
            continue

        result.append(line)
        i += 1

    return "\n".join(result)
