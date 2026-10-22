"""Compendium body text segments deterministically into AKN, for each shape it takes."""

from __future__ import annotations

import uuid
from pathlib import Path

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
    paras = root.findall(".//a:body/a:paragraph", NS)
    assert [p.get("eId") for p in paras] == ["para_1", "para_2"]
    assert paras[1].findtext("a:heading", namespaces=NS) == "Lighting the future"
    assert _eids(root, "indent") == ["para_2__indent_1", "para_2__indent_2"]


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
    assert excluded == [
        "sec_VIII__content", "sec_VII__content", "sec_VI__content", "sec_V__content",
    ]  # fmt: skip
    assert {flags[eid][1] for eid in excluded} == {"heading"}
    kept = [eid for eid, (flag, _) in flags.items() if flag is False]
    assert len(kept) == len(flags) - 4
    assert "sec_IV__para_12" in kept


def test_paragraphs_under_instructs_leave_the_pool() -> None:
    xml, _, _ = _convert(_COUNCIL_ACT)
    flags = _row_flags(xml)
    assert {eid for eid, (flag, _) in flags.items() if flag} == {"sec_IV__content", "sec_V__para_4"}
    assert flags["sec_V__para_4"] == (True, "heading")
    assert flags["sec_III__para_3"] == (False, None)
    assert flags["sec_II__para_1__point_i"] == (False, None)


def test_the_comparator_excludes_what_the_rows_exclude() -> None:
    xml, _, _ = _convert(_COUNCIL_ACT)
    doc = parse_akn(xml)
    excluded = sorted(el.akn_eid for el in iter_assessable(doc) if is_excluded(el, doc))
    assert excluded == ["sec_IV", "sec_V", "sec_V__para_4"]
