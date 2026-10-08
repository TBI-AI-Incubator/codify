"""Markers whose number precedes the keyword ("15. §", "I. FEJEZET")."""

from __future__ import annotations

from codify.jurisdictions import JurisdictionConfig, load_config
from codify.pipeline.enrich.anchors import (
    _basic_unit_line_re,
    _normalise_number,
    anchor_coverage,
    build_anchor_regex,
    scan_anchors,
)

# Invented text in the shape of a Hungarian act.
NUMBER_FIRST_ACT = """2031. évi CXI. törvény

a próbaügyekről

ELSŐ RÉSZ

ÁLTALÁNOS SZABÁLYOK

I. FEJEZET

ALAPELVEK

1. § (1) E törvény a próbaügyekre terjed ki.

(2) A 3. § (2) bekezdése szerinti eljárás díjmentes.

2. § Az eljárást a hivatal folytatja, a
3. §-a szerinti határidőben.

2/A. § A kérelmet írásban kell benyújtani.

II. FEJEZET

ZÁRÓ RENDELKEZÉSEK

3. § A Ptv. a következő 9/C. §-sal és az azt megelőző alcímmel egészül ki:

„Próbadíj

9/C. § A díj egységes.”

4. § Ez a törvény a kihirdetését követő napon lép hatályba.
"""


def _hu() -> JurisdictionConfig:
    config = load_config("hu")
    assert config is not None
    return config


def _scan(config: JurisdictionConfig, text: str, country: str = "") -> list[tuple[str, str]]:
    anchors = scan_anchors(text, build_anchor_regex(config, "act"), country=country, doctype="act")
    return [(a.kind, a.akn_eid) for a in anchors if not a.quoted_amendment]


def test_number_first_markers_anchor_with_their_containers() -> None:
    assert _scan(_hu(), NUMBER_FIRST_ACT, country="hu") == [
        ("part", "part_1"),
        ("chapter", "part_1__chp_I"),
        ("article", "part_1__chp_I__art_1"),
        ("article", "part_1__chp_I__art_2"),
        ("article", "part_1__chp_I__art_2-A"),
        ("chapter", "part_1__chp_II"),
        ("article", "part_1__chp_II__art_3"),
        ("article", "part_1__chp_II__art_4"),
    ]


def test_a_quote_spanning_its_subtitle_keeps_the_quoted_unit_out() -> None:
    """The quoted 9/C sits two blank lines below its opener; a unit-line bound
    would end the quote there and anchor it as the host's own article."""
    assert _basic_unit_line_re("hu") is None
    numbers = [n for kind, n in _scan(_hu(), NUMBER_FIRST_ACT, country="hu") if kind == "article"]
    assert not [n for n in numbers if "9" in n]


def test_the_coverage_denominator_reads_the_same_order() -> None:
    text = NUMBER_FIRST_ACT
    config = _hu()
    anchors = scan_anchors(text, build_anchor_regex(config, "act"), country="hu", doctype="act")
    coverage = anchor_coverage(text, anchors, config, "act", "article")
    host = {"1", "2", "2-A", "3", "4"}
    assert coverage.captured == host
    # The quoted 9/C counts too: the denominator reads markers through no quote mask.
    assert host <= coverage.expected


def test_keyword_first_configs_keep_their_order() -> None:
    """A keyword-first `§` config anchors "§ 5." and not the reversed form."""
    config = load_config("ee")
    assert config is not None
    assert config.structuring is None or config.structuring.marker_order == "keyword_first"
    regex = build_anchor_regex(config, "act")
    assert [a.number for a in scan_anchors("§ 5. Kohaldamine\n\nSisu.\n", regex)] == ["5"]
    assert scan_anchors("5. § Kohaldamine\n\nSisu.\n", regex) == []


def test_declaring_number_first_flips_a_keyword_first_config() -> None:
    config = load_config("ee")
    assert config is not None and config.structuring is not None
    flipped = config.model_copy(
        update={
            "structuring": config.structuring.model_copy(update={"marker_order": "number_first"})
        }
    )
    regex = build_anchor_regex(flipped, "act")
    assert [a.number for a in scan_anchors("5. § Kohaldamine\n\nSisu.\n", regex)] == ["5"]
    assert scan_anchors("§ 5. Kohaldamine\n\nSisu.\n", regex) == []


def test_a_slashed_letter_keys_as_the_parser_writes_it() -> None:
    """Bluebell gives `<num>5/A</num>` the eId `art_5-A`; a digit pair is unchanged."""
    assert _normalise_number("5/A") == "5-A"
    assert _normalise_number("7/1") == "7-1"
