"""What an absent jurisdiction config does, by what the caller wanted.

A caller that needs one is refused; a caller probing for one is answered.
"""

from __future__ import annotations

import asyncio

import pytest

from codify.jurisdictions import JurisdictionConfigError, load_config, try_load_config

_ABSENT = "zzabsent"
_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def test_a_caller_that_needs_a_config_is_refused() -> None:
    with pytest.raises(JurisdictionConfigError):
        load_config(_ABSENT)


def test_a_probe_is_answered_rather_than_refused() -> None:
    assert try_load_config(_ABSENT) is None


def test_the_enrich_passes_refuse_rather_than_returning_the_input() -> None:
    """They used to return the document unchanged, which shipped an unenriched
    law as a success."""
    from codify.pipeline.enrich.hcontainers import postprocess_hcontainers
    from codify.pipeline.enrich.inline_markup import emit_inline_markup
    from codify.pipeline.enrich.references import emit_references

    akn = f'<akomaNtoso xmlns="{_NS}"><act name="act"><meta/><body/></act></akomaNtoso>'
    with pytest.raises(JurisdictionConfigError):
        postprocess_hcontainers(akn, _ABSENT, "act")
    with pytest.raises(JurisdictionConfigError):
        emit_references(akn, _ABSENT)
    with pytest.raises(JurisdictionConfigError):
        asyncio.run(emit_inline_markup(akn, _ABSENT, "act", client=None))
