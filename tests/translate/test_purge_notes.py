"""Tests for the translation-notes purge helper."""

from __future__ import annotations

from lxml import etree

from codify.akn import AKN_NS
from codify.translate.purge_notes import purge_notes_from_tree


def _wrap(preface_body: str) -> etree._Element:
    xml = f"""<akomaNtoso xmlns="{AKN_NS}">
      <act name="act">
        <meta/>
        <preface>{preface_body}</preface>
        <body/>
      </act>
    </akomaNtoso>"""
    return etree.fromstring(xml.encode())


def test_purge_removes_named_block() -> None:
    root = _wrap(
        '<block name="translation-notes">'
        "<p>TRANSLATION NOTES (binding).</p>"
        "<p>Defined terms:</p>"
        "<p>- x → y</p>"
        "</block>"
    )
    assert purge_notes_from_tree(root) == 1
    assert not root.findall(f".//{{{AKN_NS}}}block")


def test_purge_removes_bare_notes_p_prose() -> None:
    """Some legacy versions land a bare ``<p>TRANSLATION NOTES ...`` under
    ``<preface>`` without a wrapping named block. The purge must catch
    the sentinel prose too so those deliveries end up clean."""
    root = _wrap("<p>TRANSLATION NOTES (binding).</p><p>Genuine preface prose that must stay.</p>")
    assert purge_notes_from_tree(root) == 1
    remaining = root.findall(f".//{{{AKN_NS}}}p")
    assert len(remaining) == 1
    assert remaining[0].text == "Genuine preface prose that must stay."


def test_purge_is_idempotent_on_clean_tree() -> None:
    root = _wrap("<p>Preface without notes.</p>")
    assert purge_notes_from_tree(root) == 0
    assert len(root.findall(f".//{{{AKN_NS}}}p")) == 1


def test_purge_ignores_named_blocks_of_other_kinds() -> None:
    """Only ``name="translation-notes"`` blocks are removed; other named
    blocks (crossHeading, motivation, unrelated editorial anchors) stay."""
    root = _wrap(
        '<block name="crossHeading"><p>Cross heading text.</p></block>'
        '<block name="translation-notes"><p>TRANSLATION NOTES (binding).</p></block>'
    )
    assert purge_notes_from_tree(root) == 1
    surviving = root.findall(f".//{{{AKN_NS}}}block")
    assert len(surviving) == 1
    assert surviving[0].get("name") == "crossHeading"


def test_purge_removes_sentinel_wrapped_in_inline_children() -> None:
    """Sentinel prose can land as ``<p><b>TRANSLATION NOTES</b> ...</p>``
    when the injector rendered emphasis; ``p.text`` alone reads only
    what precedes the first child and would miss it. Concatenating
    descendant text via ``itertext()`` catches the class."""
    root = _wrap("<p><b>TRANSLATION NOTES</b> (binding).</p><p>Kept prose.</p>")
    assert purge_notes_from_tree(root) == 1
    remaining = root.findall(f".//{{{AKN_NS}}}p")
    assert len(remaining) == 1
    assert remaining[0].text == "Kept prose."


def test_purge_walks_component_prefaces() -> None:
    """Multi-document AKN (act with attached amendment) has one preface
    per document; both must be swept so a legacy consolidation ships clean."""
    xml = f"""<akomaNtoso xmlns="{AKN_NS}">
      <act name="act">
        <meta/>
        <preface><block name="translation-notes"><p>TN 1</p></block></preface>
        <body/>
      </act>
      <components>
        <component>
          <bill name="bill">
            <meta/>
            <preface><block name="translation-notes"><p>TN 2</p></block></preface>
            <body/>
          </bill>
        </component>
      </components>
    </akomaNtoso>"""
    root = etree.fromstring(xml.encode())
    assert purge_notes_from_tree(root) == 2
