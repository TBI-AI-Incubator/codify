"""A container's lead-in, wrap-up and list text reach provision rows, in reading order."""

from __future__ import annotations

import uuid

from codify.akn._parser import parse_document
from codify.akn.io import to_akn
from codify.storage.documents import (
    InlineRef,
    InlineText,
    _eid_index,
    _parse_version_xml,
    _to_provision,
)
from codify.storage.mappers import document_to_rows, rows_to_document
from codify.storage.resolve_refs import element_index, nearest_stored_row

_META = """<meta><identification source="#codify">
  <FRBRWork><FRBRthis value="/akn/xa/act/2004/9/!main"/><FRBRuri value="/akn/xa/act/2004/9"/>
    <FRBRdate date="2004-05-01" name="Generation"/><FRBRauthor href=""/>
    <FRBRcountry value="xa"/></FRBRWork>
  <FRBRExpression><FRBRthis value="/akn/xa/act/2004/9/eng@2004-05-01/!main"/>
    <FRBRuri value="/akn/xa/act/2004/9/eng@2004-05-01"/>
    <FRBRdate date="2004-05-01" name="Generation"/><FRBRauthor href=""/>
    <FRBRlanguage language="eng"/></FRBRExpression>
  <FRBRManifestation><FRBRthis value="/akn/xa/act/2004/9/eng@2004-05-01/!main.xml"/>
    <FRBRuri value="/akn/xa/act/2004/9/eng@2004-05-01.xml"/>
    <FRBRdate date="2004-05-01" name="Generation"/><FRBRauthor href=""/></FRBRManifestation>
</identification></meta>"""

_NS = 'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"'


def _act(body: str) -> str:
    return f'<akomaNtoso {_NS}><act name="act">{_META}<body>{body}</body></act></akomaNtoso>'


ACT = _act("""
<section eId="section-3"><num>3</num><heading>Licences</heading>
  <subsection eId="section-3-1"><num>(1)</num>
    <intro>
      <p>A licence holder commits an offence if, without reasonable excuse, the holder</p>
    </intro>
    <paragraph eId="section-3-1-a"><num>(a)</num>
      <content><p>fails to display the licence, or</p></content></paragraph>
    <paragraph eId="section-3-1-b"><num>(b)</num>
      <content><p>refuses to produce it on request.</p></content></paragraph>
  </subsection>
  <subsection eId="section-3-2"><num>(2)</num>
    <intro><p>The holder may appeal against</p></intro>
    <paragraph eId="section-3-2-a"><num>(a)</num>
      <content><p>a refusal to renew, or</p></content></paragraph>
    <paragraph eId="section-3-2-b"><num>(b)</num>
      <content><p>a revocation,</p></content></paragraph>
    <wrapUp><p>within 28 days of the decision.</p></wrapUp>
  </subsection>
</section>
<article eId="art_4"><num>4</num>
  <intro><p>The register shall record:</p></intro>
  <paragraph eId="art_4__para_1"><num>1.</num>
    <content><p>the name of each holder;</p></content></paragraph>
  <paragraph eId="art_4__para_2"><num>2.</num>
    <content><p>the date each licence expires.</p></content></paragraph>
</article>
<article eId="art_5"><num>5</num>
  <intro><p>Subject to article 4,</p></intro>
  <content>
    <p>the registrar may refuse an application under <ref href="#art_4">article 4</ref> where:</p>
  </content>
  <point eId="art_5__point_a"><num>(a)</num>
    <content><p>the fee is unpaid; or</p></content></point>
  <point eId="art_5__point_b"><num>(b)</num>
    <content><p>the form is incomplete.</p></content></point>
</article>
<article eId="art_6"><num>6</num>
  <intro><p>Unless revoked,</p></intro>
  <content><p>a licence lasts one year.</p></content>
</article>""")

NOTICE = f"""<akomaNtoso {_NS}><doc name="notice">{_META}<mainBody>
<blockList eId="list_1">
  <listIntroduction>The following fees are payable:</listIntroduction>
  <item eId="list_1__item_a"><num>(a)</num><p>for a new licence, 40 units;</p></item>
  <item eId="list_1__item_b"><num>(b)</num><p>for a renewal, 25 units, or</p>
    <blockList eId="list_1__item_b__list_1">
      <item eId="list_1__item_b__list_1__item_i"><num>(i)</num><p>half that when late,</p></item>
    </blockList>
  </item>
  <item eId="list_1__item_c"><num>(c)</num>
    <blockList eId="list_1__item_c__list_1">
      <item eId="list_1__item_c__list_1__item_i"><num>(i)</num>
        <blockList eId="list_1__item_c__list_1__item_i__list_1">
          <item eId="list_1__item_c__list_1__item_i__list_1__item_a"><num>(A)</num>
            <p>double for a second late renewal;</p></item>
        </blockList>
      </item>
    </blockList>
  </item>
  <listWrapUp>and are due on application.</listWrapUp>
</blockList>
</mainBody></doc></akomaNtoso>"""


def _rows(akn: str) -> list[tuple[str | None, str, str, str, int]]:
    """(section eId, eId, type, text, position) in position order within each section."""
    _, sections, provisions, *_ = document_to_rows(parse_document(akn), law_id=uuid.uuid4())
    section_eid = {s.id: s.akn_eid for s in sections}
    return [
        (section_eid.get(p.section_id), p.akn_eid, p.akn_type, p.text, p.position)
        for p in sorted(provisions, key=lambda p: (str(p.section_id), p.position))
    ]


def _in(section: str | None, rows: list[tuple[str | None, str, str, str, int]]) -> list[tuple]:
    return [r[1:] for r in rows if r[0] == section]


def test_a_subsection_intro_leads_its_lettered_paragraphs() -> None:
    assert _in("section-3-1", _rows(ACT)) == [
        (
            "section-3-1__intro",
            "intro",
            "A licence holder commits an offence if, without reasonable excuse, the holder",
            0,
        ),
        ("section-3-1-a", "paragraph", "fails to display the licence, or", 1),
        ("section-3-1-b", "paragraph", "refuses to produce it on request.", 2),
    ]


def test_a_wrap_up_follows_the_paragraphs_it_closes() -> None:
    assert _in("section-3-2", _rows(ACT)) == [
        ("section-3-2__intro", "intro", "The holder may appeal against", 0),
        ("section-3-2-a", "paragraph", "a refusal to renew, or", 1),
        ("section-3-2-b", "paragraph", "a revocation,", 2),
        ("section-3-2__wrapup", "wrapUp", "within 28 days of the decision.", 3),
    ]


def test_an_article_intro_leads_its_numbered_paragraphs() -> None:
    assert _in("art_4", _rows(ACT)) == [
        ("art_4__intro", "intro", "The register shall record:", 0),
        ("art_4__para_1", "paragraph", "the name of each holder;", 1),
        ("art_4__para_2", "paragraph", "the date each licence expires.", 2),
    ]


def test_an_intro_joins_the_content_lead_in_after_it() -> None:
    """One lead-in row, as every reader of `__intro` expects, in document order."""
    assert _in("art_5", _rows(ACT)) == [
        (
            "art_5__intro",
            "paragraph",
            "Subject to article 4, the registrar may refuse an application under article 4 where:",
            0,
        ),
        ("art_5__point_a", "point", "the fee is unpaid; or", 1),
        ("art_5__point_b", "point", "the form is incomplete.", 2),
    ]
    assert _in("art_6", _rows(ACT)) == [
        ("art_6__content", "paragraph", "Unless revoked, a licence lasts one year.", 0),
    ]


def test_the_joined_row_keeps_the_content_citations() -> None:
    _, _, provisions, refs, *_ = document_to_rows(parse_document(ACT), law_id=uuid.uuid4())
    eid_of = {p.id: p.akn_eid for p in provisions}
    assert [(eid_of[r.source_provision_id], r.target_uri) for r in refs] == [
        ("art_5__intro", "#art_4"),
    ]


def test_a_block_list_outside_content_rows_its_introduction_and_items() -> None:
    assert _in(None, _rows(NOTICE)) == [
        ("list_1", "blockList", "The following fees are payable:", 0),
        ("list_1__item_a", "item", "for a new licence, 40 units;", 1),
        ("list_1__item_b", "item", "for a renewal, 25 units, or", 2),
        ("list_1__item_b__list_1__item_i", "item", "half that when late,", 3),
        (
            "list_1__item_c__list_1__item_i__list_1__item_a",
            "item",
            "double for a second late renewal;",
            4,
        ),
        ("list_1__wrapup", "wrapUp", "and are due on application.", 5),
    ]


def test_a_list_keeps_its_introduction_and_wrap_up_through_a_round_trip() -> None:
    assert _rows(to_akn(parse_document(NOTICE))) == _rows(NOTICE)


def test_the_reader_renders_a_persisted_list_item_once() -> None:
    _, _, provisions, *_ = document_to_rows(parse_document(NOTICE), law_id=uuid.uuid4())
    index = _eid_index(_parse_version_xml(NOTICE))
    persisted = frozenset(p.akn_eid for p in provisions)

    def nested(blocks: list) -> list[str]:
        return [e for b in blocks for e in (b.akn_eid, *nested(b.blocks))]

    assert [
        (p.akn_eid, e)
        for p in provisions
        for e in nested(_to_provision(p, index, persisted).blocks)
        if e in persisted and e != p.akn_eid
    ] == []


def _rebuilt_eids(akn: str) -> list[str]:
    version, sections, provisions, refs, *_ = document_to_rows(
        parse_document(akn), law_id=uuid.uuid4()
    )
    doc = rows_to_document(version, sections, provisions, refs, frbr_work_uri="/akn/xa/act/2004/9")

    def eids(els: list) -> list[str]:
        return [e for el in els for e in (el.akn_eid, *eids(el.children))]

    return eids(doc.body)


def test_the_rebuilt_document_leaves_the_new_rows_out() -> None:
    """The comparator reads the rebuilt document; a content lead-in stays in it."""
    assert _rebuilt_eids(NOTICE) == []
    assert _rebuilt_eids(ACT) == [
        "section-3",
        "section-3-1",
        "section-3-1-a",
        "section-3-1-b",
        "section-3-2",
        "section-3-2-a",
        "section-3-2-b",
        "art_4",
        "art_4__para_1",
        "art_4__para_2",
        "art_5",
        "art_5__intro",
        "art_5__point_a",
        "art_5__point_b",
        "art_6",
        "art_6__content",
    ]


SPLIT = _act("""
<article eId="art_7"><num>7</num>
  <intro><block name="lead">The <ref href="#art_4">registrar</ref> shall:</block></intro>
  <point eId="art_7__point_a"><num>(a)</num><content><p>keep the register;</p></content></point>
  <wrapUp eId="art_7__closing"><block name="closing">and publish it at:</block>
    <blockList eId="art_7__wrapup__list_1">
      <item eId="art_7__wrapup__list_1__item_1"><p>the registry office;</p></item>
    </blockList>
    <table eId="art_7__table_1"><tr><td><p>Fee</p></td><td><p>10</p></td></tr></table>
  </wrapUp>
</article>
<article eId="art_8"><num>8</num>
  <point eId="art_8__point_a"><num>(a)</num><content><p>a fee is due;</p></content></point>
  <wrapUp eId="art_8__wrapup"><block name="closing">unless waived.</block></wrapUp>
</article>
<article eId="art_9"><num>9</num><content><p>A licence lasts one year.</p></content>
  <wrapUp><p>Renewal is free.</p></wrapUp>
</article>""")


def _rendered(akn: str) -> dict[str, list]:
    _, _, provisions, *_ = document_to_rows(parse_document(akn), law_id=uuid.uuid4())
    index = _eid_index(_parse_version_xml(akn))
    persisted = frozenset(p.akn_eid for p in provisions)
    return {p.akn_eid: _to_provision(p, index, persisted).blocks for p in provisions}


def _words(nodes: list) -> str:
    return "".join(n.text for n in nodes if isinstance(n, InlineText)).strip()


def test_a_split_off_row_renders_its_own_subtree() -> None:
    rendered = _rendered(SPLIT)
    (lead,) = rendered["art_7__intro"]
    assert any(isinstance(n, InlineRef) and n.text == "registrar" for n in lead.intro)
    # A named block keeps its words beside the list and table that follow it.
    (closing,) = rendered["art_7__wrapup"]
    assert (_words(closing.intro), [b.akn_type for b in closing.blocks]) == (
        "and publish it at:",
        ["item", "table"],
    )
    # A wrapper may carry its row's name as its own eId.
    assert _words(rendered["art_8__wrapup"][0].intro) == "unless waived."
    # Content beside a lead-in renders with it, as the row's text joins them.
    assert [_words(b.intro) for b in _rendered(ACT)["art_5__intro"]] == [
        "Subject to article 4,",
        "the registrar may refuse an application under  where:",
    ]


def test_closing_words_render_once() -> None:
    rendered = _rendered(SPLIT)
    assert [(_words(b.intro), b.wrap_up) for b in rendered["art_9__content"]] == [
        ("A licence lasts one year.", [])
    ]
    assert _words(rendered["art_9__wrapup"][0].intro) == "Renewal is free."


def test_an_anchor_in_closing_words_lands_on_the_wrap_up_row() -> None:
    _, sections, provisions, *_ = document_to_rows(parse_document(SPLIT), law_id=uuid.uuid4())
    index = element_index(_parse_version_xml(SPLIT))
    rows = {p.akn_eid for p in provisions}
    named = {s.akn_eid for s in sections}
    assert nearest_stored_row(index, "art_7__table_1", rows, named) == (
        "art_7__wrapup",
        "enclosing",
    )
    assert nearest_stored_row(index, "art_7__closing", rows, named) == (
        "art_7__wrapup",
        "container",
    )
