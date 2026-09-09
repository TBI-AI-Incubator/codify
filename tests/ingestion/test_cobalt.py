"""Unit tests for Cobalt AKN enrichment wrapper."""

from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.cobalt import enrich_akn


def _make_sample_akn() -> str:
    """Generate a sample AKN XML for testing."""
    return parse_to_akn(
        bluebell_text="SECTION 1 - Test\n\n  Some content.\n",
        country="za",
        date="2024",
        number="1",
    )


def test_enrich_sets_title():
    """Enriching should set the document title."""
    akn = _make_sample_akn()
    enriched = enrich_akn(
        akn_xml=akn,
        title="My Test Act",
        country="za",
        doctype="act",
        year="2024",
        number="1",
    )
    assert "My Test Act" in enriched


def test_enrich_sets_frbr_uri():
    """Enriching should update the FRBR URI."""
    akn = _make_sample_akn()
    enriched = enrich_akn(
        akn_xml=akn,
        title="Kenya Wildlife Act",
        country="ke",
        doctype="act",
        year="2023",
        number="47",
    )
    assert "/akn/ke/act/2023/47" in enriched


def test_enrich_preserves_body():
    """Enriching metadata should not alter the document body."""
    akn = _make_sample_akn()
    enriched = enrich_akn(
        akn_xml=akn,
        title="Enriched Act",
        country="za",
        doctype="act",
        year="2024",
        number="1",
    )
    assert "Some content." in enriched
    assert "<body" in enriched
