"""An inline note survives every surface that learned to drop lifted ones.

The only input where the tag and the placement disagree, so the only one that
holds the predicate in place. An inline note is part of the sentence: dropping
it removes text from the reader, the exports and the translated line.
"""

from __future__ import annotations

from lxml import etree

from codify.storage.documents import _push_inline
from codify.translate.anchors import provision_text
from codify.translate.write import _set_p_text

NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_TEXT = "Recital (see OJ L 1) applies."


def _p() -> etree._Element:
    return etree.fromstring(
        f'<p xmlns="{NS}">Recital (<authorialNote><p>see OJ L 1</p></authorialNote>)'
        " applies.</p>".encode()
    )


def test_the_translation_extractor_reads_the_whole_sentence() -> None:
    assert provision_text(_p()) == _TEXT


def test_the_translated_line_replaces_an_inline_note_rather_than_keeping_it() -> None:
    """The translated line already carries the note, because the note is part of
    the sentence. Preserving the element too would leave the English inside the
    Spanish, which is what treating every `<authorialNote>` as lifted does."""
    p = _p()
    _set_p_text(p, "Considerando (véase DO L 1) se aplica.")
    assert "".join(p.itertext()) == "Considerando (véase DO L 1) se aplica."


def test_a_lifted_note_below_the_top_level_is_not_deleted_by_the_write() -> None:
    """`provision_text` excludes lifted notes at any depth, so a nested one is
    absent from the line the translator returns. The writer has to collect at
    the same depth or the note goes back nowhere."""
    p = etree.fromstring(
        f'<p xmlns="{NS}">Operative.<span><authorialNote placement="bottom">'
        "<p>AMENDED</p></authorialNote></span></p>".encode()
    )
    _set_p_text(p, "Texto.")
    assert "AMENDED" in "".join(p.itertext())


def test_the_server_projection_keeps_it_in_the_paragraph() -> None:
    """A lifted note becomes its own node; an inline one must not, or the reader
    renders a footnote marker mid-sentence with no sentence around it."""
    nodes: list[object] = []
    _push_inline(_p(), nodes)
    rendered = "".join(getattr(n, "text", "") or "" for n in nodes)
    assert "see OJ L 1" in rendered
