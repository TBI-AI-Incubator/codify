"""An instrument that adopts another body's numbered text. Synthetic vocabulary
throughout: a test written against one tradition's wording says nothing about the
mechanism."""

from __future__ import annotations

from codify.jurisdictions import load_config
from codify.pipeline.enrich.adoption import adopts_external_text

MARKERS = ["ratification of the agreement", "publication of the convention"]
# Both article-based, so the same fixture scans under either. `xz` declares the
# markers and `xy` declares none, which is what makes the pair a control.
DECLARES = "xz"
DECLARES_NOT = "xy"


def _config(markers: list[str] | None = None):
    config = load_config("xz")
    assert config is not None
    return config.model_copy(update={"adoption_markers": markers or []})


def _instrument(purpose: str, adopted_articles: int = 12) -> str:
    """A short operative part, then somebody else's text numbering from its own 1."""
    lines = [f"DECREE No. 14 of 2026, {purpose}", "", "Article 1. The agreement is adopted.", ""]
    lines += ["Article 2. This decree takes effect on publication.", "", "THE AGREEMENT", ""]
    for i in range(1, adopted_articles + 1):
        lines += [f"Article {i}. Adopted text, article {i}.", ""]
    return "\n".join(lines) + "\n"


def test_an_instrument_declaring_adoption_is_recognised() -> None:
    assert adopts_external_text(_instrument("ratification of the agreement"), _config(MARKERS))


def test_an_ordinary_instrument_is_not() -> None:
    """The point of the check: a document whose numbering restarts is not by itself
    adopting anything, and reading it as such would excuse a real defect."""
    assert not adopts_external_text(
        _instrument("concerning municipal boundaries"), _config(MARKERS)
    )


def test_a_law_that_merely_cites_an_agreement_is_not_adopting_it() -> None:
    """The distinction the title block draws. A law citing a convention in its recitals
    says the same words a little further down, and reading the whole opening page takes
    those as its purpose, suppressing counts over every document that quotes one."""
    body = "AN ACT concerning municipal boundaries\n\n"
    body += "Article 1. " + ("ordinary provision text. " * 20) + "\n\n"
    body += "Whereas the ratification of the agreement of 1998 required it,\n"
    assert not adopts_external_text(body, _config(MARKERS))


def test_a_jurisdiction_declaring_no_markers_reports_nothing() -> None:
    """Silence, not a finding that the document carries nothing. Most jurisdictions
    declare none, and a default that guessed would suppress counts everywhere."""
    assert not adopts_external_text(_instrument("ratification of the agreement"), _config([]))
    assert not adopts_external_text(_instrument("ratification of the agreement"), None)


def test_a_marker_broken_across_lines_still_matches() -> None:
    """OCR reflows a phrase mid-line, so whitespace in a marker matches any run."""
    text = _instrument("ratification of the\n   agreement")
    assert adopts_external_text(text, _config(MARKERS))


def _scan_tail(text: str, country: str):
    from codify.pipeline.enrich.anchors import cached_regex, scan_anchors_with_ambiguity

    scan = scan_anchors_with_ambiguity(
        text, cached_regex(country, "act"), country=country, doctype="act"
    )
    return next((sp for sp in scan.ambiguity if sp.kind == "untwinned_tail"), None)


def _two_series(purpose: str, adopted_bodied: int = 2) -> str:
    """An operative part listing eight units, then an adopted text bodying a few."""
    lines = [f"DECREE No. 14 of 2026, {purpose}", "", "CONTENTS", ""]
    lines += [f"Article {i}. Heading number {i}" for i in range(1, 9)]
    lines += ["", "THE AGREEMENT", ""]
    for i in range(1, adopted_bodied + 1):
        lines += [f"Article {i}. Adopted text {i}.", f"  Body of adopted article {i}.", ""]
    return "\n".join(lines) + "\n"


def test_an_adopting_instrument_suppresses_the_truncation_reading() -> None:
    """The wiring, exercised both ways on one document. `xz` declares the markers, so
    the suppression fires; `xy` declares none, so the same text still reads as
    truncated. Without the second half the test would pass with the call deleted."""
    text = _two_series("ratification of the agreement")
    assert _scan_tail(text, DECLARES_NOT) is not None, "the fixture no longer produces a tail"
    assert _scan_tail(text, DECLARES) is None, "the adopting instrument was still read as cut"


def test_an_ordinary_instrument_in_the_same_jurisdiction_is_still_read() -> None:
    """Suppression is keyed on the declaration, not on the jurisdiction. A document
    that declares nothing is read as before even where markers exist."""
    assert _scan_tail(_two_series("concerning municipal boundaries"), DECLARES) is not None


def test_a_cut_adopted_text_is_currently_hidden() -> None:
    """A known limitation, asserted so it cannot change unnoticed. Suppression is
    whole-document because nothing here knows where the adopted text begins, so how
    much of it is present makes no difference. Invert this when segmentation lands."""
    for bodied in (1, 2, 6):
        text = _two_series("ratification of the agreement", adopted_bodied=bodied)
        assert _scan_tail(text, DECLARES) is None, f"{bodied} bodied was read as cut"


def test_the_suppression_is_recorded_rather_than_only_logged() -> None:
    """A landed instrument otherwise reads as though the check had examined it and
    found nothing, which is the same shape as a clean result and not the same fact."""
    from codify.pipeline.enrich.anchors import cached_regex, scan_anchors_with_ambiguity

    text = _two_series("ratification of the agreement")
    scan = scan_anchors_with_ambiguity(
        text, cached_regex(DECLARES, "act"), country=DECLARES, doctype="act"
    )
    recorded = [sp for sp in scan.ambiguity if sp.kind == "adoption_suppressed"]
    assert recorded, "the check declined to run and said nothing about it"
    assert recorded[0].resolved is True, "a recorded decision is not an open ambiguity"
    assert recorded[0].detail["reason"]
