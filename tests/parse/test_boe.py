"""BOE Diario Oficial XML to AKN. The fixtures keep the BOE's own markup (element
names, paragraph classes, metadata fields) around invented text and numbers."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from codify.akn._schema import AKN_NS, validate_akn
from codify.pipeline import ingest_document
from codify.pipeline.enrich.validator import validate_akn as run_validator
from codify.pipeline.events import Complete, MetadataExtracted
from codify.pipeline.formats.boe import (
    BoeTextMissing,
    boe_to_akn,
    cardinal_value,
    is_boe,
    ordinal_value,
)

NS = {"a": AKN_NS}


def _item(
    texto: str,
    *,
    rango: str = "Ley",
    eli: str = "l",
    numero: str = "91/2019",
    titulo: str = "Ley 91/2019, de 3 de abril, de ensayo.",
) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<documento fecha_actualizacion="20190404000000">
  <metadatos>
    <identificador>BOE-A-2019-90001</identificador>
    <departamento codigo="7723">Jefatura del Estado</departamento>
    <rango codigo="1300">{rango}</rango>
    <fecha_disposicion>20190403</fecha_disposicion>
    <numero_oficial>{numero}</numero_oficial>
    <titulo>{titulo}</titulo>
    <diario codigo="BOE">Boletín Oficial del Estado</diario>
    <fecha_publicacion>20190404</fecha_publicacion>
    <diario_numero>81</diario_numero>
    <pagina_inicial>9001</pagina_inicial>
    <url_pdf>https://www.boe.es/boe/dias/2019/04/04/pdfs/BOE-A-2019-90001.pdf</url_pdf>
    <url_eli>https://www.boe.es/eli/es/{eli}/2019/04/03/{numero.split("/")[0]}</url_eli>
  </metadatos>
  <analisis><referencias><anteriores/></referencias></analisis>
  <texto>{texto}</texto>
</documento>""".encode()


_PROMULGATION = """
    <p class="centro_redonda">FELIPE VI</p>
    <p class="centro_redonda">REY DE ESPAÑA</p>
    <p class="parrafo">A todos los que la presente vieren y entendieren.</p>
    <p class="parrafo">Sabed: Que las Cortes Generales han aprobado y Yo sanciono esta ley.</p>"""

_CLOSING = """
    <p class="parrafo_2">Por tanto,</p>
    <p class="parrafo">Mando a todos los españoles que guarden esta ley.</p>
    <p class="parrafo_2">Madrid, 3 de abril de 2019.</p>
    <p class="firma_rey">FELIPE R.</p>
    <p class="firma_ministro">El Presidente del Gobierno,</p>"""

# Ordinal article heads in body classes, and a plural group of `Primera.-` finals.
ORDINAL = _item(
    _PROMULGATION
    + """
    <p class="parrafo">Artículo primero.</p>
    <p class="parrafo">El registro de ensayo queda abierto a toda persona.</p>
    <p class="parrafo">Artículo segundo.</p>
    <p class="parrafo">Artículo 9 de la Ley 7/2001. Se le añade lo siguiente:</p>
    <p class="parrafo">«3. Primera. El texto citado no es una disposición de esta ley.»</p>
    <p class="parrafo">DISPOSICIONES FINALES</p>
    <p class="parrafo">Primera.- La presente ley entra en vigor al día siguiente.</p>
    <p class="parrafo">Segunda.- Se autoriza al Gobierno a desarrollarla.</p>
    <p class="parrafo_2">Por tanto,</p>
    <p class="parrafo">Mando a todos los españoles que guarden esta ley.</p>
    <p class="parrafo">Palacio de la Zarzuela, Madrid, 3 de abril de 2019.- FELIPE R.</p>"""
)

# An organic law amending another by quoting a chapter: the quoted chapter,
# section and articles stay text inside the amending article.
ORGANIC = _item(
    _PROMULGATION
    + """
    <p class="articulo">Artículo único. Modificación de la Ley Orgánica 4/2001.</p>
    <p class="parrafo">Se añade un capítulo II bis, que queda redactado como sigue:</p>
    <p class="capitulo_num">«CAPÍTULO II BIS</p>
    <p class="capitulo_tit">De los registros de ensayo</p>
    <p class="seccion">Sección primera. Disposiciones comunes</p>
    <p class="sangrado_articulo">Artículo 20 bis. Objeto.</p>
    <p class="sangrado_2">1. El registro se lleva por medios electrónicos.</p>
    <p class="sangrado_2">2. Su consulta es libre.»</p>
    <p class="capitulo">DISPOSICIÓN FINAL</p>
    <p class="parrafo">La presente ley orgánica entra en vigor el día de su publicación.</p>"""
    + _CLOSING,
    rango="Ley Orgánica",
    eli="lo",
    numero="9/2019",
    titulo="Ley Orgánica 9/2019, de 3 de abril, de ensayo.",
)

# A preamble, a title and chapter, apartados with letters, and the four dispositions.
PREAMBLE = _item(
    _PROMULGATION
    + """
    <p class="centro_redonda">PREÁMBULO</p>
    <p class="centro_redonda">I</p>
    <p class="parrafo">Esta ley ordena el registro de ensayo.</p>
    <p class="titulo_num">TÍTULO PRELIMINAR</p>
    <p class="titulo_tit">Disposiciones generales</p>
    <p class="articulo">Artículo 1. Objeto.</p>
    <p class="parrafo">Esta ley regula el registro de ensayo.</p>
    <p class="titulo_num">TÍTULO I</p>
    <p class="titulo_tit">Del registro</p>
    <p class="capitulo_num">CAPÍTULO PRIMERO</p>
    <p class="capitulo_tit">Inscripción</p>
    <p class="articulo">Artículo 2. Requisitos.</p>
    <p class="parrafo">1. La inscripción exige:</p>
    <p class="parrafo_2">a) Una solicitud.</p>
    <p class="parrafo">b) Un documento de identidad.</p>
    <p class="parrafo_2">2. La inscripción es gratuita.</p>
    <p class="articulo">Artículo 2 bis. Plazo.</p>
    <p class="parrafo">El plazo es de un mes.</p>
    <p class="parrafo">5. Este número suelto no abre un apartado.</p>
    <p class="articulo">Disposición adicional primera. Medios.</p>
    <p class="parrafo">Se dotarán los medios necesarios.</p>
    <p class="articulo">Disposición adicional decimoctava. Informe.</p>
    <p class="parrafo">El Gobierno informará cada año.</p>
    <p class="articulo">Disposición transitoria única. Solicitudes en curso.</p>
    <p class="parrafo">Las solicitudes en curso se rigen por la norma anterior.</p>
    <p class="articulo">Disposición derogatoria única. Derogación normativa.</p>
    <p class="parrafo">Quedan derogadas cuantas normas se opongan a esta ley.</p>
    <p class="articulo">Disposición final primera. Título competencial.</p>
    <p class="parrafo">Esta ley se dicta al amparo del artículo 149.1.1.ª de la Constitución.</p>
    <p class="articulo">Disposición final segunda. Entrada en vigor.</p>
    <p class="parrafo">Esta ley entra en vigor al mes de su publicación.</p>"""
    + _CLOSING
)

# A royal decree-law: a motivated preamble ending in DISPONGO, and an annex with a table.
DECREE_LAW = _item(
    """
    <p class="parrafo">La situación exige medidas urgentes de ensayo.</p>
    <p class="parrafo">En su virtud, previa deliberación del Consejo de Ministros,</p>
    <p class="centro_redonda">DISPONGO:</p>
    <p class="articulo">Artículo 1. Ayudas.</p>
    <p class="parrafo">Se conceden las ayudas que figuran en el anexo.</p>
    <p class="articulo">Disposición final única. Entrada en vigor.</p>
    <p class="parrafo">Este real decreto-ley entra en vigor el día de su publicación.</p>
    <p class="parrafo_2">Dado en Madrid, el 3 de abril de 2019.</p>
    <p class="firma_rey">FELIPE R.</p>
    <p class="firma_ministro">El Presidente del Gobierno,</p>
    <p class="anexo_num">ANEXO I</p>
    <p class="anexo_tit">Cuantías</p>
    <table class="tabla"><tr><th>Concepto</th><th>Euros</th></tr>
      <tr><td>Ayuda</td><td>100</td></tr></table>
    <p class="anexo_num">ANEXO II</p>
    <p class="parrafo">Modelo de solicitud.</p>""",
    rango="Real Decreto-ley",
    eli="rdl",
    numero="8/2019",
    titulo="Real Decreto-ley 8/2019, de 3 de abril, de ensayo.",
)


# BOE editorial cues: `[encabezado]` and `[precepto]` are dropped, `[ignorar]` marks quoted text.
MARKERS = _item(
    """
    <p class="articulo">Artículo 1.</p>
    <p class="parrafo">Texto de ensayo.</p>
    <p class="capitulo">[encabezado]DISPOSICIONES ADICIONALES</p>
    <p class="articulo">[precepto]Primera.</p>
    <p class="parrafo">Primera.– Esta línea fuera de orden es contenido.</p>
    <p class="articulo">[precepto]Segunda.</p>
    <p class="parrafo">[ignorar]CAPÍTULO IX</p>
    <p class="articulo">Disposición transitoria primera.</p>
    <p class="parrafo">Tercera.- Esta línea también es contenido.</p>"""
)

# A legislative decree: the text it approves follows the signature.
CONSOLIDATING = _item(
    """
    <p class="centro_redonda">DISPONGO:</p>
    <p class="articulo">Artículo único. Aprobación.</p>
    <p class="parrafo">Se aprueba el texto refundido que se inserta a continuación.</p>
    <p class="parrafo_2">Dado en Madrid, el 3 de abril de 2019.</p>
    <p class="firma_rey">FELIPE R.</p>
    <p class="libro">[encabezado]TEXTO REFUNDIDO DE LA LEY DE ENSAYO</p>
    <p class="titulo_num">TÍTULO I</p>
    <p class="titulo_tit">Disposiciones generales</p>
    <p class="articulo">Artículo 1. Objeto.</p>
    <p class="parrafo">Este texto refunde las normas de ensayo.</p>""",
    rango="Real Decreto Legislativo",
    eli="rdlg",
    numero="2/2019",
    titulo="Real Decreto Legislativo 2/2019, de 3 de abril, de ensayo.",
)


def _convert(source: bytes) -> tuple[etree._Element, dict[str, object]]:
    xml, meta = boe_to_akn(source)
    validate_akn(xml)
    return etree.fromstring(xml.encode()), meta


def _eids(root: etree._Element, tag: str) -> list[str]:
    return [el.get("eId", "") for el in root.iter(f"{{{AKN_NS}}}{tag}")]


def _text(el: etree._Element | None) -> str:
    assert el is not None
    return " ".join("".join(el.itertext()).split())


def test_ordinal_articles_and_a_dashed_disposition_group() -> None:
    root, meta = _convert(ORDINAL)
    assert _eids(root, "article") == ["art_1", "art_2"]
    assert "Artículo 9 de la Ley 7/2001" in _text(root.find(".//a:article[@eId='art_2']", NS))
    assert [_text(n) for n in root.findall(".//a:article/a:num", NS)] == ["1", "2"]
    finals = root.findall(".//a:body/a:hcontainer", NS)
    assert [f.get("eId") for f in finals] == ["hcontainer_df-1", "hcontainer_df-2"]
    assert [f.get("name") for f in finals] == ["disposicion-final"] * 2
    assert _text(finals[0].find("a:num", NS)) == "Disposición final primera"
    assert (
        _text(finals[0].find("a:content", NS)) == "La presente ley entra en vigor al día siguiente."
    )
    # The quoted "Primera." stays inside article two.
    assert "Primera. El texto citado" in _text(root.find(".//a:article[@eId='art_2']", NS))
    assert _text(root.find(".//a:conclusions", NS)).startswith("Por tanto,")
    assert meta["frbr_work_uri"] == "/akn/es/act/2019/91"


def test_organic_law_keeps_a_quoted_chapter_inside_its_article() -> None:
    root, meta = _convert(ORGANIC)
    assert meta["frbr_work_uri"] == "/akn/es/act/lo/2019/9"
    assert meta["doctype"] == "lo"
    assert root.find(".//a:FRBRWork/a:FRBRuri", NS).get("value") == "/akn/es/act/lo/2019/9"
    assert _eids(root, "article") == ["art_unico"]
    assert _eids(root, "chapter") == [] and _eids(root, "section") == []
    assert _eids(root, "paragraph") == []  # the quoted article's apartados are not this law's
    article = _text(root.find(".//a:article", NS))
    assert "«CAPÍTULO II BIS" in article and "Artículo 20 bis. Objeto." in article
    final = root.find(".//a:hcontainer[@name='disposicion-final']", NS)
    assert final is not None and final.get("eId") == "hcontainer_df-unica"
    assert _text(final.find("a:num", NS)) == "Disposición final"
    assert _text(final.find("a:content", NS)).startswith("La presente ley orgánica")


def test_preamble_hierarchy_subdivisions_and_dispositions() -> None:
    root, _ = _convert(PREAMBLE)
    formula = root.find(".//a:preamble/a:formula[@name='enactingFormula']", NS)
    assert _text(formula).startswith("A todos los que la presente vieren")
    assert "Esta ley ordena el registro" in _text(root.find(".//a:preamble", NS))
    assert _eids(root, "title") == ["title_preliminar", "title_I"]
    assert _eids(root, "chapter") == ["title_I__chp_I"]
    assert _text(root.find(".//a:chapter/a:heading", NS)) == "Inscripción"
    assert _eids(root, "article") == [
        "title_preliminar__art_1",
        "title_I__chp_I__art_2",
        "title_I__chp_I__art_2bis",
    ]
    assert _eids(root, "paragraph") == [
        "title_I__chp_I__art_2__para_1",
        "title_I__chp_I__art_2__para_2",
    ]
    assert root.find(".//a:article[@eId='title_I__chp_I__art_2bis']/a:content", NS) is not None
    assert _eids(root, "point") == [
        "title_I__chp_I__art_2__para_1__point_a",
        "title_I__chp_I__art_2__para_1__point_b",
    ]
    point = root.find(".//a:point", NS)
    assert _text(point.find("a:num", NS)) == "a)"
    assert _text(point.find("a:content", NS)) == "Una solicitud."
    dispositions = [
        (h.get("eId"), _text(h.find("a:num", NS)), _text(h.find("a:heading", NS)))
        for h in root.findall(".//a:body/a:hcontainer", NS)
    ]
    assert dispositions == [
        ("hcontainer_da-1", "Disposición adicional primera", "Medios"),
        ("hcontainer_da-18", "Disposición adicional decimoctava", "Informe"),
        ("hcontainer_dt-unica", "Disposición transitoria única", "Solicitudes en curso"),
        ("hcontainer_dd-unica", "Disposición derogatoria única", "Derogación normativa"),
        ("hcontainer_df-1", "Disposición final primera", "Título competencial"),
        ("hcontainer_df-2", "Disposición final segunda", "Entrada en vigor"),
    ]
    assert _text(root.find(".//a:conclusions", NS)).endswith("El Presidente del Gobierno,")
    assert run_validator(etree.tostring(root, encoding="unicode"), provenance="native") == []


def test_decree_law_preamble_annexes_and_table() -> None:
    root, meta = _convert(DECREE_LAW)
    assert meta["frbr_work_uri"] == "/akn/es/act/rdl/2019/8"
    assert meta["gazette"] == {
        "name": "Boletín Oficial del Estado",
        "date": "2019-04-04",
        "number": "81",
        "year": 2019,
        "page": "9001",
    }
    formula = root.find(".//a:preamble/a:formula", NS)
    assert _text(formula).startswith("En su virtud") and _text(formula).endswith("DISPONGO:")
    assert _eids(root, "article") == ["art_1"]
    assert _eids(root, "attachment") == ["att_1", "att_2"]
    first, second = root.findall(".//a:attachment", NS)
    assert [_text(p) for p in first.findall(".//a:preface//a:p", NS)] == ["Anexo I", "Cuantías"]
    assert [_text(c) for c in first.findall(".//a:mainBody/a:table//a:td", NS)] == ["Ayuda", "100"]
    assert _text(second.find(".//a:mainBody", NS)) == "Modelo de solicitud."
    this = first.find(".//a:FRBRWork/a:FRBRthis", NS).get("value")
    assert this == "/akn/es/act/rdl/2019/8/!att_1"
    # The signature closes the body; the annexes are not conclusions.
    assert "Cuantías" not in _text(root.find(".//a:conclusions", NS))


def test_editorial_cues_and_out_of_order_ordinals() -> None:
    root, _ = _convert(MARKERS)
    assert _eids(root, "chapter") == []
    additional = root.findall(".//a:hcontainer[@name='disposicion-adicional']", NS)
    assert [h.get("eId") for h in additional] == ["hcontainer_da-1", "hcontainer_da-2"]
    assert _text(additional[0].find("a:content", NS)).startswith("Primera.– Esta línea")
    assert _text(additional[1].find("a:content", NS)) == "CAPÍTULO IX"
    assert "[" not in _text(root.find(".//a:body", NS))
    transitional = root.find(".//a:hcontainer[@name='disposicion-transitoria']", NS)
    assert _text(transitional.find("a:content", NS)).startswith("Tercera.- Esta línea")


def test_the_text_a_legislative_decree_approves_is_an_attachment() -> None:
    root, meta = _convert(CONSOLIDATING)
    assert meta["frbr_work_uri"] == "/akn/es/act/rdlg/2019/2"
    assert _eids(root, "article") == ["art_unico", "att_1__title_I__art_1"]
    attachment = root.find(".//a:attachment", NS)
    assert _text(attachment.find(".//a:preface", NS)) == "TEXTO REFUNDIDO DE LA LEY DE ENSAYO"
    assert _text(root.find(".//a:conclusions", NS)).endswith("FELIPE R.")


def test_an_item_without_text_is_refused() -> None:
    with pytest.raises(BoeTextMissing):
        boe_to_akn(_item(""))


def test_detects_boe_items_only() -> None:
    assert is_boe(ORDINAL)
    assert is_boe(ORDINAL.decode())
    assert not is_boe(b"<!DOCTYPE html><html><body>Error 404</body></html>")
    assert not is_boe(b"<oigusakt xmlns='Juurakt'/>")
    assert not is_boe(ORDINAL.replace(b"BOE-A-", b"DOGC-A-"))
    assert not is_boe("not/a/path.xml")


async def test_dispatch_routes_a_boe_item(tmp_path: Path) -> None:
    src = tmp_path / "item.akn"
    src.write_bytes(PREAMBLE)
    events = [ev async for ev in ingest_document(src, "es")]
    meta = next(ev for ev in events if isinstance(ev, MetadataExtracted)).metadata
    assert meta["language"] == "spa" and meta["source_id"] == "BOE-A-2019-90001"
    complete = next(ev for ev in events if isinstance(ev, Complete))
    assert complete.document.frbr_expression_uri == "/akn/es/act/2019/91/spa@2019-04-03"


@pytest.mark.parametrize(
    ("word", "value"),
    [
        ("primero", 1),
        ("Primera", 1),
        ("tercer", 3),
        ("décima", 10),
        ("undécimo", 11),
        ("decimotercera", 13),
        ("decimoctava", 18),
        ("vigésima primera", 21),
        ("vigesimoctavo", 28),
        ("centésimo", 100),
        ("primerísima", None),
    ],
)
def test_ordinals(word: str, value: int | None) -> None:
    assert ordinal_value(word) == value


@pytest.mark.parametrize(
    ("word", "value"),
    [("Uno", 1), ("quince", 15), ("veintitrés", 23), ("treinta y dos", 32), ("veintidiez", None)],
)
def test_cardinals(word: str, value: int | None) -> None:
    assert cardinal_value(word) == value
