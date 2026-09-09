"""Rewrite `<num>` text in a stored AKN into the target's canonical form, the same fold
`apply_translation_to_akn` runs at write time. The Hebrew lane also converts legacy
Latin `<num>` from target-blind writes to Hebrew letter numerals, so a backfill
converges on a fresh Hebrew translation. Returns the mutated XML and a change count,
or ``(None, 0)`` when the tree already matches. The admin-op step wraps it in
`update_repaired_akn`.
"""

from __future__ import annotations

from lxml import etree

from codify.translate.write import _canonicalise_num_text


def recanonicalise_nums(akn_xml: str, target_language: str) -> tuple[str | None, int]:
    """Return ``(new_xml, changed_count)`` or ``(None, 0)`` when the
    canonicalised tree matches the input byte-for-byte. Runs the same
    pass a fresh translation would run, so a re-translation and a
    backfill converge on identical output for the same target."""
    root = etree.fromstring(akn_xml.encode("utf-8"))
    before_nums = _num_iter_root(root)
    _canonicalise_num_text(root, target_language)
    after_nums = _num_iter_root(root)
    changed = sum(1 for pair in zip(before_nums, after_nums, strict=True) if pair[0] != pair[1])
    if changed == 0:
        return None, 0
    return etree.tostring(root, encoding="unicode"), changed


def _num_iter_root(root: etree._Element) -> list[str]:
    """Collect ``<num>`` text from an in-memory tree so caller can diff
    before / after without re-parsing serialised XML."""
    return [
        (el.text or "")
        for el in root.iter()
        if isinstance(el.tag, str) and etree.QName(el).localname == "num"
    ]


__all__ = ["recanonicalise_nums"]
