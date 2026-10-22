"""Riigi Teataja annexes published as files: reading their bytes and filling their text."""

from __future__ import annotations

import base64

from lxml import etree

from codify.akn._schema import AKN_NS, validate_akn
from codify.akn.io import parse_akn
from codify.pipeline.formats.riigi_teataja import riigi_teataja_to_akn
from codify.pipeline.formats.riigi_teataja_annexes import annex_files, fill_annex_text

_PDF = b"%PDF-1.4 invented annex bytes"
_GIF = b"GIF89a invented"


def _act(*, meta_ids: str, annexes: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<oigusakt xmlns="Juurakt">
  <metaandmed>
    <valjaandja>Kultuuriminister</valjaandja>
    <dokumentLiik>määrus</dokumentLiik>
    <tekstiliik>terviktekst</tekstiliik>
    <vastuvoetud><aktikuupaev>2021-03-04</aktikuupaev><aktiNr>7</aktiNr></vastuvoetud>
    {meta_ids}
    <kehtivus><kehtivuseAlgus>2024-05-06</kehtivuseAlgus></kehtivus>
  </metaandmed>
  <aktinimi><nimi><pealkiri>Kaunite kunstide toetuse kord</pealkiri></nimi></aktinimi>
  <sisu>
    <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
      <loige><sisuTekst><tavatekst>Taotlus esitatakse lisa 1 vormil.</tavatekst>
      </sisuTekst></loige>
    </paragrahv>
  </sisu>
  {annexes}
</oigusakt>""".encode()


def _annex(name: str, data: bytes | None, title: str) -> str:
    body = base64.encodebytes(data).decode() if data is not None else ""
    return f"""<lisaViide><lisaViit><sisuTekst>
      <fail failKuvamine="viitena" failNimi="{name}" failVorming="pdf">{body}</fail>
    </sisuTekst></lisaViit><lisaPealkiri><lisaNimi>{title}</lisaNimi></lisaPealkiri></lisaViide>"""


_REPEALED = (
    "<lisaViide><lisaViit><sisuTekst><tavatekst>Lisa 2</tavatekst></sisuTekst></lisaViit>"
    "<lisaPealkiri><lisaNimi>Vorm</lisaNimi></lisaPealkiri></lisaViide>"
)
_CONSOLIDATED_IDS = (
    "<avaldamismarge><avaldamineKuupaev>2021-03-08</avaldamineKuupaev>"
    "<aktViide>108032021002</aktViide></avaldamismarge>"
    "<globaalID>106052024011</globaalID>"
)
_SOURCE = _act(
    meta_ids=_CONSOLIDATED_IDS,
    annexes=_annex("KUM_m7_lisa1.pdf", _PDF, "Taotluse vorm")
    + _REPEALED
    + _annex("KUM_m7_lisa3.gif", _GIF, "Joonis")
    + _annex("KUM_m7_lisa4.pdf", None, "Tühi")
    + _annex("KUM_m7_lisa5.pdf", None, "Katki").replace("</fail>", "QUJ</fail>")
    + _annex("KUM_m7_lisa6.pdf", None, "Võõras").replace("</fail>", "QU-JD</fail>"),
)


def _attachment_eids(akn: str) -> list[str]:
    root = etree.fromstring(akn.encode())
    return [a.get("eId", "") for a in root.iter(f"{{{AKN_NS}}}attachment")]


def test_annex_files_name_the_transformers_attachments_and_carry_their_bytes() -> None:
    akn, _ = riigi_teataja_to_akn(_SOURCE)
    files = annex_files(_SOURCE)
    assert [f.eid for f in files] == _attachment_eids(akn) == [f"att_{i}" for i in range(1, 7)]
    assert [(f.name, f.data) for f in files] == [
        ("KUM_m7_lisa1.pdf", _PDF),
        (None, None),
        ("KUM_m7_lisa3.gif", _GIF),
        ("KUM_m7_lisa4.pdf", None),
        ("KUM_m7_lisa5.pdf", None),  # bad padding
        ("KUM_m7_lisa6.pdf", None),  # a character outside the alphabet
    ]


def test_annex_link_names_the_texts_own_id_not_the_first_publication() -> None:
    akn, _ = riigi_teataja_to_akn(_SOURCE)
    assert 'href="https://www.riigiteataja.ee/aktilisa/106052024011/KUM_m7_lisa1.pdf"' in akn
    assert "108032021002" not in akn


def test_annex_link_takes_the_digits_of_a_url_form_act_reference() -> None:
    old = _act(
        meta_ids=(
            "<avaldamismarge><avaldamineKuupaev>2009-04-28</avaldamineKuupaev>"
            "<aktViide>http://www.riigiteataja.ee/akt/13175900</aktViide></avaldamismarge>"
        ),
        annexes=_annex("13175903.gif", _GIF, ""),
    )
    akn, _ = riigi_teataja_to_akn(old)
    assert 'href="https://www.riigiteataja.ee/aktilisa/13175900/13175903.gif"' in akn


def test_fill_writes_one_container_per_page_after_the_link() -> None:
    akn, _ = riigi_teataja_to_akn(_SOURCE)
    pages = {
        "att_1": ["Taotleja nimi\nIsikukood", "", "| Kulu | Summa |\n| --- | --- |\n| Rent | 10 |"],
        "att_9": ["names no attachment"],
    }
    filled, count = fill_annex_text(akn, pages)

    assert count == 1
    validate_akn(filled)
    root = etree.fromstring(filled.encode())
    a = f"{{{AKN_NS}}}"
    main = root.find(f".//{a}attachment[@eId='att_1']/{a}doc/{a}mainBody")
    assert main is not None
    assert [etree.QName(c).localname for c in main] == ["p", "hcontainer", "hcontainer"]
    assert [c.get("eId") for c in main][1:] == ["att_1__hcontainer_1", "att_1__hcontainer_3"]
    assert main.find(f"{{{AKN_NS}}}hcontainer//{{{AKN_NS}}}table") is not None

    # The reader's rows come from the parsed document: the annex now has content.
    doc = parse_akn(filled)
    att = next(a for a in doc.attachments if a.akn_eid == "att_1")
    texts = [child.text for child in att.children]
    assert texts[0] == "Taotleja nimi\nIsikukood"


def test_fill_leaves_a_transcribed_attachment_alone_and_drops_illegal_characters() -> None:
    akn, _ = riigi_teataja_to_akn(_SOURCE)
    once, first = fill_annex_text(akn, {"att_3": ["Joonis\x0c 1\x00"]})
    twice, second = fill_annex_text(once, {"att_3": ["something else"]})
    assert (first, second) == (1, 0)
    assert twice == once
    assert "Joonis 1" in once


def test_fill_skips_an_attachment_whose_pages_are_all_blank() -> None:
    akn, _ = riigi_teataja_to_akn(_SOURCE)
    filled, count = fill_annex_text(akn, {"att_1": ["", "  \n"]})
    assert count == 0
    assert "hcontainer" not in filled
