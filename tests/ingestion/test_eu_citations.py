"""EU act citations read out of prose into work URIs."""

from __future__ import annotations

import pytest
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.akn.references import DERIVED_REF_CLASS
from codify.pipeline.enrich.eu_citations import CITATION, EU_CITATION_CLASS, work_uri
from codify.pipeline.enrich.inline_markup import emit_inline_markup


def _uris(text: str, eu_text: bool = True) -> list[str | None]:
    return [work_uri(m, text[: m.start()], eu_text=eu_text) for m in CITATION.finditer(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Regulation (EU) 2016/679", ["/akn/eu/act/reg/2016/679"]),
        ("Directive 95/46/EC", ["/akn/eu/act/dir/1995/46"]),
        ("Council Regulation (EEC) No 2913/92", ["/akn/eu/act/reg/1992/2913"]),
        ("Regulation (EEC) 1408/71", ["/akn/eu/act/reg/1971/1408"]),
        ("Regulation (EC) 58/2003", ["/akn/eu/act/reg/2003/58"]),
        ("Decision No 1082/2013/EU", ["/akn/eu/act/dec/2013/1082"]),
        ("Regulations (EU) No 2018/1883", ["/akn/eu/act/reg/2018/1883"]),
        ("Regulation (EU, Euratom) 2018/1046", ["/akn/eu/act/reg/2018/1046"]),
        ("Commission Implementing Decision 2019/1092", ["/akn/eu/act/dec/2019/1092"]),
        (
            "Regulations (EU) No 1305/2013, (EU) No 1306/2013, and (EU) No 1307/2013",
            [
                "/akn/eu/act/reg/2013/1305",
                "/akn/eu/act/reg/2013/1306",
                "/akn/eu/act/reg/2013/1307",
            ],
        ),
        (
            "Directives 79/117/EEC and 91/414/EEC",
            ["/akn/eu/act/dir/1979/117", "/akn/eu/act/dir/1991/414"],
        ),
        # Formex runs a footnote straight on.
        (
            "Directive 2003/71/ECDirective 2003/71/EC of",
            ["/akn/eu/act/dir/2003/71", "/akn/eu/act/dir/2003/71"],
        ),
        (
            "CELEX 32016R0679 and 31995L0046",
            ["/akn/eu/act/reg/2016/679", "/akn/eu/act/dir/1995/46"],
        ),
    ],
)
def test_a_citation_reads_to_its_work_uri(text: str, expected: list[str]) -> None:
    assert _uris(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Decision No 1/2019 of the Joint Committee",
        "regulation 5/2 of these Regulations",
        "on 12/05/2016",
        "CELEX 32016R0000",
    ],
)
def test_a_number_no_eu_head_governs_is_not_cited(text: str) -> None:
    assert not any(_uris(text))


def test_outside_eu_text_a_bare_number_needs_a_marker() -> None:
    assert _uris("Regulation 2019/1092 of the Council of Ministers", eu_text=False) == [None]


def test_a_run_of_digit_pairs_does_not_backtrack() -> None:
    assert _uris("Directive " + "11/11" * 40) == []
    assert _uris("Directive " + "11/11, " * 200 + "95/46/EC")[-1] == "/akn/eu/act/dir/1995/46"


def _act(body: str, preamble: str = "") -> str:
    return f"""<akomaNtoso xmlns="{AKN_NS}"><act>
<meta><identification source="#codify"><FRBRWork><FRBRthis value="/akn/eu/act/reg/2016/679/main"/>
<FRBRuri value="/akn/eu/act/reg/2016/679"/></FRBRWork></identification></meta>
<preamble>{preamble}</preamble><body>{body}</body></act></akomaNtoso>"""


_MINTED = f"{DERIVED_REF_CLASS} {EU_CITATION_CLASS}"


def _refs(xml: str) -> list[etree._Element]:
    return etree.fromstring(xml.encode()).findall(".//akn:ref", NS)


async def test_recitals_and_body_are_marked_and_the_act_itself_is_not() -> None:
    xml = _act(
        '<article eId="art_1"><paragraph eId="art_1__para_1"><content><p>'
        "This Regulation (EU) 2016/679 replaces Directive 95/46/EC."
        "</p></content></paragraph></article>",
        '<recitals><recital eId="recs__rec_1"><p>Under Regulation (EC) No 45/2001.</p>'
        "</recital></recitals>",
    )
    refs = _refs(await emit_inline_markup(xml, "eu", "regulation"))
    assert [(r.get("href"), r.get("class")) for r in refs] == [
        ("/akn/eu/act/reg/2001/45", _MINTED),
        ("/akn/eu/act/dir/1995/46", _MINTED),
    ]


async def test_a_list_split_by_a_footnote_keeps_its_head() -> None:
    xml = _act(
        '<article eId="art_1"><paragraph eId="art_1__para_1"><content><p>Directives 2014/23/EU'
        '<authorialNote eId="fn_1"><p>OJ L 94, 28.3.2014.</p></authorialNote>, 2014/24/EU and '
        "2014/25/EU apply.</p></content></paragraph></article>"
    )
    hrefs = [r.get("href") for r in _refs(await emit_inline_markup(xml, "eu", "regulation"))]
    assert hrefs == [
        "/akn/eu/act/dir/2014/23",
        "/akn/eu/act/dir/2014/24",
        "/akn/eu/act/dir/2014/25",
    ]


async def test_a_citation_in_an_amending_paragraph_stays_a_citation_on_every_run() -> None:
    """The amendment pass reads a footnote's "amending Directive X" as an amendment by this act;
    a refresh re-enriches the stored, already-marked document."""
    xml = _act(
        '<article eId="art_1"><paragraph eId="art_1__para_1"><content><p>'
        "Directive 2001/83/EC is amended as follows, amending Directive 2003/35/EC.</p>"
        "</content></paragraph></article>"
    )
    once = await emit_inline_markup(xml, "eu", "regulation")
    twice = await emit_inline_markup(once, "eu", "regulation")
    assert [r.get("class") for r in _refs(twice)] == [_MINTED, _MINTED]


async def test_a_citation_in_inline_formatting_is_marked() -> None:
    xml = _act(
        '<article eId="art_1"><paragraph eId="art_1__para_1"><content><p>'
        "Under <i>Directive 95/46/EC</i> and Regulation <i>(EU) 2016/680</i>.</p>"
        "</content></paragraph></article>"
    )
    hrefs = [r.get("href") for r in _refs(await emit_inline_markup(xml, "eu", "regulation"))]
    assert hrefs == ["/akn/eu/act/dir/1995/46", "/akn/eu/act/reg/2016/680"]


async def test_an_article_of_another_act_is_not_a_self_link() -> None:
    xml = _act(
        '<article eId="art_5"><paragraph eId="art_5__para_1"><content><p>'
        "As in Article 5 of Regulation (EU) No 1303/2013.</p></content></paragraph></article>"
    )
    hrefs = [r.get("href") for r in _refs(await emit_inline_markup(xml, "eu", "regulation"))]
    assert hrefs == ["/akn/eu/act/reg/2013/1303"]
