"""Synthetic target-text cases cover Markdown and invisible separators."""

from lxml import etree

from codify.translate.translate_bodies import TranslatedBlock
from codify.translate.write import (
    _sanitise_target_text,
    apply_translation_to_akn,
)

# --- Markdown emphasis stripping (v38-K-01) ------------------------------------


def test_double_asterisk_bold_collapses_to_inner():
    """Markdown punctuation must not survive as literal heading text."""
    assert _sanitise_target_text("**Works:**") == "Works:"


def test_double_underscore_bold_collapses_to_inner():
    """`__Emphasis__`, same failure mode as `**bold**`, different marker."""
    assert _sanitise_target_text("__Emphasis__") == "Emphasis"


def test_single_asterisk_is_left_alone():
    """Single `*` occurs in legitimate positions (footnote markers,
    transliteration hints). Only paired `**` collapses."""
    assert _sanitise_target_text("see *ibid* below") == "see *ibid* below"


def test_single_underscore_is_left_alone():
    """Same reasoning as single `*`: `_ibid_` and `snake_case_variable`
    ship through the sanitiser unchanged. Only paired `__` collapses."""
    assert _sanitise_target_text("see _ibid_ below") == "see _ibid_ below"
    assert _sanitise_target_text("snake_case_variable") == "snake_case_variable"


def test_bold_span_across_punctuation():
    """`**the Tenders Department:**`, the emphasis wraps a phrase with a
    colon and trailing space; the sanitiser must not fumble the punctuation."""
    assert (
        _sanitise_target_text("**the Tenders Department:** definitions apply")
        == "the Tenders Department: definitions apply"
    )


# --- Invisible-character restoration (v38-J-01) --------------------------------


def test_shy_between_letters_becomes_hyphen():
    """`Anti­Corruption` (SHY sits where a hyphen should), the reader
    sees `AntiCorruption` in the PDF and search misses `Anti-Corruption`.
    Restore the hyphen."""
    assert _sanitise_target_text("Anti­Corruption") == "Anti-Corruption"


def test_zwsp_between_letters_becomes_hyphen():
    """ZWSP U+200B has the same failure mode as SHY: invisible token where
    a hyphen should be."""
    assert _sanitise_target_text("services​related") == "services-related"


def test_zwnj_between_letters_becomes_hyphen():
    """ZWNJ U+200C: same class."""
    assert _sanitise_target_text("non‌agricultural") == "non-agricultural"


def test_zwj_between_letters_becomes_hyphen():
    """ZWJ U+200D: same class."""
    assert _sanitise_target_text("ill‍treatment") == "ill-treatment"


def test_invisible_next_to_whitespace_is_stripped():
    """When the invisible is not between two word characters, e.g.
    whitespace-adjacent or at a string edge, no hyphen belongs there. Strip."""
    assert _sanitise_target_text("some ­text") == "some text"
    assert _sanitise_target_text("​text") == "text"
    assert _sanitise_target_text("text​") == "text"


def test_multiple_invisibles_in_one_word():
    """`non­aligned​movement`, two different invisibles in one span,
    both between word characters. Both restore."""
    assert _sanitise_target_text("non­aligned​movement") == "non-aligned-movement"


# --- Composed cases ------------------------------------------------------------


def test_bold_around_invisible_hyphen_repair():
    """`**Anti­Corruption**`, bold wraps a SHY-broken compound.
    Sanitiser strips the bold, then repairs the hyphen."""
    assert _sanitise_target_text("**Anti­Corruption**") == "Anti-Corruption"


def test_plain_text_untouched():
    """A clean translated sentence must not be altered."""
    text = "The Council shall convene annually and issue a report."
    assert _sanitise_target_text(text) == text


# --- Residual-markdown detection (unclosed pair) ------------------------------


def test_unclosed_bold_passes_through_but_logs_warn():
    """`**Overview of duties` with no closing pair falls through the paired
    substitutions and would ship literal asterisks to the reader. Preserve
    the string (blind-stripping a lone `**` could destroy legitimate
    emphasis a jurist wants to keep) but log at warn level so operators
    can find the offending provision."""
    import structlog
    from structlog.testing import capture_logs

    with capture_logs() as captured:
        result = _sanitise_target_text("**Overview of duties")
    # Reset structlog defaults so the capture handler is torn down cleanly.
    structlog.reset_defaults()

    assert result == "**Overview of duties"
    assert any(
        rec.get("event") == "sanitise_target_text_residual_markdown"
        and rec.get("log_level") == "warning"
        for rec in captured
    )


def test_closed_bold_does_not_trigger_residual_warn():
    """Companion to the residual test: a well-formed `**bold**` collapses
    cleanly and does not emit the residual-markdown warning. Guards against
    the log firing on every provision if a maintainer widens the regex."""
    import structlog
    from structlog.testing import capture_logs

    with capture_logs() as captured:
        result = _sanitise_target_text("**bold text**")
    structlog.reset_defaults()

    assert result == "bold text"
    assert not any(rec.get("event") == "sanitise_target_text_residual_markdown" for rec in captured)


# --- End-to-end via apply_translation_to_akn -----------------------------------


def test_sanitiser_runs_end_to_end_on_p_and_heading():
    """Confirm the sanitiser sits on the actual write path, not just when
    called directly. Feed a source AKN + a block whose heading and body both
    carry both defect classes; assert on the resulting XML."""
    source_xml = """<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act>
        <body>
          <article eId="art_1">
            <num>1</num>
            <heading>SOURCE HEADING</heading>
            <content><p eId="art_1__p_1">source p</p></content>
          </article>
        </body>
      </act>
    </akomaNtoso>"""
    block = TranslatedBlock(
        eid="art_1",
        heading="**Definitions**",
        lines=["Anti­Corruption is prohibited."],
    )
    result_xml = apply_translation_to_akn(
        source_xml, [block], preface_lines=[], target_language="en"
    )
    root = etree.fromstring(result_xml.encode("utf-8"))
    ns = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
    heading = root.find(".//a:heading", ns)
    para = root.find(".//a:p", ns)
    assert heading is not None and heading.text == "Definitions"
    assert para is not None and para.text == "Anti-Corruption is prohibited."
