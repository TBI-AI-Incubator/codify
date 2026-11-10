"""Compendium body text segments deterministically into AKN, for each shape it takes."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from codify.acquisition.adapters.oecd.compendium import wrap_body
from codify.akn import parse_akn, validate_akn
from codify.compare.scaffold import is_excluded, iter_assessable
from codify.pipeline.enrich.validator import validate_akn as run_validator
from codify.pipeline.events import Complete, Failed
from codify.pipeline.formats import dispatch
from codify.pipeline.formats.oecd_html import (
    OecdHtmlError,
    instrument_html_to_akn,
    is_oecd_instrument_html,
)
from codify.storage.mappers import document_to_rows

NS = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}

# Synthetic instruments in the Compendium's shapes; the subject matter is invented.
_COUNCIL_ACT = """
<p>THE COUNCIL,</p>
<p>HAVING REGARD to Article 5 b) of the Convention on the Organisation for Economic
Co-operation and Development of 14 December 1960;</p>
<p>HAVING REGARD to the Recommendation of the Council on Lantern Keeping [OECD/LEGAL/9001];</p>
<p>RECOGNISING that harbour lanterns are lit by many hands;</p>
<p>NOTING that a register of keepers serves every port;</p>
<p>On the proposal of the Public Governance Committee:</p>
<p>I.            AGREES that, for the purpose of the present Recommendation, the following
definitions are used:</p>
<p>-             Lantern refers to any fixed harbour light;</p>
<p>-             Keeper refers to the person who lights it.</p>
<p>II.           RECOMMENDS that Adherents establish lantern registers which:</p>
<p>1.            Record every keeper by name, by:</p>
<p>i)     recording the name at appointment;</p>
<p>ii)     publishing the register each year.</p>
<p>2.            Are open to inspection by any resident.</p>
<p>III.          RECOMMENDS that, in keeping their registers, Adherents should:</p>
<p>3.            Secure a keeper for every lantern.</p>
<p>IV.           INVITES the Secretary-General to disseminate this Recommendation.</p>
<p>V.            INSTRUCTS the Public Governance Committee to:</p>
<p>4.            Monitor the implementation of this Recommendation and report to the
Council no later than five years following its adoption.</p>
"""

_LETTERED_ACT = """
<p>THE COUNCIL,</p>
<p>HAVING REGARD to Article 5 b) of the Convention;</p>
<p>On the proposal of the Committee on Digital Economy Policy:</p>
<p>I. AGREES that the purpose of this Recommendation is to set out principles.</p>
<p>SECTION 1. LIGHTING THE HARBOUR</p>
<p>II. RECOMMENDS that Adherents:</p>
<p>a) Light every lantern at dusk;</p>
<p>b) Extinguish every lantern at dawn.</p>
"""

_CODE = """
<p>THE COUNCIL,</p>
<p>HAVING REGARD to Articles 2d) and 5a) of the Convention;</p>
<p>DECIDES:</p>
<p>PART I</p>
<p>UNDERTAKINGS WITH REGARD TO LANTERNS</p>
<p>Article 1</p>
<p>General undertakings</p>
<p>a. Members shall light lanterns between one another.</p>
<p>b. Members shall, in particular, endeavour:</p>
<p>i) to treat all lanterns alike;</p>
<p>ii) to permit the lighting of any lantern.</p>
<p>Article 2</p>
<p>Public order</p>
<p>Nothing in this Code prevents a Member from darkening a lantern for:</p>
<p>i) the maintenance of public order;</p>
<p>ii) the protection of shipping.</p>
"""

# Parts, titles and chapters nest; a heading line follows each number.
_NESTED_CODE = """
<p>THE COUNCIL,</p>
<p>DECIDES:</p>
<p>PART I</p>
<p>GENERAL PROVISIONS</p>
<p>CHAPTER I</p>
<p>Lantern duties</p>
<p>Article 1</p>
<p>Lighting</p>
<p>a. Members shall light lanterns at dusk.</p>
<p>Article 2</p>
<p>Extinguishing</p>
<p>a. Members shall extinguish lanterns at dawn.</p>
<p>CHAPTER II</p>
<p>Keeper duties</p>
<p>Article 3</p>
<p>Registers</p>
<p>a. Members shall keep a register of keepers.</p>
<p>PART II</p>
<p>FINAL PROVISIONS</p>
<p>TITLE I</p>
<p>Review</p>
<p>Article 4</p>
<p>Review of the Code</p>
<p>a. The Council shall review this Code every five years.</p>
"""

# The shape of a Council act whose last sections bind someone other than the
# Adherents: eight sections, twelve paragraphs numbered across three of them.
_EIGHT_SECTIONS = """
<p>THE COUNCIL,</p>
<p>HAVING REGARD to Article 5 b) of the Convention on the Organisation;</p>
<p>HAVING REGARD to the Recommendation of the Council on Lantern Keeping [OECD/LEGAL/9001];</p>
<p>HAVING REGARD to the Agenda on harbour safety welcomed by the Council;</p>
<p>RECOGNISING that harbour lanterns are lit at every level of port authority;</p>
<p>RECOGNISING a new stage in the use of electric lanterns;</p>
<p>RECOGNISING that ports require efficient lighting;</p>
<p>RECOGNISING the need for a common approach to lighting;</p>
<p>RECOGNISING that lighting can contribute to safe navigation;</p>
<p>NOTING that the Organisation supports harbour safety;</p>
<p>HAVING REGARD to the background document noted by the Harbour Committee;</p>
<p>On the proposal of the Harbour Committee:</p>
<p>I. AGREES that, for the purpose of the present Recommendation, the following
definitions are used:</p>
<p>- Lantern refers to any fixed harbour light;</p>
<p>- Keeper refers to the person who lights it.</p>
<p>II. RECOMMENDS that Adherents develop lighting strategies which:</p>
<p>1. Ensure transparency in lighting by:</p>
<p>i) publishing the lighting schedule;</p>
<p>ii) recording every outage.</p>
<p>2. Encourage participation of harbour users.</p>
<p>3. Create a culture of maintenance.</p>
<p>4. Reflect a risk management approach to outages.</p>
<p>III. RECOMMENDS that, in developing their strategies, Adherents should:</p>
<p>5. Secure leadership for the strategy.</p>
<p>6. Ensure coherent use of lanterns across ports.</p>
<p>7. Establish effective governance of lighting.</p>
<p>8. Strengthen co-operation with neighbouring ports.</p>
<p>IV. RECOMMENDS that, in implementing their strategies, Adherents should:</p>
<p>9. Develop clear business cases for lighting.</p>
<p>10. Reinforce capacities to manage lighting projects.</p>
<p>11. Procure lanterns based on an assessment of existing assets.</p>
<p>12. Ensure that legal frameworks allow electric lanterns.</p>
<p>V. INVITES the Secretary-General to disseminate this Recommendation.</p>
<p>VI. INVITES Adherents to disseminate this Recommendation at all levels of port authority.</p>
<p>VII. INVITES non-Adherents to take account of and adhere to this Recommendation.</p>
<p>VIII. INSTRUCTS the Harbour Committee to monitor the implementation of this Recommendation.</p>
"""

_DECLARATION = """
<p>WE, THE MINISTERS AND REPRESENTATIVES OF the lantern ports,</p>
<p>WE RECOGNISE that lanterns matter.</p>
<p>Lighting the future</p>
<p>WE CALL on the OECD, through its committees, to:</p>
<p>● Support ports in lighting lanterns;</p>
<p>● Track progress on lighting internationally.</p>
"""

_META = {
    "key": "OECD/LEGAL/9002",
    "type": "recommendation",
    "adopted": "2020-05-05",
    "title": "Recommendation of the Council on Lantern Registers",
    "lang": "en",
}


def _convert(body: str, **meta: str) -> tuple[str, dict[str, object], etree._Element]:
    page = wrap_body(body, meta={**_META, **meta}).decode()
    provenance: dict[str, object] = {}
    xml = instrument_html_to_akn(
        page, frbr_work_uri="/akn/oecd/act/recommendation/2020/9002", provenance=provenance
    )
    return xml, provenance, etree.fromstring(xml.encode())


def _eids(root: etree._Element, *tags: str) -> list[str]:
    return [
        el.get("eId") or ""
        for el in root.iter(*[f"{{{NS['a']}}}{t}" for t in tags])
        if el.get("eId")
    ]


def test_council_act_segments_preamble_sections_and_paragraphs() -> None:
    xml, provenance, root = _convert(_COUNCIL_ACT)
    validate_akn(xml)
    assert root.findtext(".//a:preface/a:longTitle/a:p", namespaces=NS) == _META["title"]
    assert _eids(root, "citation") == ["citations__cit_1", "citations__cit_2"]
    assert _eids(root, "recital") == ["recitals__rec_1", "recitals__rec_2"]
    assert root.findtext(".//a:formula/a:p", namespaces=NS) == (
        "On the proposal of the Public Governance Committee:"
    )
    sections = root.findall(".//a:body/a:section", NS)
    assert [s.findtext("a:heading", namespaces=NS) for s in sections] == [
        "AGREES", "RECOMMENDS", "RECOMMENDS", "INVITES", "INSTRUCTS",
    ]  # fmt: skip
    # Paragraph numbering runs across sections and the eId keeps the section.
    assert _eids(root, "paragraph") == [
        "sec_II__para_1", "sec_II__para_2", "sec_III__para_3", "sec_V__para_4",
    ]  # fmt: skip
    assert _eids(root, "point") == ["sec_II__para_1__point_i", "sec_II__para_1__point_ii"]
    assert _eids(root, "indent") == ["sec_I__indent_1", "sec_I__indent_2"]
    # A point list makes the paragraph's own text its intro.
    para_1 = root.find(".//a:paragraph[@eId='sec_II__para_1']", NS)
    assert para_1 is not None
    assert para_1.findtext("a:intro/a:p", namespaces=NS) == "Record every keeper by name, by:"
    html_source = provenance["html_source"]
    assert isinstance(html_source, dict)
    assert html_source["non_adherent_paragraphs"] == 1


def test_non_adherent_sections_are_marked_and_the_concept_declared() -> None:
    _, _, root = _convert(_COUNCIL_ACT)
    marked = {el.get("eId") for el in root.iter() if el.get("refersTo") == "#nonAdherentDuty"}
    assert marked == {"sec_IV", "sec_V", "sec_V__para_4"}
    concept = root.find(".//a:references/a:TLCConcept[@eId='nonAdherentDuty']", NS)
    assert concept is not None


def test_lettered_items_under_a_section_are_sibling_paragraphs() -> None:
    xml, _, root = _convert(_LETTERED_ACT)
    validate_akn(xml)
    assert _eids(root, "paragraph") == ["sec_II__para_a", "sec_II__para_b"]
    assert _eids(root, "point") == []
    # The all-caps line between sections becomes the next section's subheading.
    sec_ii = root.find(".//a:section[@eId='sec_II']", NS)
    assert sec_ii is not None
    assert sec_ii.findtext("a:subheading", namespaces=NS) == "SECTION 1. LIGHTING THE HARBOUR"


def test_code_shape_nests_parts_articles_and_lettered_paragraphs() -> None:
    xml, _, root = _convert(_CODE, type="decision")
    validate_akn(xml)
    assert root.findtext(".//a:formula/a:p", namespaces=NS) == "DECIDES:"
    part = root.find(".//a:body/a:part", NS)
    assert part is not None
    assert part.get("eId") == "part_i"
    assert part.findtext("a:heading", namespaces=NS) == "UNDERTAKINGS WITH REGARD TO LANTERNS"
    assert [
        (a.get("eId"), a.findtext("a:heading", namespaces=NS))
        for a in root.iter(f"{{{NS['a']}}}article")
    ] == [("part_i__art_1", "General undertakings"), ("part_i__art_2", "Public order")]
    assert _eids(root, "paragraph") == [
        "part_i__art_1__para_a",
        "part_i__art_1__para_b",
        "part_i__art_2__para_i",
        "part_i__art_2__para_ii",
    ]
    # `i)` under a dotted paragraph is a point; under an article it is a paragraph.
    assert _eids(root, "point") == [
        "part_i__art_1__para_b__point_i",
        "part_i__art_1__para_b__point_ii",
    ]


def test_declaration_paragraphs_carry_bullets_as_indents() -> None:
    xml, _, root = _convert(_DECLARATION, type="declaration")
    validate_akn(xml)
    assert root.findtext(".//a:preamble/a:p", namespaces=NS) == (
        "WE, THE MINISTERS AND REPRESENTATIVES OF the lantern ports,"
    )
    # Loose paragraphs gather into unnumbered sections, one per heading line.
    sections = root.findall(".//a:body/a:section", NS)
    assert [(s.get("eId"), s.findtext("a:heading", namespaces=NS)) for s in sections] == [
        ("sec_1", None),
        ("sec_2", "Lighting the future"),
    ]
    assert [s.find("a:num", NS) for s in sections] == [None, None]
    assert _eids(root, "paragraph") == ["sec_1__para_1", "sec_2__para_2"]
    # The source numbers none of them, so no number is invented.
    assert root.findall(".//a:paragraph/a:num", NS) == []
    assert _eids(root, "indent") == ["sec_2__para_2__indent_1", "sec_2__para_2__indent_2"]
    # `WE CALL on the OECD … to:` puts the duty on the OECD.
    marked = {el.get("eId") for el in root.iter() if el.get("refersTo") == "#nonAdherentDuty"}
    assert marked == {"sec_2__para_2", "sec_2__para_2__indent_1", "sec_2__para_2__indent_2"}
    assert run_validator(xml) == []


def test_repeated_paragraph_number_keeps_a_unique_eid() -> None:
    body = _LETTERED_ACT + "<p>III. RECOMMENDS that Adherents:</p><p>a) Keep the harbour dark.</p>"
    xml, _, root = _convert(body)
    validate_akn(xml)
    assert "sec_III__para_a" in _eids(root, "paragraph")


def test_validator_reports_nothing_structural_on_a_council_act() -> None:
    xml, _, _ = _convert(_COUNCIL_ACT)
    severe = [i for i in run_validator(xml) if i.get("severity") in ("error", "critical")]
    assert severe == []


def test_missing_adoption_date_is_refused() -> None:
    page = wrap_body(_COUNCIL_ACT, meta={**_META, "adopted": ""}).decode()
    with pytest.raises(OecdHtmlError, match="oecd.adopted"):
        instrument_html_to_akn(page, frbr_work_uri="/akn/oecd/act/recommendation/2020/9002")


def test_a_page_without_operative_text_is_refused() -> None:
    page = wrap_body("<p>THE COUNCIL,</p><p>HAVING REGARD to nothing;</p>", meta=_META).decode()
    with pytest.raises(OecdHtmlError, match="no operative text"):
        instrument_html_to_akn(page, frbr_work_uri="/akn/oecd/act/recommendation/2020/9002")


def test_sniff_reads_the_acquirer_meta(tmp_path: Path) -> None:
    assert is_oecd_instrument_html(wrap_body("<p>x</p>", meta=_META).decode())
    assert not is_oecd_instrument_html("<html><body><p>x</p></body></html>")
    wrapped, plain = tmp_path / "wrapped.html", tmp_path / "plain.html"
    wrapped.write_bytes(wrap_body("<p>x</p>", meta=_META))
    plain.write_text("<html><body><p>x</p></body></html>")
    assert is_oecd_instrument_html(wrapped)
    assert not is_oecd_instrument_html(plain)
    assert not is_oecd_instrument_html(tmp_path / "absent.html")


@pytest.mark.asyncio
async def test_dispatch_routes_a_wrapped_oecd_body_to_the_deterministic_lane(
    tmp_path: Path,
) -> None:
    path = tmp_path / "0406.html"
    path.write_bytes(wrap_body(_COUNCIL_ACT, meta=_META))
    events = [
        e
        async for e in dispatch(
            path, "oecd", frbr_work_uri="/akn/oecd/act/recommendation/2020/9002"
        )
    ]
    assert not [e for e in events if isinstance(e, Failed)]
    done = [e for e in events if isinstance(e, Complete)]
    assert len(done) == 1
    assert done[0].document.frbr_work_uri == "/akn/oecd/act/recommendation/2020/9002"


def test_chapters_and_titles_nest_under_parts_as_their_own_elements() -> None:
    xml, _, root = _convert(_NESTED_CODE, type="decision")
    validate_akn(xml)
    assert _eids(root, "part") == ["part_i", "part_ii"]
    assert _eids(root, "chapter") == ["part_i__chp_i", "part_i__chp_ii"]
    assert _eids(root, "title") == ["part_ii__title_i"]
    assert _eids(root, "article") == [
        "part_i__chp_i__art_1",
        "part_i__chp_i__art_2",
        "part_i__chp_ii__art_3",
        "part_ii__title_i__art_4",
    ]
    chapters = [
        (c.findtext("a:num", namespaces=NS), c.findtext("a:heading", namespaces=NS))
        for c in root.findall(".//a:chapter", NS)
    ]
    assert chapters == [("Chapter I", "Lantern duties"), ("Chapter II", "Keeper duties")]
    title = root.find(".//a:title", NS)
    assert title is not None
    assert (title.findtext("a:num", namespaces=NS), title.findtext("a:heading", namespaces=NS)) == (
        "Title I",
        "Review",
    )
    hierarchy = [i for i in run_validator(xml) if i.get("check") == "hierarchy_coherence"]
    assert hierarchy == []


def test_eight_section_act_structures_and_validates_clean() -> None:
    xml, provenance, root = _convert(_EIGHT_SECTIONS)
    validate_akn(xml)
    assert len(root.findall(".//a:preamble/a:citations/a:citation", NS)) == 4
    assert len(root.findall(".//a:preamble/a:recitals/a:recital", NS)) == 6
    assert root.findtext(".//a:formula/a:p", namespaces=NS) == (
        "On the proposal of the Harbour Committee:"
    )
    sections = root.findall(".//a:body/a:section", NS)
    assert [s.findtext("a:heading", namespaces=NS) for s in sections] == [
        "AGREES", "RECOMMENDS", "RECOMMENDS", "RECOMMENDS",
        "INVITES", "INVITES", "INVITES", "INSTRUCTS",
    ]  # fmt: skip
    paragraphs = _eids(root, "paragraph")
    assert [p.rsplit("_", 1)[-1] for p in paragraphs] == [str(n) for n in range(1, 13)]
    assert run_validator(xml) == []
    html_source = provenance["html_source"]
    assert isinstance(html_source, dict)
    assert html_source["paragraphs"] == 12


def _row_flags(xml: str) -> dict[str, tuple[bool | None, str | None]]:
    _, _, provisions, *_ = document_to_rows(parse_akn(xml), law_id=uuid.uuid4())
    return {p.akn_eid: (p.excluded_from_pool, p.exclusion_reason) for p in provisions}


def test_rows_under_invites_and_instructs_leave_the_pool() -> None:
    xml, _, _ = _convert(_EIGHT_SECTIONS)
    flags = _row_flags(xml)
    excluded = sorted(eid for eid, (flag, _) in flags.items() if flag)
    # VI invites the Adherents themselves, so it stays.
    assert excluded == ["sec_VIII__content", "sec_VII__content", "sec_V__content"]
    assert {flags[eid][1] for eid in excluded} == {"marked"}
    kept = [eid for eid, (flag, _) in flags.items() if flag is False]
    assert len(kept) == len(flags) - 3
    assert {"sec_IV__para_12", "sec_VI__content"} <= set(kept)


def test_paragraphs_under_instructs_leave_the_pool() -> None:
    xml, _, _ = _convert(_COUNCIL_ACT)
    flags = _row_flags(xml)
    assert {eid for eid, (flag, _) in flags.items() if flag} == {
        "sec_IV__content",
        "sec_V__intro",
        "sec_V__para_4",
    }
    assert flags["sec_V__intro"] == (True, "marked")
    assert flags["sec_V__para_4"] == (True, "marked")
    assert flags["sec_III__intro"] == (False, None)
    assert flags["sec_III__para_3"] == (False, None)
    assert flags["sec_II__para_1__point_i"] == (False, None)


def test_the_comparator_excludes_what_the_rows_exclude() -> None:
    xml, _, _ = _convert(_COUNCIL_ACT)
    doc = parse_akn(xml)
    excluded = sorted(el.akn_eid for el in iter_assessable(doc) if is_excluded(el, doc))
    assert excluded == ["sec_IV", "sec_V", "sec_V__para_4"]


def test_an_empty_excluded_section_does_not_pool_its_bullets() -> None:
    body = _LETTERED_ACT + (
        "<p>III. INVITES</p><p>- the Secretary-General to disseminate this Recommendation;</p>"
        "<p>- non-Adherents to adhere to this Recommendation.</p>"
    )
    xml, _, _ = _convert(body)
    flags = _row_flags(xml)
    assert {eid for eid, (flag, _) in flags.items() if flag} == {
        "sec_III__indent_1",
        "sec_III__indent_2",
    }
    doc = parse_akn(xml)
    pooled = [el.akn_eid for el in iter_assessable(doc) if not is_excluded(el, doc)]
    assert "sec_III" not in pooled
    assert "sec_II__para_a" in pooled


# A closing INSTRUCTS section, then an annex whose guidance binds the Adherents.
_WITH_ANNEX = """
<p>THE COUNCIL,</p>
<p>On the proposal of the Harbour Committee:</p>
<p>I. RECOMMENDS that Adherents apply the Guiding Principles in the Annex.</p>
<p>II. INSTRUCTS the Harbour Committee to review this Recommendation.</p>
<p>Annex</p>
<p>GUIDING PRINCIPLES ON HARBOUR LIGHTING</p>
<p>1. Every harbour should light its entrance from dusk to dawn.</p>
<p>a) Cost Allocation</p>
<p>2. The keeper should bear the cost of the lantern oil.</p>
<p>Annex II</p>
<p>3. Records should be kept for five years.</p>
<p>I. INVITES harbours to share their records.</p>
"""


def test_an_annex_closes_the_last_section_and_stays_in_the_pool() -> None:
    xml, provenance, root = _convert(_WITH_ANNEX)
    validate_akn(xml)
    html_source = provenance["html_source"]
    assert isinstance(html_source, dict)
    assert html_source["annexes"] == 2
    attachments = root.findall(".//a:attachments/a:attachment", NS)
    assert [(a.get("eId"), a.findtext("a:heading", namespaces=NS)) for a in attachments] == [
        ("att_1", "Annex"),
        ("att_2", "Annex II"),
    ]
    assert attachments[0].findtext("a:subheading", namespaces=NS) == (
        "GUIDING PRINCIPLES ON HARBOUR LIGHTING"
    )
    assert _eids(root.find(".//a:body", NS), "paragraph") == []
    assert _eids(attachments[0], "paragraph") == ["att_1__sec_1__para_1", "att_1__sec_1__para_2"]
    flags = _row_flags(xml)
    assert {eid for eid, (flag, _) in flags.items() if flag} == {"sec_II__content"}
    assert flags["att_1__sec_1__para_2"] == (False, None)
    assert flags["att_2__sec_1__para_3"] == (False, None)
    # A verb inside an annex binds no one by its verb.
    assert flags["att_2__sec_I__content"] == (False, None)
    doc = parse_akn(xml)

    def units(elements: list[Any]) -> list[Any]:
        return [u for el in elements for u in (el, *units(el.children))]

    annex_units = {u.akn_eid: is_excluded(u, doc) for u in units(doc.attachments)}
    expected = ["att_1__sec_1__para_1", "att_1__sec_1__para_2", "att_2__sec_1__para_3"]
    assert [annex_units[eid] for eid in [*expected, "att_2__sec_I"]] == [False] * 4


def test_an_unnumbered_verb_after_a_verb_section_opens_a_sibling() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>On the proposal of the Harbour Committee:</p>
<p>RECOMMENDS that Adherents light every lantern.</p>
<p>INSTRUCTS the Harbour Committee to report within five years.</p>
"""
    xml, _, root = _convert(body)
    sections = root.findall(".//a:body/a:section", NS)
    assert [(s.get("eId"), s.findtext("a:heading", namespaces=NS)) for s in sections] == [
        ("sec_I", "RECOMMENDS"),
        ("sec_II", "INSTRUCTS"),
    ]
    flags = _row_flags(xml)
    assert flags == {"sec_I__content": (False, None), "sec_II__content": (True, "marked")}


def test_a_translated_heading_keeps_the_unit_out_of_the_pool() -> None:
    xml, _, _ = _convert(_COUNCIL_ACT)
    # Translation rewrites the heading text and keeps the markup.
    translated = xml.replace("<heading>INSTRUCTS</heading>", "<heading>CHARGE</heading>")
    translated = translated.replace("<heading>INVITES</heading>", "<heading>เชิญ</heading>")
    assert translated != xml
    flags = _row_flags(translated)
    assert {eid for eid, (flag, _) in flags.items() if flag} == {
        "sec_IV__content",
        "sec_V__intro",
        "sec_V__para_4",
    }
    doc = parse_akn(translated)
    assert [el.akn_eid for el in iter_assessable(doc) if is_excluded(el, doc)] == [
        "sec_IV", "sec_V", "sec_V__para_4",
    ]  # fmt: skip


def test_the_french_text_marks_the_same_sections() -> None:
    body = """
<p>LE CONSEIL,</p>
<p>I. RECOMMANDE que les Adhérents allument chaque lanterne.</p>
<p>II. INVITE le Secrétaire général à diffuser la présente Recommandation.</p>
<p>III. CHARGE le Comité des ports :</p>
<p>1. de suivre la mise en œuvre de la présente Recommandation.</p>
<p>Annexe</p>
<p>2. Les ports allument les lanternes au crépuscule.</p>
"""
    xml, _, root = _convert(body, lang="fr")
    # The wrapper names the language, so the French expression is not the English one.
    assert [e.get("language") for e in root.iterfind(".//a:FRBRlanguage", NS)] == ["fra", "fra"]
    assert parse_akn(xml).frbr_expression_uri == (
        "/akn/oecd/act/recommendation/2020/9002/fra@2020-05-05"
    )
    sections = root.findall(".//a:body/a:section", NS)
    assert [s.findtext("a:heading", namespaces=NS) for s in sections] == [
        "RECOMMANDE", "INVITE", "CHARGE",
    ]  # fmt: skip
    flags = _row_flags(xml)
    assert {eid for eid, (flag, _) in flags.items() if flag} == {
        "sec_II__content",
        "sec_III__intro",
        "sec_III__para_1",
    }
    assert flags["att_1__sec_1__para_2"] == (False, None)


@pytest.mark.parametrize(
    ("lead_in", "marked"),
    [
        ("INVITES Adherents to disseminate this Recommendation.", False),
        ("INVITES the Secretary-General and Adherents to disseminate it.", False),
        ("INVITES non-Adherents to adhere to this Recommendation.", True),
        ("REQUESTS Members to report on its implementation.", False),
        ("ENCOURAGES all harbour actors to promote it.", True),
        ("REQUESTS the Secretary-General to report to Adherents.", True),
        ("INVITE les Adhérents à diffuser la présente Recommandation.", False),
        ("INVITE les non-Adhérents à y adhérer.", True),
        ("CHARGE le Comité des ports de suivre sa mise en œuvre.", True),
        ("CHARGE le Comité de faire rapport aux Adhérents.", True),
        ("CHARGE le Comité d’informer les Adhérents.", True),
        ("CHARGE le Comité de la gouvernance publique de suivre les Adhérents.", True),
        ("INVITE les représentants de chaque Adhérent à diffuser le texte.", False),
    ],
)
def test_the_addressee_decides_the_mark(lead_in: str, marked: bool) -> None:
    for numbered in (f"II. {lead_in}", lead_in):
        body = (
            "<p>THE COUNCIL,</p><p>I. RECOMMENDS that Adherents light lanterns.</p>"
            f"<p>{numbered}</p>"
        )
        _, _, root = _convert(body)
        section = root.findall(".//a:body/a:section", NS)[1]
        assert (section.get("refersTo") == "#nonAdherentDuty") is marked, numbered


def test_items_name_their_own_addressee_when_the_lead_in_names_none() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents light lanterns.</p>
<p>II. INVITES:</p>
<p>a) the Secretary-General to disseminate this Recommendation;</p>
<p>b) Adherents to share their records.</p>
"""
    xml, _, root = _convert(body)
    marked = {el.get("eId") for el in root.iter() if el.get("refersTo") == "#nonAdherentDuty"}
    assert marked == {"sec_II__para_a"}
    flags = _row_flags(xml)
    assert flags["sec_II__para_a"] == (True, "marked")
    assert flags["sec_II__para_b"] == (False, None)


def test_a_revised_text_is_a_new_expression_of_the_same_work() -> None:
    adopted, _, _ = _convert(_COUNCIL_ACT)
    revised_xml, _, root = _convert(
        _COUNCIL_ACT.replace("five years", "three years"), revised="2024-01-07"
    )
    first, second = parse_akn(adopted), parse_akn(revised_xml)
    assert first.frbr_work_uri == second.frbr_work_uri
    assert (first.frbr_expression_uri, second.frbr_expression_uri) == (
        "/akn/oecd/act/recommendation/2020/9002/eng@2020-05-05",
        "/akn/oecd/act/recommendation/2020/9002/eng@2024-01-07",
    )
    work_date = root.find(".//a:FRBRWork/a:FRBRdate", NS)
    assert work_date is not None and work_date.get("date") == "2020-05-05"
    assert [e.get("eId") for e in root.iter(f"{{{NS['a']}}}eventRef")] == [
        "evt_adoption",
        "evt_revision",
    ]
    validate_akn(revised_xml)
    assert run_validator(revised_xml) == []
    # A revision date before adoption is a data error, not an earlier expression.
    early, _, _ = _convert(_COUNCIL_ACT, revised="2001-01-01")
    assert parse_akn(early).frbr_expression_uri.endswith("@2020-05-05")


@pytest.mark.parametrize(
    ("annex", "first_unit"),
    [
        ("<p>Article 1</p><p>a. Members shall keep registers.</p>", "att_1__art_1"),
        ("<p>1. Adherents should:</p><p>a) keep registers;</p><p>b) publish them.</p>",
         "att_1__sec_1__para_1"),
        ("<p>1. Members shall keep registers</p>", "att_1__sec_1__para_1"),
        ("<p>1) Members shall keep registers</p>", "att_1__sec_1__para_1"),
    ],
)  # fmt: skip
def test_an_annex_never_takes_its_first_unit_as_a_caption(annex: str, first_unit: str) -> None:
    body = "<p>THE COUNCIL,</p><p>I. RECOMMENDS that Adherents apply the Annex.</p><p>Annex</p>"
    xml, _, root = _convert(body + annex)
    validate_akn(xml)
    attachment = root.find(".//a:attachments/a:attachment", NS)
    assert attachment is not None
    assert attachment.find("a:subheading", NS) is None
    assert _eids(attachment, "article", "paragraph")[0] == first_unit


def test_a_verb_line_inside_an_article_continues_the_article() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>DECIDES:</p>
<p>Article 1</p>
<p>General undertakings</p>
<p>a. Members shall light lanterns at dusk.</p>
<p>NOTES that this undertaking covers every harbour.</p>
<p>Article 2</p>
<p>a. Members shall keep registers.</p>
"""
    _, _, root = _convert(body, type="decision")
    assert root.findall(".//a:body/a:section", NS) == []
    assert _eids(root, "article") == ["art_1", "art_2"]
    para = root.find(".//a:paragraph[@eId='art_1__para_a']", NS)
    assert para is not None
    assert "NOTES that this undertaking covers every harbour." in "".join(para.itertext())


def _marked(root: etree._Element) -> set[str]:
    return {el.get("eId") or "" for el in root.iter() if el.get("refersTo") == "#nonAdherentDuty"}


@pytest.mark.parametrize(
    ("label", "prefix"),
    [
        ("PART", "part"),
        ("PARTIE", "part"),
        ("CHAPTER", "chp"),
        ("CHAPITRE", "chp"),
        ("TITLE", "title"),
        ("TITRE", "title"),
    ],
)
def test_a_container_label_closes_the_section_before_it(label: str, prefix: str) -> None:
    body = f"""
<p>LE CONSEIL,</p>
<p>{label} I</p>
<p>I. DÉCIDE que les pays Membres éclairent les ports.</p>
<p>II. CHARGE le Comité des ports de faire rapport.</p>
<p>{label} II</p>
<p>Pour mettre en œuvre la Décision exposée ci-dessus :</p>
<p>I. RECOMMANDE que les pays Membres tiennent des registres.</p>
"""
    xml, _, root = _convert(body, lang="fr")
    validate_akn(xml)
    # Under the second label, its lead-in is no part of the Committee's duty.
    assert _marked(root) == {f"{prefix}_i__sec_II"}


def _clean(xml: str) -> None:
    validate_akn(xml)
    assert run_validator(xml) == []


def test_a_ministerial_declaration_speaks_in_the_first_person() -> None:
    body = """
<p>WE, the Ministers and Representatives of the lantern ports,</p>
<p>CONSIDERING that:</p>
<p>1. Harbours need light at night.</p>
<p>2. Keepers are scarce.</p>
<p>DECLARE that we:</p>
<p>1. Support the work of the harbour masters.</p>
<p>2. Call on the OECD to:</p>
<p>- Map every lantern;</p>
<p>- Report on keepers.</p>
<p>3. Plan to light every harbour by 2030.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    assert root.findtext(".//a:preamble/a:p", namespaces=NS).startswith("WE, the Ministers")  # type: ignore[union-attr]
    assert len(root.findall(".//a:preamble/a:recitals/a:recital", NS)) == 3
    sections = root.findall(".//a:body/a:section", NS)
    assert [s.findtext("a:heading", namespaces=NS) for s in sections] == ["DECLARE"]
    assert _eids(root, "paragraph") == ["sec_I__para_1", "sec_I__para_2", "sec_I__para_3"]
    # Only the item calling on the OECD binds someone other than the declarants.
    assert _marked(root) == {"sec_I__para_2", "sec_I__para_2__indent_1", "sec_I__para_2__indent_2"}


def test_governments_declare_numbered_paragraphs() -> None:
    body = """
<p>THE GOVERNMENTS OF THE LANTERN PORTS:</p>
<p>RECOGNISING that harbours grow busier;</p>
<p>DECLARE that:</p>
<p>1. The lighting of harbours is a shared objective.</p>
<p>2. They will co-operate on keepers.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    assert (
        root.findtext(".//a:preamble/a:p", namespaces=NS) == "THE GOVERNMENTS OF THE LANTERN PORTS:"
    )
    assert _eids(root, "paragraph") == ["sec_I__para_1", "sec_I__para_2"]
    assert _marked(root) == set()


def test_ministerial_conclusions_report_in_the_past_tense() -> None:
    body = """
<p>AT THEIR MEETING, MINISTERS OF THE LANTERN PORTS:</p>
<p>WELCOMED the work on harbour lighting.</p>
<p>INVITED the Secretary-General to report on keepers.</p>
<p>AGREED that Adherents should light every harbour.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    sections = root.findall(".//a:body/a:section", NS)
    assert [s.findtext("a:heading", namespaces=NS) for s in sections] == [
        "WELCOMED", "INVITED", "AGREED",
    ]  # fmt: skip
    assert _marked(root) == {"sec_II"}


def test_numbered_paragraphs_opening_on_a_verb_start_the_body() -> None:
    body = """
<p>WE, THE MINISTERS of the lantern ports,</p>
<p>CONSIDERING that lanterns matter;</p>
<p>1. DECLARE that harbours must be lit;</p>
<p>2. CALL on all shipping lines to report outages;</p>
<p>3. INVITE the Secretary-General to keep a register;</p>
<p>4. AGREE that Adherents will share their registers.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    assert len(root.findall(".//a:preamble/a:recitals/a:recital", NS)) == 1
    assert _eids(root, "paragraph") == [f"sec_1__para_{n}" for n in range(1, 5)]
    assert _marked(root) == {"sec_1__para_2", "sec_1__para_3"}


def test_a_text_without_operative_verbs_is_all_body() -> None:
    body = """
<p>Preamble</p>
<p>1. Harbours are lit by keepers.</p>
<p>2. Keepers are scarce.</p>
<p>What the ports want to achieve</p>
<p>3. Each port should:</p>
<p>1. Keep a register;</p>
<p>2. Train new keepers.</p>
<p>4. The ports will meet every year.</p>
"""
    xml, _, root = _convert(body, type="arrangement")
    _clean(xml)
    assert root.find(".//a:preamble", NS) is None
    sections = root.findall(".//a:body/a:section", NS)
    assert [(s.get("eId"), s.findtext("a:heading", namespaces=NS)) for s in sections] == [
        ("sec_1", "Preamble"),
        ("sec_2", "What the ports want to achieve"),
    ]
    # `3. … should:` then `1.`, `2.`: a list inside paragraph 3, and `4.` resumes.
    assert _eids(root, "paragraph") == [
        "sec_1__para_1", "sec_1__para_2", "sec_2__para_3", "sec_2__para_4",
    ]  # fmt: skip
    assert _eids(root, "point") == ["sec_2__para_3__point_1", "sec_2__para_3__point_2"]


def test_a_french_declaration_marks_its_call_on_the_oecd() -> None:
    body = """
<p>NOUS, MINISTRES ET REPRÉSENTANTS des ports,</p>
<p>NOUS RECONNAISSONS que les lanternes comptent.</p>
<p>NOUS APPELONS l’OCDE à cartographier chaque lanterne.</p>
<p>NOUS INVITONS les Adhérents à partager leurs registres.</p>
"""
    xml, _, root = _convert(body, type="declaration", lang="fr")
    _clean(xml)
    assert _eids(root, "paragraph") == ["sec_1__para_1", "sec_1__para_2", "sec_1__para_3"]
    assert _marked(root) == {"sec_1__para_2"}


def test_a_quoted_excerpt_opens_on_its_section() -> None:
    body = """
<p>“III. Commitments</p>
<p>4. Members agree to the following guidelines:</p>
<p>i) To light every lantern;</p>
<p>ii) To keep a register.”</p>
"""
    xml, _, root = _convert(body, type="arrangement")
    _clean(xml)
    assert _eids(root, "section") == ["sec_III"]
    assert _eids(root, "point") == ["sec_III__para_4__point_i", "sec_III__para_4__point_ii"]


def test_an_agreement_numbers_articles_in_roman_and_paragraphs_in_parentheses() -> None:
    body = """
<p>The Parties to this Agreement,</p>
<p>CONSIDERING that harbours need light,</p>
<p>HAVE AGREED as follows:</p>
<p>Article I</p>
<p>Objective</p>
<p>1) The objective is to light every harbour.</p>
<p>2) Collaboration shall be peaceful.</p>
<p>Article II</p>
<p>Forms of collaboration</p>
<p>Collaboration may include:</p>
<p>a) joint lighting;</p>
<p>b) exchange of keepers.</p>
"""
    xml, _, root = _convert(body, type="agreement")
    _clean(xml)
    assert [(a.get("eId"), a.findtext("a:heading", namespaces=NS))
            for a in root.iter(f"{{{NS['a']}}}article")] == [
        ("art_i", "Objective"), ("art_ii", "Forms of collaboration"),
    ]  # fmt: skip
    assert _eids(root, "paragraph")[:2] == ["art_i__para_1", "art_i__para_2"]


def test_lettered_items_under_a_numbered_paragraph_are_its_points() -> None:
    body = """
<p>THE COMMITTEE,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>1. Develop lighting policies, including to:</p>
<p>a. Foster a culture of maintenance;</p>
<p>b. Ensure senior commitment.</p>
<p>2. Develop keeper support, including to:</p>
<p>a. Provide training;</p>
<p>b. Publish registers.</p>
"""
    xml, _, root = _convert(body)
    _clean(xml)
    assert _eids(root, "paragraph") == ["sec_I__para_1", "sec_I__para_2"]
    assert _eids(root, "point") == [
        "sec_I__para_1__point_a", "sec_I__para_1__point_b",
        "sec_I__para_2__point_a", "sec_I__para_2__point_b",
    ]  # fmt: skip


def test_items_under_a_colon_lead_in_each_name_their_addressee() -> None:
    body = """
<p>THE GOVERNMENTS OF THE LANTERN PORTS,</p>
<p>DECLARE THAT:</p>
<p>They will light every harbour.</p>
<p>They will keep registers, by:</p>
<p>a) training keepers;</p>
<p>b) publishing outages.</p>
<p>INVITE:</p>
<p>Other ports to take this Declaration into account.</p>
<p>Adherents to share their registers.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    assert _eids(root, "paragraph") == [
        "sec_I__para_1", "sec_I__para_2", "sec_II__para_3", "sec_II__para_4",
    ]  # fmt: skip
    assert _eids(root, "point") == ["sec_I__para_2__point_a", "sec_I__para_2__point_b"]
    assert _marked(root) == {"sec_II__para_3"}


def test_a_numbered_sublist_closes_when_the_outer_sequence_resumes() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>1. Ports light lanterns at dusk.</p>
<p>2. Ports should:</p>
<p>1. Keep a register;</p>
<p>2. Train new keepers.</p>
<p>3. Ports will meet annually.</p>
"""
    xml, _, root = _convert(body, type="arrangement")
    _clean(xml)
    assert _eids(root, "paragraph") == ["sec_1__para_1", "sec_1__para_2", "sec_1__para_3"]
    assert _eids(root, "point") == ["sec_1__para_2__point_1", "sec_1__para_2__point_2"]


def test_items_under_an_adherents_lead_in_stay_the_adherents_duty() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents should:</p>
<p>1. ENCOURAGE local authorities to light every harbour;</p>
<p>2. INVITE shipping lines to report outages.</p>
"""
    xml, _, root = _convert(body)
    _clean(xml)
    assert _eids(root, "paragraph") == ["sec_I__para_1", "sec_I__para_2"]
    assert _marked(root) == set()


def test_a_verbless_text_keeps_each_heading() -> None:
    body = """
<p>Purpose</p>
<p>The ports light their lanterns at dusk;</p>
<p>Implementation</p>
<p>The ports meet every year to review the lighting.</p>
"""
    xml, _, root = _convert(body, type="arrangement")
    _clean(xml)
    sections = root.findall(".//a:body/a:section", NS)
    assert [(s.findtext("a:heading", namespaces=NS), len(s.findall("a:paragraph", NS)))
            for s in sections] == [("Purpose", 1), ("Implementation", 1)]  # fmt: skip


def test_a_verbless_body_keeps_what_lies_between_the_preamble_lines() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>Lanterns help ships find the harbour.</p>
<p>HAVING REGARD to the Lantern Convention;</p>
<p>Keepers light them every evening.</p>
"""
    xml, _, root = _convert(body, type="arrangement")
    _clean(xml)
    assert [c.findtext("a:p", namespaces=NS) for c in root.iterfind(".//a:citation", NS)] == [
        "HAVING REGARD to the Lantern Convention;"
    ]
    paragraphs = [
        " ".join(p.itertext()).strip() for p in root.iterfind(".//a:body//a:paragraph", NS)
    ]
    assert paragraphs == [
        "Lanterns help ships find the harbour.",
        "Keepers light them every evening.",
    ]


def _body_words(xml: str) -> list[str]:
    root = etree.fromstring(xml.encode())
    for el in list(root.iter(f"{{{NS['a']}}}meta")) + list(root.iter(f"{{{NS['a']}}}preface")):
        el.getparent().remove(el)  # type: ignore[union-attr]
    return sorted(" ".join(root.itertext()).replace("/", " ").split())


def _source_words(body: str) -> list[str]:
    return sorted(" ".join(etree.fromstring(f"<b>{body}</b>").itertext()).replace("“", "").split())


def test_a_heading_between_list_items_ends_the_list_and_survives() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>2. Publish data as follows:</p>
<p>Chapter Two Keepers</p>
<p>1. Train keepers.</p>
<p>a) The Committee shall:</p>
<p>Duty to Return</p>
<p>i) return the lanterns.</p>
<p>II. AGREES that:</p>
<p>Definitions</p>
<p>- Lantern refers to a fixed light.</p>
"""
    xml, _, root = _convert(body)
    _clean(xml)
    assert _body_words(xml) == _source_words(body)
    headed = {el.get("eId"): el.findtext("a:heading", namespaces=NS)
              for el in root.iter() if el.find("a:heading", NS) is not None}  # fmt: skip
    assert headed["sec_I__para_1"] == "Chapter Two Keepers"
    assert headed["sec_I__para_i"] == "Duty to Return"
    # A bullet takes the heading above it too.
    assert headed["sec_II__indent_1"] == "Definitions"


def test_consecutive_heading_lines_all_survive() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>PART ONE</p>
<p>GENERAL PRINCIPLES</p>
<p>1. Light every harbour.</p>
<p>Closing remarks</p>
"""
    xml, _, _ = _convert(body)
    _clean(xml)
    assert _body_words(xml) == _source_words(body)


def test_a_lead_ins_next_sibling_is_not_its_point() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>a) The Committee shall:</p>
<p>i) keep a register;</p>
<p>ii) publish it.</p>
<p>b) The Secretariat shall assist.</p>
"""
    xml, _, root = _convert(body)
    _clean(xml)
    assert _eids(root, "paragraph") == ["sec_I__para_a", "sec_I__para_b"]
    assert _eids(root, "point") == ["sec_I__para_a__point_i", "sec_I__para_a__point_ii"]


def test_the_declarants_own_commitments_stay_unmarked() -> None:
    body = """
<p>WE, THE MINISTERS of the lantern ports,</p>
<p>DECLARE that we:</p>
<p>1. ENCOURAGE green investment in harbour lighting.</p>
<p>2. ENCOURAGE efforts to develop proposals for keepers.</p>
<p>3. INVITE Non-OECD Members to adhere to this Declaration.</p>
<p>4. INVITE the Secretary-General to report.</p>
<p>5. INVITE contributions from harbour users.</p>
<p>6. URGE the Harbour Committee to report.</p>
<p>7. ENCOURAGE the Secretary-General to report.</p>
<p>8. ENCOURAGE green lighting. In this respect, We are resolved to fund it.</p>
<p>9. ENCOURAGE wider use of lighting in Harbour towns to grow.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    # `Non-OECD Members` are not the Adherents, whatever the regex for Members finds.
    # Encouraging an activity names no one; encouraging an actor does.
    assert _marked(root) == {"sec_I__para_3", "sec_I__para_4", "sec_I__para_6", "sec_I__para_7"}


def test_a_bare_verb_leaves_each_item_to_name_its_addressee() -> None:
    body = """
<p>WE, THE MINISTERS of the lantern ports,</p>
<p>DECLARE that we:</p>
<p>1. INVITE:</p>
<p>a) the Secretary-General to report;</p>
<p>b) Adherents to share records.</p>
<p>2. ENCOURAGE:</p>
<p>a) green investment in harbour lighting;</p>
<p>b) the Harbour Committee to report.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    assert _marked(root) == {"sec_I__para_1__point_a", "sec_I__para_2__point_b"}


def test_appendices_and_labels_keep_their_text() -> None:
    body = """
<p>PREAMBLE</p>
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents light harbours.</p>
<p>ANNEX</p>
<p>ANNEX TO THE RECOMMENDATION ON HARBOURS</p>
<p>1. Every harbour has a keeper.</p>
<p>APPENDIX I. CONTENTS LINE</p>
<p>APPENDIX II</p>
<p>2. Keepers keep registers.</p>
"""
    xml, _, root = _convert(body)
    _clean(xml)
    assert _body_words(xml) == _source_words(body)
    attachments = root.findall(".//a:attachment", NS)
    assert [(a.findtext("a:heading", namespaces=NS), a.findtext("a:subheading", namespaces=NS))
            for a in attachments] == [
        ("ANNEX", "ANNEX TO THE RECOMMENDATION ON HARBOURS"), ("APPENDIX II", None),
    ]  # fmt: skip


def test_loose_paragraphs_in_an_article_text_group_without_a_title() -> None:
    body = """
<p>The Parties to this Agreement,</p>
<p>HAVE AGREED as follows:</p>
<p>Article 1</p>
<p>Objective</p>
<p>a. The Parties shall light harbours.</p>
<p>ANNEX</p>
<p>Harbours are lit at dusk.</p>
"""
    xml, _, root = _convert(body, type="agreement")
    _clean(xml)
    group = root.find(".//a:attachment//a:hcontainer", NS)
    assert group is not None
    assert (group.get("eId"), group.get("name")) == ("att_1__hcontainer_1", "group")


@pytest.mark.parametrize(
    ("lead_in", "marked"),
    [
        # `Adherents` is the verb's object here; the items are the declarants' acts.
        ("DECLARE that we support Adherents through the following actions:", (True, True)),
        ("RECOMMENDS that Adherents should:", (False, False)),
        ("RECOMMENDS that, in lighting harbours, Adherents should:", (False, False)),
        ("DECLARE that, in this spirit, we will:", (True, True)),
        # A `we` later in the clause does not make the items the declarants' own:
        # only the capitalised verb is read, as it is under any other subject.
        ("AGREE that the Secretariat, as we requested, should:", (True, False)),
    ],
)
def test_the_lead_ins_subject_decides_whose_items_they_are(
    lead_in: str, marked: tuple[bool, bool]
) -> None:
    body = f"""
<p>WE, THE MINISTERS of the lantern ports,</p>
<p>I. {lead_in}</p>
<p>1. CALL on the OECD to map every lantern.</p>
<p>2. Call on the OECD to count every keeper.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    assert (("sec_I__para_1" in _marked(root)), ("sec_I__para_2" in _marked(root))) == marked


def test_a_colon_line_is_a_lead_in_not_a_heading() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>1. Light every harbour.</p>
<p>To that effect, the ports should:</p>
<p>a) keep registers;</p>
<p>b) train keepers.</p>
"""
    xml, _, root = _convert(body)
    _clean(xml)
    assert _eids(root, "point") == ["sec_I__para_1__point_a", "sec_I__para_1__point_b"]
    assert root.findall(".//a:heading", NS)[-1].text == "RECOMMENDS"


def test_the_preamble_label_opens_the_preamble() -> None:
    body = "<p>PREAMBLE</p><p>THE COUNCIL,</p><p>RECOGNISING that harbours matter;</p>" + (
        "<p>I. RECOMMENDS that Adherents light harbours.</p>"
    )
    xml, _, root = _convert(body)
    _clean(xml)
    lines = [p.text for p in root.findall(".//a:preamble/a:p", NS)]
    assert lines == ["PREAMBLE", "THE COUNCIL,"]
    recitals = [" ".join(r.itertext()).strip() for r in root.iterfind(".//a:recital", NS)]
    assert recitals == ["RECOGNISING that harbours matter;"]


def test_an_annex_caption_may_end_in_a_colon() -> None:
    body = "<p>THE COUNCIL,</p><p>I. DECIDES that ports are lit.</p>" + (
        "<p>Annex I to the Code:</p><p>1. Every harbour has a keeper.</p>"
    )
    xml, _, root = _convert(body, type="decision")
    _clean(xml)
    assert root.findtext(".//a:attachment/a:heading", namespaces=NS) == "Annex I to the Code:"


def test_a_terminal_enacting_line_is_no_operative_text() -> None:
    body = "<p>THE COUNCIL,</p><p>HAVING REGARD to the Lantern Convention;</p><p>DECIDES:</p>"
    page = wrap_body(body, meta=_META).decode()
    with pytest.raises(OecdHtmlError, match="no operative text"):
        instrument_html_to_akn(page, frbr_work_uri="/akn/oecd/act/recommendation/2020/9002")


def test_paragraph_one_may_hold_a_numbered_list() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>1. Each port should:</p>
<p>1. Keep a register;</p>
<p>2. Train new keepers.</p>
<p>2. Ports will meet annually.</p>
"""
    xml, _, root = _convert(body, type="arrangement")
    _clean(xml)
    assert _eids(root, "paragraph") == ["sec_1__para_1", "sec_1__para_2"]
    assert _eids(root, "point") == ["sec_1__para_1__point_1", "sec_1__para_1__point_2"]


_LETTERED_PARTS = """
<p>THE COUNCIL,</p>
<p>On the proposal of the Harbour Committee:</p>
<p>I. RECOMMENDS that Adherents keep lanterns lit.</p>
<p>II. RECOMMENDS that Adherents:</p>
<p>i. review their lantern registers each year;</p>
<p>ii. publish the outcome of the review;</p>
<p>iii. consult keepers.</p>
<p>III. RECOMMENDS that Adherents:</p>
<p>i. record every keeper;</p>
<p>ii. train each keeper, including:</p>
<p>a. night watch duties;</p>
<p>b. weather duties;</p>
<p>iii. publish the register.</p>
<p>IV. RECOMMENDS that Adherents cooperate across harbours.</p>
<p>A. Shared registers</p>
<p>i. exchange register extracts;</p>
<p>ii. agree common formats.</p>
<p>B. Joint inspection</p>
<p>i. inspect lanterns in pairs;</p>
<p>ii. report faults promptly.</p>
<p>C. Cross-harbour cases</p>
<p>i. coordinate where two harbours share a channel;</p>
<p>ii. name a lead harbour.</p>
<p>V. INVITES the Secretary-General to disseminate this Recommendation.</p>
<p>Annex I: Good Practice Guidance on Keeping Specific
Lanterns Lit in Every Harbour of the Realm</p>
<p>Having regard to the findings of the Harbour Committee,</p>
<p>A. Lanterns on the quay: Duty to Light</p>
<p>1. Quay lanterns should be lit at dusk.</p>
<p>2. Keepers should be trained. Training should cover:</p>
<p>a. wicks;</p>
<p>b. oil.</p>
<p>B. Lanterns on the pier: Liability of Keepers</p>
<p>1. Keepers should be answerable for faults.</p>
<p>2. Faults should be reported. This should cover:</p>
<p>a. date;</p>
<p>b. place.</p>
<p>Annex II: Good Practice Guidance on Registers</p>
<p>Introduction</p>
<p>This guidance is addressed to keepers and harbour associations.</p>
<p>A. Good Practice Guidance for Keepers</p>
<p>1. Keep a register, including:</p>
<p>i. names;</p>
<p>ii. dates.</p>
<p>B. Actions by Harbour Associations</p>
<p>1. Share registers.</p>
"""


def test_lettered_parts_nest_roman_items_split_and_annexes_stay_attachments() -> None:
    xml, _, root = _convert(_LETTERED_PARTS, type="recommendation")
    _clean(xml)
    eids = _eids(root, "section", "subsection", "paragraph", "point")
    assert not [e for e in eids if "_dup" in e]
    assert [e for e in eids if e.startswith("sec_") and "__" not in e] == [
        "sec_I", "sec_II", "sec_III", "sec_IV", "sec_V",
    ]  # fmt: skip
    assert _eids(root, "subsection")[:3] == [
        "sec_IV__subsec_A", "sec_IV__subsec_B", "sec_IV__subsec_C",
    ]  # fmt: skip
    assert "sec_IV__subsec_C__para_ii" in eids
    assert "sec_II__para_iii" in eids
    assert "sec_III__para_ii__point_a" in eids
    assert "sec_III__para_iii" in eids
    assert "att_1__sec_B__para_2__point_b" in eids
    assert "att_1__sec_A__para_2__point_a" in eids
    assert "att_2__sec_A__para_1__point_ii" in eids
    assert "att_2__sec_B__para_1" in eids


def test_an_instrument_without_lettered_parts_keeps_its_eids() -> None:
    _, _, root = _convert(_COUNCIL_ACT)
    assert _eids(root, "section", "paragraph", "point") == [
        "sec_I", "sec_II", "sec_II__para_1", "sec_II__para_1__point_i",
        "sec_II__para_1__point_ii", "sec_II__para_2", "sec_III", "sec_III__para_3",
        "sec_IV", "sec_V", "sec_V__para_4",
    ]  # fmt: skip


def test_a_lettered_annex_does_not_turn_later_roman_letters_into_sections() -> None:
    letters = "".join(f"<p>{c}. Part {c}</p><p>1. Keep lantern {c} lit.</p>" for c in "ABCDEFGHIJ")
    body = (
        "<p>THE COUNCIL,</p><p>I. RECOMMENDS that Adherents keep lanterns lit.</p>"
        "<p>Annex I: Good Practice Guidance on Lanterns</p>"
        f"<p>Keepers should note this.</p>{letters}"
    )
    xml, _, root = _convert(body, type="recommendation")
    _clean(xml)
    sections = [e for e in _eids(root, "section") if e.startswith("att_1__")]
    assert sections[1:] == [f"att_1__sec_{c}" for c in "ABCDEFGHIJ"]
    assert not _eids(root, "subsection")


def test_an_annex_mention_in_running_text_is_not_a_caption() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents keep lanterns lit.</p>
<p>Annex I: sets out the guidance that keepers should follow when lighting a lantern at dusk</p>
<p>II. RECOMMENDS that Adherents keep registers.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    _clean(xml)
    assert not root.findall(".//a:attachment", NS)
    assert "sec_II" in _eids(root, "section")


def test_an_unpunctuated_lettered_item_stays_text_not_a_heading() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>A. light every lantern at dusk</p>
<p>1. Check the wick.</p>
<p>B. Registers</p>
<p>1. Keep a register.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    _clean(xml)
    a, b = root.findall(".//a:subsection", NS)
    assert a.find("a:heading", NS) is None
    assert "light every lantern at dusk" in "".join(a.itertext())
    assert b.findtext("a:heading", namespaces=NS) == "Registers"


def test_a_lettered_part_keeps_the_declarants_voice_for_its_items() -> None:
    body = """
<p>WE, THE MINISTERS of the lantern ports,</p>
<p>DECLARE that we:</p>
<p>A. Coordination</p>
<p>1. Call on the Harbour Committee to count every keeper.</p>
<p>2. ENCOURAGE green investment in harbour lighting.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    assert "sec_I__subsec_A__para_1" in _eids(root, "paragraph")
    assert _marked(root) == {"sec_I__subsec_A__para_1"}


def test_an_annex_caption_followed_by_a_lettered_heading_keeps_the_heading() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents keep lanterns lit.</p>
<p>Annex I: Good Practice Guidance on Lanterns</p>
<p>A. Quay lanterns</p>
<p>1. Keep lamps.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    _clean(xml)
    assert "att_1__sec_A__para_1" in _eids(root, "paragraph")
    assert root.xpath("//a:attachment//a:section/a:heading/text()", namespaces=NS) == [
        "Quay lanterns"
    ]


# The shape of OECD/LEGAL/0194, Annex II: a numbered list under each heading,
# every list starting again at `1.`, and prose that prints no number.
_RESTARTED_LISTS = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents apply the principles set out in the Annex.</p>
<p>ANNEX</p>
<p>PRINCIPLES OF GOOD LANTERN KEEPING</p>
<p>Preface</p>
<p>Harbours and keepers share an interest in well kept lanterns.</p>
<p>These principles replace those of the previous decade.</p>
<p>Lamp rooms</p>
<p>1. A lamp room should be dry.</p>
<p>2. A lamp room should be locked at night.</p>
<p>Oil stores</p>
<p>1. Oil should be kept apart from wicks, in:</p>
<p>a) a stone store;</p>
<p>b) a tin chest.</p>
<p>2. An oil store should be aired each week.</p>
<p>3. Spilt oil should be reported.</p>
<p>Records</p>
<p>The keeper is answerable for the register.</p>
<p>1. Each lighting should be entered.</p>
<p>2. Each fault should be entered.</p>
<p>3. The register should be kept for ten years.</p>
"""


def _numbers(root: etree._Element, *tags: str) -> dict[str, str | None]:
    """Each element's eId against the number it prints, None where it prints none."""
    return {
        el.get("eId") or "": el.findtext("a:num", namespaces=NS)
        for el in root.iter(*[f"{{{NS['a']}}}{t}" for t in tags])
    }


def test_a_list_restarting_under_each_heading_is_numbered_within_its_section() -> None:
    xml, _, root = _convert(_RESTARTED_LISTS, type="recommendation")
    _clean(xml)
    # A number repeats only among the paragraphs it ends up beside, so no list
    # carries a suffix for the lists printed above it.
    assert _numbers(root, "paragraph", "point") == {
        "att_1__sec_1__para_1": None,
        "att_1__sec_1__para_2": None,
        "att_1__sec_2__para_1": "1.",
        "att_1__sec_2__para_2": "2.",
        "att_1__sec_3__para_1": "1.",
        "att_1__sec_3__para_1__point_a": "a)",
        "att_1__sec_3__para_1__point_b": "b)",
        "att_1__sec_3__para_2": "2.",
        "att_1__sec_3__para_3": "3.",
        # The annex's third unnumbered paragraph stands beside a printed `3.`:
        # the printed number names its own paragraph.
        "att_1__sec_4__para_3_dup2": None,
        "att_1__sec_4__para_1": "1.",
        "att_1__sec_4__para_2": "2.",
        "att_1__sec_4__para_3": "3.",
    }


def test_a_printed_number_keeps_its_eid_when_prose_is_added_above() -> None:
    """One more unnumbered paragraph earlier in the annex moves no numbered eId."""
    longer = _RESTARTED_LISTS.replace(
        "<p>Lamp rooms</p>", "<p>They apply in every harbour.</p><p>Lamp rooms</p>"
    )
    _, _, before = _convert(_RESTARTED_LISTS, type="recommendation")
    _, _, after = _convert(longer, type="recommendation")

    def numbered(root: etree._Element) -> list[str]:
        return [eid for eid, num in _numbers(root, "paragraph", "point").items() if num]

    assert numbered(after) == numbered(before)
    assert not [eid for eid in numbered(after) if "_dup" in eid]


def test_rows_derive_from_restarted_lists_with_distinct_ids() -> None:
    xml, _, _ = _convert(_RESTARTED_LISTS, type="recommendation")
    _, _, provisions, *_ = document_to_rows(parse_akn(xml), law_id=uuid.uuid4())
    ids = [p.akn_eid for p in provisions]
    assert len(ids) == len(set(ids))
    assert not [eid for eid in ids if "_dup" in eid and "sec_4__para_3_dup2" not in eid]


# The shape of OECD/LEGAL/0002, Annex A: a second list of sections under a
# heading of its own, keeping the numbers of the first and skipping some.
_TWO_LISTS = """
<p>THE COUNCIL,</p>
<p>I. DECIDES that Members shall keep the lists set out in Annex A.</p>
<p>Annex A</p>
<p>Lists of Lantern Duties</p>
<p>LIST A</p>
<p>I. Lighting at dusk</p>
<p>II. Lighting in fog</p>
<p>1. A fog lantern is lit on the word of the harbour master.</p>
<p>III. Trimming of wicks</p>
<p>IV. Storage of oil</p>
<p>LIST B</p>
<p>II. Lighting in fog</p>
<p>1. A fog lantern may be lit by any keeper.</p>
<p>IV. Storage of oil</p>
<p>V. Keeping of registers</p>
"""


def test_a_second_list_of_sections_under_its_heading_is_a_group() -> None:
    xml, _, root = _convert(_TWO_LISTS, type="decision")
    _clean(xml)
    assert _eids(root, "section", "hcontainer", "paragraph") == [
        "sec_I",
        "att_1__sec_I",
        "att_1__sec_II",
        "att_1__sec_II__para_1",
        "att_1__sec_III",
        "att_1__sec_IV",
        "att_1__hcontainer_1",
        "att_1__hcontainer_1__sec_II",
        "att_1__hcontainer_1__sec_II__para_1",
        "att_1__hcontainer_1__sec_IV",
        # `V.` follows the first list's `IV.` and still belongs to the second.
        "att_1__hcontainer_1__sec_V",
    ]
    group = root.find(".//a:attachment//a:hcontainer", NS)
    assert group is not None
    assert group.get("name") == "group"
    assert group.findtext("a:heading", namespaces=NS) == "LIST B"
    # The heading names the list, not the list's first section.
    assert group.find("a:section/a:heading", NS) is None


# The shape of OECD/LEGAL/0307: procedures appended to the body under a title,
# numbered `I.` to `III.` as the sections before them are.
_APPENDED_PROCEDURES = """
<p>THE COUNCIL,</p>
<p>HAVING REGARD to Article 5 a) of the Convention;</p>
<p>DECIDES:</p>
<p>I. Harbour Offices</p>
<p>1. Adherents shall set up harbour offices.</p>
<p>2. Harbour offices shall meet each year.</p>
<p>II. The Harbour Committee</p>
<p>1. The Committee shall oversee this Decision.</p>
<p>Procedures</p>
<p>I. Harbour Offices</p>
<p>1. A harbour office will answer enquiries.</p>
<p>II. The Harbour Committee</p>
<p>1. The Committee will consider requests from keepers.</p>
<p>III. Review</p>
<p>1. These procedures are reviewed every five years.</p>
"""


def test_a_text_reproduced_in_the_body_under_its_title_is_a_group() -> None:
    xml, _, root = _convert(_APPENDED_PROCEDURES, type="decision")
    _clean(xml)
    assert _eids(root, "section", "hcontainer", "paragraph") == [
        "sec_I",
        "sec_I__para_1",
        "sec_I__para_2",
        "sec_II",
        "sec_II__para_1",
        "hcontainer_1",
        "hcontainer_1__sec_I",
        "hcontainer_1__sec_I__para_1",
        "hcontainer_1__sec_II",
        "hcontainer_1__sec_II__para_1",
        "hcontainer_1__sec_III",
        "hcontainer_1__sec_III__para_1",
    ]
    assert root.findtext(".//a:body/a:hcontainer/a:heading", namespaces=NS) == "Procedures"
    _, _, provisions, *_ = document_to_rows(parse_akn(xml), law_id=uuid.uuid4())
    ids = [p.akn_eid for p in provisions]
    assert len(ids) == len(set(ids))
    assert not [eid for eid in ids if "_dup" in eid]


def test_each_reproduced_text_is_a_group_of_its_own() -> None:
    body = _APPENDED_PROCEDURES + (
        "<p>Commentary</p><p>I. Harbour Offices</p><p>1. An office may be shared.</p>"
    )
    xml, _, root = _convert(body, type="decision")
    _clean(xml)
    assert _eids(root, "hcontainer") == ["hcontainer_1", "hcontainer_2"]
    assert "hcontainer_2__sec_I__para_1" in _eids(root, "paragraph")
    assert not [e for e in _eids(root, "section", "paragraph") if "_dup" in e]


def test_a_verb_section_after_a_group_is_the_bodys_own() -> None:
    """A text of two sections reproduced under section II, as long as the body
    so far: the next verb carries the body on, whatever its number follows."""
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents light every lantern.</p>
<p>II. RECOMMENDS that Adherents report against the reference points below.</p>
<p>Reference points</p>
<p>I. Lanterns lit</p>
<p>II. Keepers trained</p>
<p>III. INVITES the Secretary-General to disseminate this Recommendation.</p>
<p>IV. INSTRUCTS the Harbour Committee to report to the Council.</p>
"""
    xml, provenance, root = _convert(body, type="recommendation")
    _clean(xml)
    assert _eids(root, "section", "hcontainer") == [
        "sec_I",
        "sec_II",
        "hcontainer_1",
        "hcontainer_1__sec_I",
        "hcontainer_1__sec_II",
        "sec_III",
        "sec_IV",
    ]
    assert _marked(root) == {"sec_III", "sec_IV"}
    html_source = provenance["html_source"]
    assert isinstance(html_source, dict)
    assert html_source["verbs"] == ["RECOMMENDS", "RECOMMENDS", "INVITES", "INSTRUCTS"]


def test_a_group_of_verb_sections_keeps_its_own_count() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents follow the procedures below.</p>
<p>Procedures</p>
<p>I. AGREES that harbour offices answer enquiries.</p>
<p>II. AGREES that harbour offices meet each year.</p>
<p>INVITES the Secretary-General to disseminate these procedures.</p>
"""
    xml, provenance, root = _convert(body, type="recommendation")
    validate_akn(xml)
    # The unnumbered verb is counted among the group's sections, not the body's.
    assert _numbers(root, "section") == {
        "sec_I": "I.",
        "hcontainer_1__sec_I": "I.",
        "hcontainer_1__sec_II": "II.",
        "hcontainer_1__sec_III": "III.",
    }
    html_source = provenance["html_source"]
    assert isinstance(html_source, dict)
    assert html_source["verbs"] == ["RECOMMENDS", "AGREES", "AGREES", "INVITES"]


def test_a_numbered_table_row_opens_no_group() -> None:
    """A table numbers its rows `I.`, `II.` inside section III: rows are not a
    second text, and the body's own sections stay where they were."""
    body = """
<p>THE COUNCIL,</p>
<p>I. AGREES that this Recommendation concerns harbour lanterns.</p>
<p>II. RECOMMENDS that Adherents light every lantern at dusk.</p>
<p>III. RECOMMENDS that Adherents report their position against the reference points:</p>
<table>
<tr><td>Position</td><td>Reference point</td></tr>
<tr><td>I. Lanterns lit</td><td>0.6</td></tr>
<tr><td>II. Keepers trained</td><td>0.4</td></tr>
<tr><td>III. Wicks trimmed</td><td>0.2</td></tr>
</table>
<p>IV. INVITES the Secretary-General to disseminate this Recommendation.</p>
<p>V. INSTRUCTS the Harbour Committee to report to the Council.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    validate_akn(xml)
    assert not _eids(root, "hcontainer")
    assert _eids(root, "section") == [
        "sec_I",
        "sec_II",
        "sec_III",
        "sec_I_dup2",
        "sec_II_dup2",
        "sec_III_dup2",
        "sec_IV",
        "sec_V",
    ]
    assert _marked(root) == {"sec_IV", "sec_V"}


def test_chapters_reproduced_after_a_summary_are_a_group() -> None:
    """A summary lists the chapters, then the model text prints them in full. The
    group takes the heading line directly before the restart; the model's title,
    cut off from it by a line of prose, stays where it was read before."""
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents follow the model text in the Annex.</p>
<p>Annex</p>
<p>SUMMARY</p>
<p>CHAPTER I</p>
<p>Scope</p>
<p>CHAPTER II</p>
<p>Definitions</p>
<p>MODEL CONVENTION ON HARBOUR LANTERNS</p>
<p>Convention between (State A) and (State B) on the keeping of the harbour lanterns that
stand along their common coast and its approaches</p>
<p>OPENING WORDS</p>
<p>CHAPTER I</p>
<p>SCOPE</p>
<p>Article 1</p>
<p>This Convention applies to fixed harbour lanterns.</p>
<p>CHAPTER II</p>
<p>DEFINITIONS</p>
<p>Article 2</p>
<p>A lantern is any fixed harbour light.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    validate_akn(xml)
    assert _eids(root, "chapter", "hcontainer", "article", "paragraph") == [
        "att_1__chp_i",
        "att_1__chp_ii",
        "att_1__chp_ii__para_1",
        "att_1__hcontainer_1",
        "att_1__hcontainer_1__chp_i",
        "att_1__hcontainer_1__chp_i__art_1",
        "att_1__hcontainer_1__chp_ii",
        "att_1__hcontainer_1__chp_ii__art_2",
    ]
    group = root.find(".//a:attachment//a:hcontainer", NS)
    assert group is not None
    assert group.findtext("a:heading", namespaces=NS) == "OPENING WORDS"
    assert group.xpath("a:chapter/a:heading/text()", namespaces=NS) == ["SCOPE", "DEFINITIONS"]
    title = "//a:paragraph[@eId='att_1__chp_ii__para_1']/a:heading/text()"
    assert root.xpath(title, namespaces=NS) == ["MODEL CONVENTION ON HARBOUR LANTERNS"]


def test_the_first_of_two_heading_lines_heads_the_group() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>a) light every lantern at dusk;</p>
<p>b) trim every wick.</p>
<p>Second list</p>
<p>Registers</p>
<p>a) keep a register of keepers;</p>
<p>b) publish it each year.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    _clean(xml)
    assert root.xpath("//a:hcontainer/a:heading/text()", namespaces=NS) == ["Second list"]
    first = "//a:paragraph[@eId='sec_I__hcontainer_1__para_a']/a:heading/text()"
    assert root.xpath(first, namespaces=NS) == ["Registers"]


def test_a_heading_before_a_number_only_an_unnumbered_paragraph_holds_opens_no_group() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>Keep every lantern lit through the night and through fog, in summer as in winter, on
every quay.</p>
<p>Registers</p>
<p>1. Keep a register of keepers.</p>
<p>2. Publish it each year.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    validate_akn(xml)
    assert not _eids(root, "hcontainer")
    assert _numbers(root, "paragraph") == {
        "sec_I__para_1_dup2": None,
        "sec_I__para_1": "1.",
        "sec_I__para_2": "2.",
    }


def test_an_unnumbered_paragraphs_suffix_ignores_the_numbers_repeated_below_it() -> None:
    annex = (
        "<p>THE COUNCIL,</p><p>I. RECOMMENDS that Adherents apply the Annex.</p><p>Annex</p>"
        "<p>Each keeper answers for the lanterns of one quay.</p>"
        "<p>1. A keeper lights them at dusk.</p><p>2. A keeper trims them at dawn.</p>"
    )
    _, _, short = _convert(annex, type="recommendation")
    _, _, longer = _convert(annex + "<p>1. A keeper reports faults.</p>", type="recommendation")
    assert _numbers(short, "paragraph") == {
        "att_1__sec_1__para_1_dup2": None,
        "att_1__sec_1__para_1": "1.",
        "att_1__sec_1__para_2": "2.",
    }
    assert _numbers(longer, "paragraph") == {
        **_numbers(short, "paragraph"),
        "att_1__sec_1__para_1_dup3": "1.",
    }


def test_a_verb_section_naming_no_one_judges_the_items_of_its_groups() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents light every lantern at dusk.</p>
<p>II. INVITES:</p>
<p>Reporting</p>
<p>a) the Secretary-General to report on lanterns each year;</p>
<p>b) the Harbour Committee to review the reports.</p>
<p>Outreach</p>
<p>a) the Secretary-General to disseminate this Recommendation;</p>
<p>b) the Harbour Committee to hold a forum of keepers.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    _clean(xml)
    assert _marked(root) == {
        "sec_II",
        "sec_II__para_a",
        "sec_II__para_b",
        "sec_II__hcontainer_1__para_a",
        "sec_II__hcontainer_1__para_b",
    }


def test_a_lettered_list_restarting_under_a_heading_inside_a_section_is_a_group() -> None:
    """The shape of OECD/LEGAL/0449: headed lists of `a)`, `b)` in one section."""
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>Lighting</p>
<p>a) light every lantern at dusk;</p>
<p>b) trim every wick.</p>
<p>Registers</p>
<p>a) keep a register of keepers;</p>
<p>b) publish it each year;</p>
<p>c) correct it on request.</p>
<p>II. INVITES the Secretary-General to disseminate this Recommendation.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    _clean(xml)
    assert _eids(root, "section", "hcontainer", "paragraph") == [
        "sec_I",
        "sec_I__para_a",
        "sec_I__para_b",
        "sec_I__hcontainer_1",
        "sec_I__hcontainer_1__para_a",
        "sec_I__hcontainer_1__para_b",
        "sec_I__hcontainer_1__para_c",
        "sec_II",
    ]
    # The first list is as it was: its heading stays on its first item.
    assert root.xpath("//a:paragraph[@eId='sec_I__para_a']/a:heading/text()", namespaces=NS) == [
        "Lighting"
    ]
    assert root.xpath("//a:hcontainer/a:heading/text()", namespaces=NS) == ["Registers"]


def test_a_group_keeps_the_declarants_voice_for_its_items() -> None:
    body = """
<p>WE, THE MINISTERS of the lantern ports,</p>
<p>DECLARE that we:</p>
<p>Coordination</p>
<p>1. Support the work of the harbour masters.</p>
<p>2. Call on the Harbour Committee to count every keeper.</p>
<p>Review</p>
<p>1. Call on the Harbour Committee to report each year.</p>
<p>2. Welcome the work of keepers.</p>
"""
    xml, _, root = _convert(body, type="declaration")
    _clean(xml)
    assert _eids(root, "paragraph") == [
        "sec_I__para_1",
        "sec_I__para_2",
        "sec_I__hcontainer_1__para_1",
        "sec_I__hcontainer_1__para_2",
    ]
    assert _marked(root) == {"sec_I__para_2", "sec_I__hcontainer_1__para_1"}


def test_a_list_restarting_with_no_heading_keeps_a_suffix_counted_in_its_section() -> None:
    """Nothing in the source marks the second list, so it keeps the suffix it
    had, counted within its own section."""
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>1. Light every lantern.</p>
<p>2. Trim every wick.</p>
<p>1. Keep a register.</p>
<p>II. RECOMMENDS that Adherents:</p>
<p>1. Train every keeper.</p>
<p>2. Pay every keeper.</p>
<p>1. Publish the register.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    validate_akn(xml)
    assert _eids(root, "paragraph") == [
        "sec_I__para_1",
        "sec_I__para_2",
        "sec_I__para_1_dup2",
        "sec_II__para_1",
        "sec_II__para_2",
        "sec_II__para_1_dup2",
    ]


def test_a_verb_on_a_line_of_its_own_heads_its_section_not_a_group() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>DECIDES:</p>
<p>I. Harbour offices shall open at dawn.</p>
<p>II. Harbour offices shall close at dusk.</p>
<p>INVITES</p>
<p>I. the Secretary-General to report on lanterns each year.</p>
<p>II. the Harbour Committee to review the reports.</p>
"""
    xml, _, root = _convert(body, type="decision")
    validate_akn(xml)
    assert not _eids(root, "hcontainer")
    assert root.xpath("//a:section[@eId='sec_I_dup2']/a:heading/text()", namespaces=NS) == [
        "INVITES"
    ]
    assert "sec_I_dup2" in _marked(root)


def test_a_numbered_table_row_ends_no_group() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents light every lantern.</p>
<p>II. RECOMMENDS that Adherents report against the reference points below.</p>
<p>Reference points</p>
<p>I. Lanterns lit</p>
<table><tr><td>II. INVITES keepers to count</td><td>0.4</td></tr></table>
<p>III. Wicks trimmed</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    validate_akn(xml)
    assert _eids(root, "section", "hcontainer") == [
        "sec_I",
        "sec_II",
        "hcontainer_1",
        "hcontainer_1__sec_I",
        "hcontainer_1__sec_II",
        "hcontainer_1__sec_III",
    ]


def test_an_unnumbered_verb_in_a_group_follows_the_groups_last_number() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents follow the procedures below.</p>
<p>II. RECOMMENDS that Adherents review them each year.</p>
<p>Procedures</p>
<p>II. AGREES that harbour offices answer enquiries.</p>
<p>INVITES the Secretary-General to disseminate these procedures.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    validate_akn(xml)
    assert _numbers(root, "section") == {
        "sec_I": "I.",
        "sec_II": "II.",
        "hcontainer_1__sec_II": "II.",
        "hcontainer_1__sec_III": "III.",
    }


def test_bullets_before_a_headed_first_number_open_no_group() -> None:
    """A bullet's ordinal is no printed number of the list that follows."""
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>- light every lantern;</p>
<p>- trim every wick.</p>
<p>Registers</p>
<p>1. Keep a register of keepers.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    validate_akn(xml)
    assert not _eids(root, "hcontainer")
    assert "sec_I__para_1" in _eids(root, "paragraph")


def test_headed_prose_after_a_numbered_list_opens_no_group() -> None:
    """The prose is the section's first unnumbered paragraph: its ordinal is the
    list's `1.`, but it prints no number and starts nothing over."""
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>1. Light every lantern.</p>
<p>2. Trim every wick.</p>
<p>Registers</p>
<p>Each harbour keeps a register of its keepers and of the lanterns that each of them
tends, and publishes it.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    validate_akn(xml)
    assert not _eids(root, "hcontainer")
    assert _numbers(root, "paragraph") == {
        "sec_I__para_1": "1.",
        "sec_I__para_2": "2.",
        "sec_I__para_1_dup2": None,
    }


def test_items_after_a_repeated_number_nest_as_after_its_first_printing() -> None:
    body = """
<p>THE COUNCIL,</p>
<p>I. RECOMMENDS that Adherents:</p>
<p>i. record every keeper;</p>
<p>ii. train each keeper;</p>
<p>ii. pay each keeper, including:</p>
<p>a. night watch pay;</p>
<p>b. weather pay;</p>
<p>iii. publish the register.</p>
"""
    xml, _, root = _convert(body, type="recommendation")
    validate_akn(xml)
    assert _eids(root, "paragraph", "point") == [
        "sec_I__para_i",
        "sec_I__para_ii",
        "sec_I__para_ii_dup2",
        "sec_I__para_ii_dup2__point_a",
        "sec_I__para_ii_dup2__point_b",
        "sec_I__para_iii",
    ]
