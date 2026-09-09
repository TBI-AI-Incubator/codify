"""Configuration placeholders must not become documentary text."""

import pytest

from codify.akn import AKN_NS
from codify.jurisdictions import EnactingFormula
from codify.pipeline.enrich.enacting import inject_enacting_formula


@pytest.mark.parametrize(
    "slot",
    [
        "[SUBJECT]",
        "[Minister's name]",
        "[date]",
        "[...]",
        "[…]",
        "[tên văn bản]",
        "{day}",
        "{Parent Act}",
        "...",
        "…",
        "[12 months]",
    ],
)
def test_unexpanded_template_leaves_document_unchanged(slot: str) -> None:
    source = f'<akomaNtoso xmlns="{AKN_NS}"><act><body><p>Annex text.</p></body></act></akomaNtoso>'
    assert (
        inject_enacting_formula(source, EnactingFormula(text=f"Enacted concerning {slot}."))
        == source
    )


@pytest.mark.parametrize("citation", ["[12]", "[12A]", "[12(1)(a)]", "[12.3A(1)]"])
def test_literal_formula_with_numbered_citation_is_retained(citation: str) -> None:
    source = f'<akomaNtoso xmlns="{AKN_NS}"><act><body><p>Text.</p></body></act></akomaNtoso>'
    text = f"Enacted under section {citation}."
    result = inject_enacting_formula(source, EnactingFormula(text=text))
    assert text in result


def test_existing_source_formula_is_never_rewritten() -> None:
    source = (
        f'<akomaNtoso xmlns="{AKN_NS}"><act><preamble>'
        '<formula name="enactingFormula"><p>Source [word].</p></formula>'
        "</preamble><body/></act></akomaNtoso>"
    )
    assert inject_enacting_formula(source, EnactingFormula(text="Template [SUBJECT].")) == source
