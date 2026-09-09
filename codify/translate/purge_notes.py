"""Purge the translation-notes glossary block from stored AKN. Older versions carry
the binding-prompt payload as a `<block name="translation-notes">` under
`<preface>`, leaking glossary prose into delivered PDF and TXT; the reviewer copy
lives on `translation_runs.notes`, so removal is lossless. Pure function; the
admin-op step wraps it in `update_repaired_akn` for the immutable-versions gate.
"""

from __future__ import annotations

from lxml import etree

from codify.akn import AKN_NS

ANCHOR_NAME = "translation-notes"
_NOTES_PROSE_PREFIX = "TRANSLATION NOTES"


def _akn(tag: str) -> str:
    return f"{{{AKN_NS}}}{tag}"


def _rendered_text(p: etree._Element) -> str:
    """Concatenate the element's text plus its descendants' text so a
    sentinel wrapped in an inline (``<b>``, ``<i>``) is still detected.
    ``p.text`` alone reads only what precedes the first child."""
    return "".join(p.itertext())


def purge_notes_from_tree(root: etree._Element) -> int:
    """Remove every ``<block name="translation-notes">`` from any ``<preface>``, plus any
    bare ``<p>`` whose text starts with the notes-glossary sentinel (older versions
    where the injector ran without the named-block wrapper). Returns the count removed;
    idempotent, so a clean tree returns 0 and the caller can skip the write.
    """
    removed = 0
    preface_tag = _akn("preface")
    block_tag = _akn("block")
    p_tag = _akn("p")

    for preface in root.iter(preface_tag):
        for block in list(preface.findall(block_tag)):
            if block.get("name") == ANCHOR_NAME:
                preface.remove(block)
                removed += 1
        for p in list(preface.findall(p_tag)):
            if _rendered_text(p).lstrip().startswith(_NOTES_PROSE_PREFIX):
                preface.remove(p)
                removed += 1
    return removed


__all__ = ["ANCHOR_NAME", "purge_notes_from_tree"]
