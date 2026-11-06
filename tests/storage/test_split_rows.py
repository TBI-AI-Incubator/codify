"""Rows split off a provision: an ``<intro>`` before its rowed children and a
``<wrapUp>`` after them, as the mapper writes them and the reader renders them."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from lxml import etree

from codify.akn._schema import AKN_NS
from codify.akn.io import parse_akn
from codify.pipeline.formats.riigi_teataja import riigi_teataja_to_akn
from codify.storage.documents import (
    DocumentProvisionBlock,
    InlineRef,
    InlineText,
    _eid_index,
    _to_provision,
)
from codify.storage.mappers import document_to_rows

_A = f"{{{AKN_NS}}}"


def _act(sisu: str) -> str:
    akn_xml, _ = riigi_teataja_to_akn(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<oigusakt xmlns="Juurakt">
	<metaandmed>
		<valjaandja>Vabariigi Valitsus</valjaandja>
		<dokumentLiik>määrus</dokumentLiik>
		<vastuvoetud><aktikuupaev>2020-01-01</aktikuupaev></vastuvoetud>
	</metaandmed>
	<aktinimi><nimi><pealkiri>Näidismäärus</pealkiri></nimi></aktinimi>
	<sisu>{sisu}</sisu>
</oigusakt>"""
    )
    return akn_xml


def _akn(body: str, country: str) -> str:
    uri = f"/akn/{country}/act/2020/1"
    exp = f"{uri}/eng@2020-01-01"
    return f"""<akomaNtoso xmlns="{AKN_NS}"><act name="act"><meta>
<identification source="#x">
<FRBRWork><FRBRthis value="{uri}"/><FRBRuri value="{uri}"/>
<FRBRdate date="2020-01-01" name="enacted"/>
<FRBRauthor href="#x"/><FRBRcountry value="{country}"/></FRBRWork>
<FRBRExpression><FRBRthis value="{exp}"/><FRBRuri value="{exp}"/>
<FRBRdate date="2020-01-01" name="expression"/><FRBRauthor href="#x"/><FRBRlanguage language="eng"/>
</FRBRExpression>
<FRBRManifestation><FRBRthis value="{exp}.akn"/><FRBRuri value="{exp}.akn"/>
<FRBRdate date="2020-01-01" name="generation"/><FRBRauthor href="#x"/></FRBRManifestation>
</identification></meta><body>{body}</body></act></akomaNtoso>"""


def _inline(nodes: list) -> str:  # type: ignore[type-arg]
    return "".join(n.text for n in nodes if isinstance(n, InlineText)).strip()


def _blocks(block: DocumentProvisionBlock) -> Iterator[DocumentProvisionBlock]:
    for b in block.blocks:
        yield b
        yield from _blocks(b)


def _rows_and_reader(
    akn_xml: str,
) -> tuple[list[tuple[str, str]], dict[str, list[DocumentProvisionBlock]]]:
    _, _, rows, _, _, _ = document_to_rows(parse_akn(akn_xml), law_id=uuid.uuid4())
    by_section: dict[object, list[tuple[int, str, str]]] = {}
    for r in rows:
        by_section.setdefault(r.section_id, []).append((r.position, r.akn_eid, r.text))
    reading = [(eid, text) for sec in by_section.values() for _, eid, text in sorted(sec)]
    persisted = frozenset(r.akn_eid for r in rows)
    index = _eid_index(etree.fromstring(akn_xml.encode()))
    return reading, {r.akn_eid: _to_provision(r, index, persisted).blocks for r in rows}


def test_a_uk_subsection_lead_in_and_closing_words_get_rows() -> None:
    reading, rendered = _rows_and_reader(
        _akn(
            """<section eId="section-1"><num>1</num><heading>Powers</heading>
<subsection eId="section-1-1"><num>(1)</num><intro><p>The Secretary of State may—</p></intro>
<paragraph eId="section-1-1-a"><num>(a)</num><content><p>make rules;</p></content></paragraph>
<paragraph eId="section-1-1-b"><num>(b)</num><content><p>issue guidance,</p></content></paragraph>
<wrapUp><p>but only after consultation.</p></wrapUp></subsection>
<subsection eId="section-1-2"><num>(2)</num><content><p>Rules are laid.</p></content></subsection>
</section>""",
            "gb",
        )
    )
    assert reading == [
        ("section-1-1__intro", "The Secretary of State may—"),
        ("section-1-1-a", "make rules;"),
        ("section-1-1-b", "issue guidance,"),
        ("section-1-1__wrapup", "but only after consultation."),
        ("section-1-2__content", "Rules are laid."),
    ]
    assert _inline(rendered["section-1-1__intro"][0].intro) == "The Secretary of State may—"
    assert _inline(rendered["section-1-1__wrapup"][0].intro) == "but only after consultation."


def test_an_eu_article_and_paragraph_keep_lead_in_and_wrap_up_in_order() -> None:
    reading, rendered = _rows_and_reader(
        _akn(
            """<article eId="art_1"><num>Article 1</num><heading>Definitions</heading>
<intro><p>For the purposes of this Directive:</p></intro>
<point eId="art_1__point_1"><num>(1)</num><content><p>'ship' means a vessel;</p></content></point>
<wrapUp><p>Member States may extend these terms.</p></wrapUp></article>
<article eId="art_2"><num>Article 2</num><paragraph eId="art_2__para_1"><num>1.</num>
<intro><p>This Directive applies to:</p></intro>
<point eId="art_2__para_1__point_a"><num>(a)</num><content><p>ships,</p></content></point>
<wrapUp><p>unless excluded.</p></wrapUp></paragraph></article>""",
            "eu",
        )
    )
    assert reading == [
        ("art_1__intro", "For the purposes of this Directive:"),
        ("art_1__point_1", "'ship' means a vessel;"),
        ("art_1__wrapup", "Member States may extend these terms."),
        ("art_2__para_1", "This Directive applies to:"),
        ("art_2__para_1__point_a", "ships,"),
        ("art_2__para_1__wrapup", "unless excluded."),
    ]
    # The paragraph's own block leaves its closing words to the row after the point.
    (para,) = rendered["art_2__para_1"]
    assert (_inline(para.intro), para.wrap_up) == ("This Directive applies to:", [])
    assert _inline(rendered["art_2__para_1__wrapup"][0].intro) == "unless excluded."


def test_stored_rows_and_reader_keep_unit_text_in_reading_order() -> None:

    table = (
        "<![CDATA[<table><tr><td>{0}</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></table>]]>"
    )
    akn_xml = _act(
        f"""<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
			<sisuTekst><tavatekst>Ees.</tavatekst></sisuTekst>
			<loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
				<sisuTekst><tavatekst>Loetelu:</tavatekst></sisuTekst>
				<alampunkt><alampunktNr>1</alampunktNr><kuvatavNr>1)</kuvatavNr>
					<sisuTekst><tavatekst>punkt;</tavatekst></sisuTekst></alampunkt>
				<sisuTekst><tavatekst>Lõpetus.</tavatekst>
					<HTMLKonteiner>{table.format("L")}</HTMLKonteiner></sisuTekst></loige>
			<sisuTekst><tavatekst>Lõpp.</tavatekst>
				<HTMLKonteiner>{table.format("P")}</HTMLKonteiner></sisuTekst>
		</paragrahv>
		<paragrahv><paragrahvNr>2</paragrahvNr><kuvatavNr>§ 2.</kuvatavNr>
			<sisuTekst><HTMLKonteiner><![CDATA[<table><tr><td>A</td><td>10</td></tr>
			<tr><td>B</td><td><p>20</p><p>eurot</p></td></tr></table>]]></HTMLKonteiner></sisuTekst>
		</paragrahv>"""
    )
    _, _, rows, _, _, _ = document_to_rows(parse_akn(akn_xml), law_id=uuid.uuid4())
    by_section: dict[object, list[tuple[int, str, str]]] = {}
    for r in rows:
        by_section.setdefault(r.section_id, []).append((r.position, r.akn_eid, r.text))
    reading = [(eid, text) for sec in by_section.values() for _, eid, text in sorted(sec)]
    assert reading == [
        ("art_1__intro", "Ees."),
        ("art_1__para_1", "Loetelu:"),
        ("art_1__para_1__point_1", "punkt;"),
        ("art_1__para_1__wrapup", "Lõpetus. L 1 B 2"),
        ("art_1__wrapup", "Lõpp. P 1 B 2"),
        ("art_2__content", "A 10 B 20 eurot"),
    ]
    # The reader renders each split-off run in its own row, tables included,
    # and the lõige's own block leaves its wrap-up to that row.
    persisted = frozenset(r.akn_eid for r in rows)
    index = _eid_index(etree.fromstring(akn_xml.encode()))
    rendered = {r.akn_eid: _to_provision(r, index, persisted).blocks for r in rows}
    para = rendered["art_1__para_1"][0]
    assert para.wrap_up == []
    assert [b.akn_type for b in para.blocks] == []
    for eid, table_eid in (
        ("art_1__para_1__wrapup", "art_1__para_1__table_1"),
        ("art_1__wrapup", "art_1__table_1"),
    ):
        (closing,) = rendered[eid]
        assert [b.akn_eid for b in closing.blocks] == [table_eid]
    assert _inline(rendered["art_1__para_1__wrapup"][0].intro) == "Lõpetus."
    assert _inline(rendered["art_1__wrapup"][0].intro) == "Lõpp."


def test_a_wrap_up_closing_subsections_is_not_rowed_before_them() -> None:

    from codify.akn import Article, Chapter
    from codify.akn.document import Document

    chapter = Chapter(
        akn_eid="chp_1",
        akn_type="chapter",
        position=0,
        wrap_up="Closing words.",
        children=[Article(akn_eid="chp_1__art_1", akn_type="article", position=0, text="Body.")],
    )
    doc = Document(
        id=uuid.uuid4(),
        frbr_work_uri="/akn/xz/act/2012/7",
        frbr_expression_uri="/akn/xz/act/2012/7/est@2012",
        language="est",
        expression_date="2012-01-01",
        body=[chapter],
    )
    _, _, rows, _, _, _ = document_to_rows(doc, law_id=uuid.uuid4())
    assert [r.akn_eid for r in rows] == ["chp_1__art_1__content"]


def test_closing_text_and_anchors_land_on_the_wrap_up_row() -> None:

    from codify.storage.resolve_refs import nearest_stored_row

    # A sub-point grouping points has no text of its own, only a closing run.
    akn_xml = _act(
        """<punkt><punktNr>1</punktNr><kuvatavNr>1.</kuvatavNr>
			<sisuTekst><tavatekst>Juht:</tavatekst></sisuTekst>
			<alampunkt><alampunktNr>1</alampunktNr><kuvatavNr>1)</kuvatavNr>
				<alampunkt><alampunktNr>1</alampunktNr><kuvatavNr>a)</kuvatavNr>
					<sisuTekst><tavatekst>Sisu.</tavatekst></sisuTekst></alampunkt>
				<sisuTekst><tavatekst>Lõpetus.</tavatekst><HTMLKonteiner><![CDATA[<table><tr>
				<td>X</td><td>1</td></tr><tr><td>Y</td><td>2</td></tr></table>]]></HTMLKonteiner>
				</sisuTekst></alampunkt>
		</punkt>"""
    )
    _, sections, rows, _, _, _ = document_to_rows(parse_akn(akn_xml), law_id=uuid.uuid4())
    eids = [r.akn_eid for r in rows]
    assert eids == ["point_1", "point_1__point_1__point_1", "point_1__point_1__wrapup"]
    index = _eid_index(etree.fromstring(akn_xml.encode()))
    persisted = frozenset(eids)
    lead = _to_provision(rows[0], index, persisted).blocks[0]
    # The group under the lead has no row, so the lead's block walks into it,
    # and leaves the group's closing run and table to the wrap-up row.
    (group,) = lead.blocks
    assert group.akn_eid == "point_1__point_1"
    assert [(b.akn_eid, b.wrap_up, b.blocks) for b in _blocks(lead)] == [
        ("point_1__point_1", [], [])
    ]
    anchor = nearest_stored_row(
        index, "point_1__point_1__table_1", persisted, {s.akn_eid for s in sections}
    )
    assert anchor == ("point_1__point_1__wrapup", "enclosing")
    # An anchor naming the wrap-up itself lands on its row too.
    wrap = index["point_1__point_1__table_1"].getparent()
    assert wrap is not None
    wrap.set("eId", "closing")
    index["closing"] = wrap
    assert nearest_stored_row(index, "closing", persisted) == (
        "point_1__point_1__wrapup",
        "container",
    )


def test_compact_closing_text_keeps_its_table_boundaries() -> None:

    akn_xml = _act(
        "<punkt><punktNr>1</punktNr><kuvatavNr>1.</kuvatavNr>"
        "<sisuTekst><tavatekst>Juht:</tavatekst></sisuTekst>"
        "<alampunkt><alampunktNr>1</alampunktNr><kuvatavNr>1)</kuvatavNr>"
        "<sisuTekst><tavatekst>Sisu.</tavatekst></sisuTekst></alampunkt>"
        "<sisuTekst><tavatekst>Lõpetus.</tavatekst><HTMLKonteiner><![CDATA[<table><tr><td>A</td>"
        "<td>10</td></tr><tr><td>B</td><td><p>20</p><p>eurot</p></td></tr></table>]]>"
        "</HTMLKonteiner></sisuTekst></punkt>"
    )
    # Written without indentation, so no whitespace stands between the blocks.
    compact = etree.tostring(
        etree.fromstring(akn_xml.encode(), etree.XMLParser(remove_blank_text=True))
    )
    _, _, rows, _, _, _ = document_to_rows(parse_akn(compact.decode()), law_id=uuid.uuid4())
    closing = next(r.text for r in rows if r.akn_eid == "point_1__wrapup")
    assert closing == "Lõpetus. A 10 B 20 eurot"


def test_named_blocks_in_a_lead_in_or_wrap_up_keep_their_words() -> None:
    _, rendered = _rows_and_reader(
        _akn(
            """<article eId="art_1"><num>Article 1</num>
<intro><block name="lead">The <ref href="#art_2">Board</ref> shall:</block></intro>
<point eId="art_1__point_a"><num>(a)</num><content><p>meet;</p></content></point>
<wrapUp><block name="closing">Closing words:</block><blockList eId="art_1__wrapup__list_1">
<item eId="art_1__wrapup__list_1__item_1"><p>first;</p></item></blockList></wrapUp></article>
<article eId="art_2"><num>Article 2</num>
<point eId="art_2__point_a"><num>(a)</num><content><p>meet;</p></content></point>
<wrapUp eId="art_2__wrapup"><block name="closing">Closing words.</block></wrapUp></article>""",
            "xz",
        )
    )
    (lead,) = rendered["art_1__intro"]
    assert any(isinstance(n, InlineRef) and n.text == "Board" for n in lead.intro)
    (closing,) = rendered["art_1__wrapup"]
    assert (_inline(closing.intro), [_inline(b.intro) for b in closing.blocks]) == (
        "Closing words:",
        ["first;"],
    )
    # A wrapper may carry its row's name as its own eId.
    assert _inline(rendered["art_2__wrapup"][0].intro) == "Closing words."


def test_a_group_after_a_rowed_sibling_keeps_its_number_in_place() -> None:
    reading, rendered = _rows_and_reader(
        _akn(
            """<article eId="art_1"><num>Article 1</num>
<paragraph eId="art_1__para_1"><num>1.</num><intro><p>Lead:</p></intro>
<point eId="art_1__para_1__point_a"><num>(a)</num><content><p>First</p></content></point>
<point eId="art_1__para_1__point_b"><num>(b)</num>
<point eId="art_1__para_1__point_b__point_i"><num>(i)</num><content><p>Second</p></content></point>
<wrapUp><p>closing.</p></wrapUp></point></paragraph></article>""",
            "xz",
        )
    )
    assert [eid for eid, _ in reading] == [
        "art_1__para_1",
        "art_1__para_1__point_a",
        "art_1__para_1__point_b",
        "art_1__para_1__point_b__point_i",
    ]
    assert rendered["art_1__para_1"][0].blocks == []
    assert rendered["art_1__para_1__point_b"][0].num == "(b)"


def test_a_numbered_group_with_no_row_above_it_keeps_its_closing_words() -> None:
    # Nothing before the points would show the group's number, so its row stays:
    # directly under an article, or as an indent the paragraph's block does not walk.
    reading, rendered = _rows_and_reader(
        _akn(
            """<article eId="art_1"><num>Article 1</num>
<paragraph eId="art_1__para_1"><num>1.</num>
<point eId="art_1__para_1__point_a"><num>(a)</num><content><p>ships,</p></content></point>
<wrapUp><p>unless excluded.</p></wrapUp></paragraph></article>
<article eId="art_2"><num>Article 2</num>
<paragraph eId="art_2__para_1"><num>1.</num><intro><p>Lead:</p></intro>
<indent eId="art_2__para_1__indent_1"><num>-</num>
<point eId="art_2__para_1__indent_1__point_a"><num>(a)</num><content><p>one;</p></content></point>
<wrapUp><p>closing.</p></wrapUp></indent></paragraph></article>""",
            "xz",
        )
    )
    assert reading == [
        ("art_1__para_1", "unless excluded."),
        ("art_1__para_1__point_a", "ships,"),
        ("art_2__para_1", "Lead:"),
        ("art_2__para_1__indent_1", "closing."),
        ("art_2__para_1__indent_1__point_a", "one;"),
    ]
    assert rendered["art_1__para_1"][0].num == "1."
    assert rendered["art_2__para_1__indent_1"][0].num == "-"


def test_content_beside_a_lead_in_or_wrap_up_renders_once() -> None:
    # Schema-invalid, but native AKN is stored as the publisher serves it.
    _, rendered = _rows_and_reader(
        _akn(
            """<article eId="art_1"><num>Article 1</num>
<intro><p>The Board shall:</p></intro><content><p>Meetings are public.</p></content>
<point eId="art_1__point_a"><num>(a)</num><content><p>meet;</p></content></point></article>
<article eId="art_2"><num>Article 2</num><content><p>Body.</p></content>
<wrapUp><p>Closing words.</p></wrapUp></article>""",
            "xz",
        )
    )
    assert [_inline(b.intro) for b in rendered["art_1__intro"]] == [
        "The Board shall:",
        "Meetings are public.",
    ]
    assert [(_inline(b.intro), b.wrap_up) for b in rendered["art_2__content"]] == [("Body.", [])]
    assert _inline(rendered["art_2__wrapup"][0].intro) == "Closing words."
