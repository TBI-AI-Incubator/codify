"""Synthetic citation lists test repeated labels, including nonadjacent nouns."""

from __future__ import annotations

from codify.translate.audit import count_doubled_citation_nouns

NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _doc(text: str) -> str:
    return (
        f'<akomaNtoso xmlns="{NS}"><act><body>'
        f'<article eId="art_23"><content><p eId="art_23__p_1">{text}</p></content></article>'
        "</body></act></akomaNtoso>"
    )


def test_a_noun_inside_a_parenthetical_under_a_plural_is_found() -> None:
    result = count_doubled_citation_nouns(
        _doc("Articles (Article 31), and (Article 44) shall be repealed."), "English"
    )
    assert result is not None
    hits, by_eid = result
    assert hits >= 1
    assert "art_23" in by_eid


def test_two_nouns_inside_one_parenthetical_are_found() -> None:
    result = count_doubled_citation_nouns(_doc("Articles (Article 9 bis Article 5)."), "English")
    assert result is not None
    assert result[0] >= 1


def test_synthetic_list_defect_is_found_and_names_its_provision() -> None:
    """An invented list preserves the nonadjacent duplicate-label pattern."""
    result = count_doubled_citation_nouns(
        _doc(
            "Articles (Article 9 bis Article 5), (Article 31), and (Article 44) "
            "describe the fictional observatory intake process."
        ),
        "English",
    )
    assert result is not None
    assert result[0] >= 2
    assert sorted(result[1]) == ["art_23"]


def test_the_corrected_text_passes() -> None:
    result = count_doubled_citation_nouns(
        _doc(
            "Articles (9 bis 5), (31), and (44) describe the fictional observatory intake process."
        ),
        "English",
    )
    assert result == (0, {})


def test_an_ordinary_citation_does_not_fire() -> None:
    """A single noun with its number, and a compound cross-reference, are both
    correct. Flagging them would put every law in front of a reviewer."""
    for text in (
        "Article (31) describe the fictional observatory intake process.",
        "Article (6 bis) of the original Law is amended.",
        "in accordance with Article 4 paragraph 9 of this Law",
        "Chapter 3 sets out the penalties.",
        "Articles 5 and 6 shall be read together.",
    ):
        assert count_doubled_citation_nouns(_doc(text), "English") == (0, {}), text


def test_a_language_with_no_patterns_reports_that_it_did_not_run() -> None:
    """English is the only one written. A language nobody wrote patterns for
    has not been checked, which must not read as a clean zero."""
    assert count_doubled_citation_nouns(_doc("anything"), "Hebrew") is None
    assert count_doubled_citation_nouns(_doc("anything"), None) is None


def test_unparseable_input_reports_that_it_did_not_run() -> None:
    assert count_doubled_citation_nouns("<not xml", "English") is None
    assert count_doubled_citation_nouns("<akomaNtoso/>", "English") is None
