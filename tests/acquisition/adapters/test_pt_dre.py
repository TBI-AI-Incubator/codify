"""PT, DiarioAcquirer unit tests via httpx.MockTransport, on invented acts."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from lxml import etree

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DocumentRef
from codify.acquisition.adapters.pt.dre import (
    DATA,
    DETAIL,
    FRAG,
    DiarioAcquirer,
    DrItemMissing,
    SiteChanged,
    api_version,
    number_and_year,
)
from codify.acquisition.adapters.pt.dre_akn import (
    ROMAN,
    UnmappedFragment,
    article_body,
    from_printed,
    to_akn,
    to_bluebell,
)
from codify.acquisition.base import MissingUserAgent
from codify.acquisition.politeness import PoliteTransport
from codify.jurisdictions import SourceAdapter
from codify.pipeline.events import Complete, Failed
from codify.pipeline.formats import akn_native
from codify.storage.repository import _extract_long_title, short_title_from_akn

NS = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
SITE = "https://diariodarepublica.pt"


@pytest.fixture(autouse=True)
def _pt_config(tmp_path_factory: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An invented pt config, from the fictional xa one, for the FRBR patterns."""
    import codify.jurisdictions as jurisdictions

    root = tmp_path_factory.mktemp("jurisdictions")
    config = json.loads((jurisdictions.JURISDICTIONS_DIR / "xa" / "config.json").read_text())
    config |= {"code": "pt", "languages": ["por"], "authoritative_language": "por"}
    config["frbr"] = {
        **config.get("frbr", {}),
        "country_code": "pt",
        "uri_patterns": {
            "lei": "/akn/pt/act/{year}/{number}",
            "dl": "/akn/pt/act/dl/{year}/{number}",
            "portaria": "/akn/pt/act/portaria/{year}/{number}",
        },
    }
    (root / "pt").mkdir()
    (root / "pt" / "config.json").write_text(json.dumps(config))
    clear = getattr(jurisdictions.try_load_config, "cache_clear", lambda: None)
    monkeypatch.setattr(jurisdictions, "JURISDICTIONS_DIR", root)
    clear()
    yield
    clear()


def _frag(fid: str, parent: str, label: str, heading: str = "", text: str = "", state: int = 6):
    return {
        "Nivel": 0,
        "ConsolidacaoFragmento": {"Id": fid, "PaiId": parent, "Name": label, "Epigrafe": ""},
        "FragmentoVersao": {
            "Tituo": label,
            "Epigrafe": heading,
            "Texto": text,
            "VersaoEstadoId": state,
            "DataVersao": "2043-03-01T00:00:00",
            "DataEntradaVigor": "2043-03-02T00:00:00",
        },
    }


PREAMBLE = (
    "Decreto-Lei n.º 7/2042\rde 2 de março\rSumário: Aprova o regime de exemplo.\r"
    "O regime de exemplo carecia de simplificação.\rAssim:\r"
    "Nos termos da alínea a) do n.º 1 do artigo 198.º da Constituição, "
    "o Governo decreta o seguinte:"
)
CONSOLIDATED = [
    _frag("100", "0", "Diploma", text=PREAMBLE),
    _frag("101", "100", "Capítulo I", "Disposições gerais"),
    _frag("102", "101", "Artigo 1.º", "Objeto", "O presente decreto-lei aprova o regime anexo."),
    _frag(
        "103",
        "101",
        "Artigo 5.º",
        "Definições",
        "1 - Para efeitos do presente decreto-lei, entende-se por:\r"
        "a) «Exemplo», o caso tipo;\rb) «Registo», a inscrição.\r2 - As definições valem no anexo.",
    ),
    _frag("104", "101", "Artigo 5.º-A", "Registo eletrónico", "O registo é eletrónico."),
    _frag("105", "101", "Artigo 6.º", "Norma revogada", "Texto revogado.", state=4),
    _frag("112", "101", "Anexo II", "Modelo", "Modelo de requerimento."),
    # A division marked repealed over an article the source still marks in force.
    _frag("106", "100", "Capítulo II", "Comissão", state=4),
    _frag("107", "106", "Artigo 7.º", "Funções", "A comissão acompanha o regime."),
    _frag("108", "100", "Assinatura", text="Visto e aprovado em Conselho de Ministros."),
    _frag("109", "100", "Anexo\r(a que se refere o artigo 1.º)", "Regime de exemplo"),
    _frag("110", "109", "Artigo 1.º", "Âmbito", "O regime aplica-se a todos os casos."),
    _frag("111", "109", "Artigo 2.º", "Prazos", "Os prazos contam-se em dias úteis."),
]

PRINTED = (
    "Decreto-Lei n.º 4/2043\rde 10 de janeiro\rSumário: Fixa as taxas de exemplo.\r"
    "Nos termos da alínea a) do n.º 1 do artigo 198.º da Constituição, "
    "o Governo decreta o seguinte:\r"
    "CAPÍTULO I\rSECÇÃO I\rDisposições gerais\r"
    "Artigo 1.º\rObjeto\rO presente decreto-lei fixa as taxas.\r"
    "Artigo 2.º\rTaxas\r1 - As taxas constam do anexo.\r2 - As taxas são atualizadas.\r"
    "Visto e aprovado em Conselho de Ministros de 2 de janeiro de 2043. - Nome Exemplo.\r"
    "ANEXO\rTabela de taxas\rTaxa geral: dez unidades."
)


def _detail(did: str, numero: str, day: str, eli: str, texto: str, tipo: str = "Decreto-Lei"):
    return {
        "Id": did,
        "Titulo": f"{tipo} n.º {numero} ",
        "Numero": numero,
        "TipoDiploma": tipo,
        "DataPublicacao": day,
        "Publicacao": f"Diário da República n.º 12/{day[:4]}, Série I de {day}",
        "Serie": "I",
        "Suplemento": "",
        "Pagina": "40 - 44",
        "Emissor": "Presidência do Conselho de Ministros",
        "Sumario": "Regime de exemplo",
        "Vigencia": "VIGENTE",
        "ELI": eli,
        "URL_PDF": "https://files.example.invalid/act.pdf",
        "Texto": texto,
        "DiarioRepublica": {"Numero": "12"},
    }


DETAILS = {
    "decreto-lei/7-2042-900001": _detail(
        "900001",
        "7/2042",
        "2042-03-02",
        "https://data.dre.pt/eli/dec-lei/7/2042/03/02/p/dre/pt/html",
        PREAMBLE + "\rArtigo 1.º\rObjeto\rO presente decreto-lei aprova o regime anexo.",
    ),
    "decreto-lei/4-2043-900002": _detail(
        "900002",
        "4/2043",
        "2043-01-10",
        "https://data.dre.pt/eli/dec-lei/4/2043/01/10/p/dre/pt/html",
        PRINTED,
    ),
}
LANDINGS = {
    "dec-lei/7/2042/p/cons/pt/html": "/dr/legislacao-consolidada/decreto-lei/2042-555",
    "dec-lei/4/2043/01/10/p/dre/pt/html": "/dr/detalhe/decreto-lei/4-2043-900002",
}


VIEWS = {view for _, _, view in (DETAIL, FRAG, DATA)}


def _script(view: str, apis: dict[str, str]) -> str:
    """A screen script trimmed to the calls that name its actions' apiVersions."""
    calls = "".join(
        f'return controller.callDataAction("{path.rsplit("/", 1)[1]}", '
        f'"00000000-0000-4000-8000-000000000000", "screenservices/dr/{path}", "{api}", '
        "function (b) {});\n"
        for path, api in apis.items()
        if path.startswith(view.replace(".", "/") + "/")
    )
    return f'define("dr.{view}.mvc$controller", [], function () {{\n{calls}}});\n'


class Gazette:
    """The site's answers, and every request it was asked."""

    def __init__(
        self,
        frags: list[dict[str, Any]] | None = None,
        *,
        changed: bool = False,
        last: str = "2043-03-01",
        answer: dict[str, Any] | str | None = None,
    ):
        self.frags = CONSOLIDATED if frags is None else frags
        self.changed = changed
        self.last = last
        # What every screen call answers instead, when set.
        self.answer = answer
        self.module = "mv-1"
        self.apis = {path: api for path, api, _ in (DETAIL, FRAG, DATA)}
        self.manifest = {f"/dr/scripts/dr.{view}.mvc.js": "?h1" for view in VIEWS}
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if request.url.host == "data.dre.pt":
            search = path.removeprefix("/eli/")
            return httpx.Response(
                301, headers={"location": f"{SITE}/redirect/LinkELI.aspx?search={search}"}
            )
        if path == "/redirect/LinkELI.aspx":
            target = LANDINGS.get(request.url.params["search"], "/dr/error")
            return httpx.Response(301, headers={"location": SITE + target})
        if path == "/dr/moduleservices/moduleversioninfo":
            return httpx.Response(200, json={"versionToken": self.module})
        if path == "/dr/moduleservices/moduleinfo":
            manifest = {"versionToken": self.module, "urlVersions": self.manifest}
            return httpx.Response(200, json={"manifest": manifest})
        if path.startswith("/dr/scripts/dr."):
            view = path.removeprefix("/dr/scripts/dr.").removesuffix(".mvc.js")
            return httpx.Response(200, text=_script(view, self.apis))
        if path.startswith("/dr/screenservices/dr/"):
            body = json.loads(request.content)
            v = body["screenData"]["variables"]
            sent = body["versionInfo"]
            info = {
                "hasModuleVersionChanged": sent["moduleVersion"] != self.module,
                "hasApiVersionChanged": self.changed
                or sent["apiVersion"] != self.apis.get(path.removeprefix("/dr/screenservices/dr/")),
            }
            if isinstance(self.answer, str):
                return httpx.Response(200, text=self.answer)
            if self.answer is not None:
                return httpx.Response(200, json={"versionInfo": info, **self.answer})
            if path.endswith("DataActionGetAllConteudoDetalheData"):
                data: dict[str, Any] = {"DetalheConteudo": DETAILS.get(f"{v['Tipo']}/{v['Key']}")}
            elif path.endswith("DataActionGetDiplomaFragByIdAndApplicationSetting"):
                data = {
                    "ConsolidadaConteudoDetalhe": {
                        "DiplomaFrag": {"DiplomaLegisId": "900001", "EmAtualizacao": False},
                        "DiplomaLegis": {"LinkSitemap": "/dr/detalhe/decreto-lei/7-2042-900001"},
                    }
                }
            else:
                assert v["GetDiplomaFragByIdAndApplicationSetting"]
                data = {"LegConsBase": {"List": self.frags}, "DataUltimaConsolidada": self.last}
            return httpx.Response(200, json={"versionInfo": info, "data": data})
        raise AssertionError(f"unexpected request {request.url}")


async def _fetch(
    gazette: Gazette, ref: DocumentRef, cache: Path | None = None
) -> tuple[Any, DiarioAcquirer]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(gazette))
    try:
        acquirer = DiarioAcquirer(
            SourceAdapter(kind="json_portal", name="dre"), client=client, cache_dir=cache
        )
        return await acquirer.fetch(ref), acquirer
    finally:
        await client.aclose()


def _ref(number: str, year: int, **extra: str) -> DocumentRef:
    return DocumentRef(jurisdiction_code="pt", doctype="dl", year=year, number=number, extra=extra)


def _eids(doc: Any) -> list[str]:
    root = etree.fromstring(doc.primary().content)
    return [str(e) for e in root.xpath("//a:article/@eId | //a:chapter/@eId", namespaces=NS)]


async def test_the_official_work_id_is_the_gazettes_undated_work_eli() -> None:
    doc, _ = await _fetch(Gazette(), _ref("7", 2042))
    assert doc.official is not None
    assert (doc.official.publisher, doc.official.work_id) == ("dre", "dec-lei/7/2042")


async def test_the_law_takes_its_sumario_as_title_and_its_citation_as_short_title() -> None:
    doc, _ = await _fetch(Gazette(), _ref("7", 2042))
    xml = doc.primary().content.decode()
    root = etree.fromstring(doc.primary().content)
    assert [" ".join(p.itertext()) for p in root.xpath("//a:preface//a:p", namespaces=NS)] == [
        "Decreto-Lei n.º 7/2042",
        "de 2 de março",
        "Aprova o regime de exemplo.",
    ]
    # What `save_document` writes to `laws.title` and `laws.short_title`.
    assert _extract_long_title(xml) == "Aprova o regime de exemplo."
    assert short_title_from_akn(xml, "", "pt") == "Decreto-Lei n.º 7/2042"


@pytest.mark.parametrize(
    ("stated", "preface"),
    [
        ("", ["Lei n.º 3/2041", "Cria o exemplo."]),
        (
            "Lei n.º 3/2041, de 2 de março\rSumário:",
            ["Lei n.º 3/2041 , de 2 de março", "Cria o exemplo."],
        ),
    ],
)
def test_a_preface_that_omits_them_takes_the_gazettes_citation_and_sumario(
    stated: str, preface: list[str]
) -> None:
    frags = [
        {"id": "1", "parent": "0", "label": "Diploma", "heading": "", "text": stated},
        {"id": "2", "parent": "1", "label": "Artigo 1.º", "heading": "", "text": "Texto."},
    ]
    bluebell = to_bluebell(frags, title="Lei n.º 3/2041", summary="Cria o exemplo.")
    work = "/akn/pt/act/2041/3"
    xml = to_akn(
        bluebell,
        number="3",
        work=work,
        published="2041-03-02",
        expression=f"{work}/por@2041-03-02",
        as_of="2041-03-02",
        title="Lei n.º 3/2041",
    )
    root = etree.fromstring(xml.encode())
    assert [" ".join(p.itertext()) for p in root.xpath("//a:preface//a:p", namespaces=NS)] == (
        preface
    )
    assert _extract_long_title(xml) == "Cria o exemplo."
    assert short_title_from_akn(xml, "", "pt") == "Lei n.º 3/2041"


async def test_consolidated_act_keeps_structure_and_leaves_repealed_out() -> None:
    gazette = Gazette()
    doc, _ = await _fetch(gazette, _ref("7", 2042))
    assert doc.frbr_work_uri == "/akn/pt/act/dl/2042/7"
    assert doc.expression_uri == "/akn/pt/act/dl/2042/7/por@2043-03-01"
    assert _eids(doc) == [
        "chp_I",
        "chp_I__art_1",
        "chp_I__art_5",
        "chp_I__art_5A",
        "chp_II",
        "chp_II__art_7",
        # The annexed regime restarts at Artigo 1.º under its own prefix; the annex
        # hung below the first chapter comes first, in source order.
        "att_2__art_1",
        "att_2__art_2",
    ]
    root = etree.fromstring(doc.primary().content)
    assert root.xpath("//a:point/@eId", namespaces=NS) == [
        "chp_I__art_5__para_1__point_a",
        "chp_I__art_5__para_1__point_b",
    ]
    assert b"Texto revogado" not in doc.primary().content
    assert root.xpath("//a:attachment/a:heading/text()", namespaces=NS) == ["Anexo II", "Anexo"]
    work = root.xpath("//a:act/a:meta/a:identification/a:FRBRWork", namespaces=NS)[0]
    assert work.xpath("a:FRBRalias[@name='eli']/@value", namespaces=NS) == [
        "https://data.dre.pt/eli/dec-lei/7/2042/03/02/p/dre"
    ]
    assert work.xpath("a:FRBRalias[@name='title']/@value", namespaces=NS) == [
        "Decreto-Lei n.º 7/2042"
    ]
    publication = root.xpath("//a:act/a:meta/a:publication", namespaces=NS)[0]
    assert dict(publication.attrib) == {
        "date": "2042-03-02",
        "name": "Diário da República",
        "showAs": "Diário da República n.º 12/2042, Série I de 2042-03-02",
        "number": "12",
    }
    assert doc.source_url == f"{SITE}/dr/legislacao-consolidada/decreto-lei/2042-555"
    meta = doc.upstream_metadata
    assert (meta["text_source"], meta["consolidation_date"], meta["repealed_fragments"]) == (
        "consolidated",
        "2043-03-01",
        2,
    )
    assert meta["consolidated_eli"] == "https://data.dre.pt/eli/dec-lei/7/2042/p/cons/20430301"
    assert meta["detail_url"] == f"{SITE}/dr/detalhe/decreto-lei/7-2042-900001"
    assert doc.gazette_ref is not None
    assert (doc.gazette_ref.issue, doc.gazette_ref.page) == ("12", "40")
    # The anonymous token rides every call; the landing page itself is never fetched.
    calls = [r for r in gazette.seen if "/screenservices/" in r.url.path]
    assert len(calls) == 3
    assert all(r.headers["x-csrftoken"] for r in calls)
    assert not any(r.url.path.startswith("/dr/legislacao-consolidada") for r in gazette.seen)


async def test_the_native_akn_ingest_accepts_the_document(tmp_path: Path) -> None:
    doc, _ = await _fetch(Gazette(), _ref("7", 2042))
    path = tmp_path / "act.xml"
    path.write_bytes(doc.primary().content)
    events = [e async for e in akn_native.ingest(path, "pt")]
    assert not [e for e in events if isinstance(e, Failed)]
    done = events[-1]
    assert isinstance(done, Complete)
    assert done.document.frbr_work_uri == "/akn/pt/act/dl/2042/7"


@pytest.mark.parametrize(
    "extra",
    [
        {"eli": "http://data.dre.pt/eli/dec-lei/4/2043/01/10/p/dre/pt/html"},
        {"detail": "https://diariodarepublica.pt/dr/detalhe/decreto-lei/4-2043-900002"},
        {"detail": "decreto-lei/4-2043-900002"},
    ],
)
async def test_an_act_with_no_consolidation_is_read_from_its_printed_text(
    extra: dict[str, str],
) -> None:
    doc, _ = await _fetch(Gazette(), _ref("4", 2043, **extra))
    assert doc.frbr_work_uri == "/akn/pt/act/dl/2043/4"
    assert doc.expression_uri == "/akn/pt/act/dl/2043/4/por@2043-01-10"
    assert _eids(doc) == ["chp_I", "chp_I__sec_I__art_1", "chp_I__sec_I__art_2"]
    root = etree.fromstring(doc.primary().content)
    assert root.xpath("string(//a:section/a:heading)", namespaces=NS) == "Disposições gerais"
    assert root.xpath(
        "//a:article[@eId='chp_I__sec_I__art_2']/a:paragraph/@eId", namespaces=NS
    ) == [
        "chp_I__sec_I__art_2__para_1",
        "chp_I__sec_I__art_2__para_2",
    ]
    assert root.xpath("string(//a:conclusions)", namespaces=NS).strip().startswith("Visto")
    assert root.xpath("//a:attachment/a:heading/text()", namespaces=NS) == ["Anexo"]
    assert doc.upstream_metadata["text_source"] == "printed"
    assert doc.source_url == f"{SITE}/dr/detalhe/decreto-lei/4-2043-900002"


async def test_an_undated_ref_without_consolidation_is_refused() -> None:
    with pytest.raises(DrItemMissing, match="no consolidation"):
        await _fetch(Gazette(), _ref("4", 2043))


async def test_a_wholly_repealed_consolidation_is_refused() -> None:
    gone = [
        _frag("100", "0", "Diploma", text=PREAMBLE, state=4),
        _frag("101", "100", "Artigo 1.º", "Objeto", "Texto.", state=4),
    ]
    with pytest.raises(UnmappedFragment, match="whole act repealed"):
        await _fetch(Gazette(gone), _ref("7", 2042))


async def test_a_ref_typed_otherwise_than_the_gazette_is_refused() -> None:
    ref = DocumentRef(
        jurisdiction_code="pt",
        doctype="portaria",
        year=2042,
        number="7",
        extra={"eli": "https://data.dre.pt/eli/dec-lei/7/2042/03/02/p/dre"},
    )
    with pytest.raises(ValueError, match="types it 'Decreto-Lei'"):
        await _fetch(Gazette(), ref)


async def test_a_ref_naming_another_act_is_refused() -> None:
    ref = _ref("8", 2042, eli="https://data.dre.pt/eli/dec-lei/7/2042/03/02/p/dre")
    with pytest.raises(ValueError, match="asks for 8/2042"):
        await _fetch(Gazette(), ref)


async def test_an_untyped_act_is_refused() -> None:
    DETAILS["decreto-lei/7-2042-900001"]["TipoDiploma"] = "Decreto Regulamentar"
    try:
        with pytest.raises(ValueError, match="types it 'Decreto Regulamentar'"):
            await _fetch(Gazette(), _ref("7", 2042))
    finally:
        DETAILS["decreto-lei/7-2042-900001"]["TipoDiploma"] = "Decreto-Lei"


async def test_a_refused_resolution_is_raised_not_saved(tmp_path: Path) -> None:
    def busy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(busy))
    acquirer = DiarioAcquirer(
        SourceAdapter(kind="json_portal", name="dre"), client=client, cache_dir=tmp_path / "raw"
    )
    with pytest.raises(httpx.HTTPStatusError):
        await acquirer.fetch(_ref("7", 2042))
    await client.aclose()
    assert not (tmp_path / "raw").exists()


async def test_a_fragment_outside_the_tree_is_refused() -> None:
    stray = [*CONSOLIDATED, _frag("120", "999", "Artigo 9.º", "Perdido", "Texto.")]
    with pytest.raises(UnmappedFragment, match="outside the act's tree"):
        await _fetch(Gazette(stray), _ref("7", 2042))


SCREEN_SCRIPT = (
    'return controller.callDataAction("DataActionGetX", "00000000-0000-4000-8000-000000000000", '
    '"screenservices/dr/Mod/Screen/DataActionGetX", "Ab+c/D_e-F==", function (b) {\n'
    'return controller.callServerAction("GetY", "screenservices/dr/Mod/Screen/ActionGetY", '
    '"Yy9_zz", inputs, controller.callContext(callContext), undefined, undefined, false);\n'
)


def test_a_screen_script_names_each_actions_api_version() -> None:
    assert api_version(SCREEN_SCRIPT, "Mod/Screen/DataActionGetX") == "Ab+c/D_e-F=="
    assert api_version(SCREEN_SCRIPT, "Mod/Screen/ActionGetY") == "Yy9_zz"
    # A prefix of an action, or the same action on another screen, is not named.
    assert api_version(SCREEN_SCRIPT, "Mod/Screen/DataActionGet") is None
    assert api_version(SCREEN_SCRIPT, "Mod/Other/DataActionGetX") is None


async def test_a_redeployed_site_is_rediscovered_once_and_the_call_retried() -> None:
    gazette = Gazette()
    client = httpx.AsyncClient(transport=httpx.MockTransport(gazette))
    acquirer = DiarioAcquirer(SourceAdapter(kind="json_portal", name="dre"), client=client)
    await acquirer.fetch(_ref("7", 2042))
    gazette.module, gazette.apis[DATA[0]] = "mv-2", "Fresh+Api/Version=="
    for _ in range(2):
        doc = await acquirer.fetch(_ref("7", 2042))
    await client.aclose()

    assert doc.upstream_metadata["text_source"] == "consolidated"
    found = [r.url for r in gazette.seen if r.url.path.startswith("/dr/scripts/")]
    assert [(u.path, u.query) for u in found] == [
        ("/dr/scripts/dr.LegislacaoConsolidada.LegCons_Detalhe.mvc.js", b"h1")
    ]
    assert sum(r.url.path == "/dr/moduleservices/moduleinfo" for r in gazette.seen) == 1
    sent = [
        json.loads(r.content)["versionInfo"] for r in gazette.seen if r.url.path.endswith(DATA[0])
    ]
    pinned = {"moduleVersion": "mv-1", "apiVersion": DATA[1]}
    fresh = {"moduleVersion": "mv-2", "apiVersion": "Fresh+Api/Version=="}
    assert sent == [pinned, pinned, fresh, fresh]


async def test_a_site_still_reporting_a_new_version_after_rediscovery_stops_the_fetch() -> None:
    gazette = Gazette(changed=True)
    with pytest.raises(SiteChanged, match="still reports a new API version"):
        await _fetch(gazette, _ref("7", 2042))
    assert sum(r.url.path == "/dr/moduleservices/moduleinfo" for r in gazette.seen) == 1
    assert sum(r.url.path.endswith(FRAG[0]) for r in gazette.seen) == 2


@pytest.mark.parametrize(
    ("unlisted", "match"),
    [(True, "manifest no longer lists"), (False, "script no longer names this action")],
)
async def test_an_action_the_site_no_longer_names_stops_the_fetch(
    unlisted: bool, match: str
) -> None:
    gazette = Gazette()
    del gazette.apis[DATA[0]]
    if unlisted:
        gazette.manifest.clear()
    with pytest.raises(SiteChanged, match=match):
        await _fetch(gazette, _ref("7", 2042))


@pytest.mark.parametrize(
    "answer",
    [
        {"exception": {"name": "ServerException"}},
        {"data": None},
        {"exception": {"name": "ServerException"}, "data": {}},
        "<html>",
    ],
)
async def test_an_answer_of_another_shape_is_refused_not_rediscovered(answer: Any) -> None:
    gazette = Gazette(answer=answer)
    with pytest.raises(DrItemMissing):
        await _fetch(gazette, _ref("7", 2042))
    assert not any(r.url.path == "/dr/moduleservices/moduleinfo" for r in gazette.seen)


async def test_saved_answers_are_read_before_the_network(tmp_path: Path) -> None:
    first, _ = await _fetch(Gazette(), _ref("7", 2042), cache=tmp_path / "raw")
    assert first.upstream_metadata["from_cache"] is False
    again = Gazette()
    second, _ = await _fetch(again, _ref("7", 2042), cache=tmp_path / "raw")
    assert again.seen == []
    assert second.upstream_metadata["from_cache"] is True
    assert second.primary().content == first.primary().content
    assert second.fetched_at <= first.fetched_at


def test_the_client_is_polite_and_never_faster_than_the_gazette_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACQUISITION_USER_AGENT", "pt-test/1 (+https://example.test)")
    fast = SourceAdapter(kind="json_portal", name="dre", rate_limit_per_minute=600)
    acquirer = DiarioAcquirer(fast)
    assert isinstance(acquirer._client._transport, PoliteTransport)
    assert acquirer.politeness.rate_limit_per_minute == 40
    assert acquirer.politeness.concurrency == 1
    assert acquirer._client._transport.profile.user_agent == "pt-test/1 (+https://example.test)"


def test_the_client_needs_the_callers_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACQUISITION_USER_AGENT")
    with pytest.raises(MissingUserAgent):
        DiarioAcquirer(SourceAdapter(kind="json_portal", name="dre"))


def test_json_portal_resolves_by_jurisdiction() -> None:
    from codify.acquisition import _REGISTRY
    from codify.acquisition.adapters.th import OcsAcquirer

    factory = _REGISTRY["json_portal"]
    adapter = SourceAdapter(kind="json_portal", name="any")
    assert isinstance(factory("pt", adapter), DiarioAcquirer)
    assert isinstance(factory("th", adapter), OcsAcquirer)
    with pytest.raises(ValueError, match="no json_portal acquirer"):
        factory("xa", adapter)


@pytest.mark.parametrize(
    ("numero", "published", "expected"),
    [
        ("102-D/2020", "2020-12-10", ("102-D", "2020")),
        ("129/98", "1998-05-13", ("129", "1998")),
        ("320/2026/1", "2026-07-31", ("320", "2026")),
    ],
)
def test_act_numbers(numero: str, published: str, expected: tuple[str, str]) -> None:
    assert number_and_year(numero, published) == expected


def test_an_unread_act_number_is_refused() -> None:
    with pytest.raises(DrItemMissing):
        number_and_year("Retificação 3/2042", "2042-01-01")


def test_i_after_h_is_the_next_alinea_unless_ii_follows() -> None:
    frags = [
        {"id": "1", "parent": "0", "label": "Diploma", "heading": "", "text": "", "state": 6},
        {
            "id": "2",
            "parent": "1",
            "label": "Artigo 1.º",
            "heading": "Lista",
            "text": "Texto:\rh) agá;\ri) primeiro;\rii) segundo;\ri) alínea i.",
            "state": 6,
        },
    ]
    text = to_bluebell(frags)
    assert "INDENT i)" in text and "INDENT ii)" in text and text.count("POINT i)") == 1


def test_an_act_with_no_operative_text_is_refused() -> None:
    root = {"id": "1", "parent": "0", "label": "Diploma", "heading": "", "text": "Preâmbulo."}
    with pytest.raises(UnmappedFragment, match="no operative text"):
        to_bluebell([root])


async def test_a_consolidation_of_another_act_than_the_detail_is_refused() -> None:
    ref = _ref(
        "4",
        2043,
        eli="https://data.dre.pt/eli/dec-lei/7/2042/03/02/p/dre",
        detail="decreto-lei/4-2043-900002",
    )
    with pytest.raises(ValueError, match="is not the act at decreto-lei/4-2043-900002"):
        await _fetch(Gazette(), ref)


async def test_an_eli_naming_another_act_than_the_detail_is_refused() -> None:
    ref = _ref(
        "7",
        2042,
        eli="https://data.dre.pt/eli/dec-lei/4/2043/01/10/p/dre",
        detail="decreto-lei/7-2042-900001",
    )
    with pytest.raises(ValueError, match="by https://data.dre.pt/eli/dec-lei/4/2043"):
        await _fetch(Gazette(), ref)


async def test_the_enacted_text_is_the_printed_one() -> None:
    ref = DocumentRef(jurisdiction_code="pt", doctype="dl", year=2042, number="7", as_of="enacted")
    doc, _ = await _fetch(Gazette(), ref)
    assert doc.upstream_metadata["text_source"] == "printed"
    assert doc.expression_uri == "/akn/pt/act/dl/2042/7/por@2042-03-02"
    assert _eids(doc) == ["art_1"]


async def test_a_dated_version_is_refused() -> None:
    ref = DocumentRef(
        jurisdiction_code="pt", doctype="dl", year=2042, number="7", as_of=date(2043, 1, 1)
    )
    with pytest.raises(ValueError, match="as of today"):
        await _fetch(Gazette(), ref)


async def test_only_positive_answers_are_saved(tmp_path: Path) -> None:
    cache = tmp_path / "raw"
    ref = _ref("4", 2043, eli="https://data.dre.pt/eli/dec-lei/4/2043/01/10/p/dre")
    await _fetch(Gazette(), ref, cache=cache)
    again = Gazette()
    await _fetch(again, ref, cache=cache)
    # "No consolidation" is asked again; the act's own landing and detail are not.
    assert [r.url.path for r in again.seen] == [
        "/eli/dec-lei/4/2043/p/cons/pt/html",
        "/redirect/LinkELI.aspx",
    ]
    missing = _ref("5", 2043, detail="decreto-lei/5-2043-1")
    with pytest.raises(DrItemMissing, match="no act at"):
        await _fetch(Gazette(), missing, cache=cache)
    assert len(list(cache.iterdir())) == 2


async def test_an_eli_the_site_does_not_know_reads_as_no_consolidation() -> None:
    gazette = Gazette()

    def gone(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/p/cons/pt/html"):
            return httpx.Response(404)
        return gazette(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(gone))
    acquirer = DiarioAcquirer(SourceAdapter(kind="json_portal", name="dre"), client=client)
    ref = _ref("4", 2043, eli="https://data.dre.pt/eli/dec-lei/4/2043/01/10/p/dre")
    doc = await acquirer.fetch(ref)
    await client.aclose()
    assert doc.upstream_metadata["text_source"] == "printed"


async def test_an_undated_consolidation_is_dated_by_its_versions_or_publication() -> None:
    undated = [
        {**f, "FragmentoVersao": {**f["FragmentoVersao"], "DataVersao": ""}} for f in CONSOLIDATED
    ]
    doc, _ = await _fetch(Gazette(undated, last=""), _ref("7", 2042))
    assert doc.expression_uri == "/akn/pt/act/dl/2042/7/por@2042-03-02"


def _shape(frags: list[dict[str, Any]]) -> list[tuple[str, str]]:
    labels = {f["id"]: f["label"] for f in frags}
    return [(f["label"], labels.get(f["parent"], "")) for f in frags[1:]]


def test_bare_provisions_and_annexed_articles_are_both_read() -> None:
    text = (
        "Portaria n.º 9/2042\rManda o Governo, pelo membro responsável, o seguinte:\r"
        "1.º\rObjeto\rFixa as taxas.\r2.º\rAs taxas constam do anexo.\r"
        "ANEXO\rArtigo 1.º\rÂmbito\rTexto do anexo."
    )
    assert _shape(from_printed(text)) == [
        ("Artigo 1.º", "Diploma"),
        ("Artigo 2.º", "Diploma"),
        ("Anexo", "Diploma"),
        ("Artigo 1.º", "Anexo"),
    ]


def test_without_an_enacting_formula_the_body_opens_at_its_first_division() -> None:
    text = "Lei n.º 1/2042\rCAPÍTULO I\rDisposições gerais\rArtigo 1.º\rObjeto\rTexto."
    frags = from_printed(text)
    assert frags[0]["text"] == "Lei n.º 1/2042"
    assert _shape(frags) == [("Capítulo I", "Diploma"), ("Artigo 1.º", "Capítulo I")]


def test_a_quote_opened_mid_line_keeps_the_quoted_markers_prose() -> None:
    text = "1 - O texto passa a ser: «\r2 - Regra citada.»\r2 - Regra atual."
    assert [x.strip() for x in article_body(text, "") if "PARA" in x] == ["PARA 1", "PARA 2"]
    printed = (
        "Lei n.º 2/2042\rA Assembleia decreta o seguinte:\r"
        "Artigo 1.º\rAlteração\rO artigo 8.º passa a ser: «\rArtigo 8.º\rRegra\rTexto.»\r"
        "Artigo 2.º\rEntrada em vigor\rEntra em vigor amanhã."
    )
    assert _shape(from_printed(printed)) == [("Artigo 1.º", "Diploma"), ("Artigo 2.º", "Diploma")]


def test_a_self_parented_fragment_is_refused() -> None:
    root = {"id": "1", "parent": "0", "label": "Diploma", "heading": "", "text": ""}
    one = {"id": "2", "parent": "1", "label": "Artigo 1.º", "heading": "", "text": "Um."}
    loop = {"id": "3", "parent": "3", "label": "Artigo 2.º", "heading": "", "text": "Dois."}
    with pytest.raises(UnmappedFragment, match="outside the act's tree"):
        to_bluebell([root, one, loop])


def test_divisions_alone_are_no_operative_text() -> None:
    root = {"id": "1", "parent": "0", "label": "Diploma", "heading": "", "text": ""}
    chapter = {"id": "2", "parent": "1", "label": "Capítulo I", "heading": "", "text": ""}
    gone = {"id": "3", "parent": "2", "label": "Artigo 1.º", "heading": "", "text": "", "state": 4}
    with pytest.raises(UnmappedFragment, match="no operative text"):
        to_bluebell([root, {**chapter, "state": 6}, gone])


def test_long_roman_subalineas_stay_nested() -> None:
    items = "\r".join(f"{r}) item;" for r in ROMAN)
    body = [x.strip() for x in article_body(f"a) alínea:\r{items}", "")]
    assert [x for x in body if x.startswith(("POINT", "INDENT"))] == ["POINT a)"] + [
        f"INDENT {r})" for r in ROMAN
    ]


async def test_the_enacted_text_by_detail_skips_the_consolidation() -> None:
    gazette = Gazette()
    ref = DocumentRef(
        jurisdiction_code="pt",
        doctype="dl",
        year=2042,
        number="7",
        as_of="enacted",
        extra={"detail": "decreto-lei/7-2042-900001"},
    )
    doc, _ = await _fetch(gazette, ref)
    assert doc.upstream_metadata["text_source"] == "printed"
    assert [r.url.path for r in gazette.seen if "/p/cons/" in r.url.path] == []
    assert [r for r in gazette.seen if "LegCons" in r.url.path] == []


async def test_an_empty_consolidation_is_not_saved(tmp_path: Path) -> None:
    cache = tmp_path / "raw"
    ref = _ref("7", 2042, eli="https://data.dre.pt/eli/dec-lei/7/2042/03/02/p/dre")
    first, _ = await _fetch(Gazette([]), ref, cache=cache)
    assert first.upstream_metadata["text_source"] == "printed"
    second, _ = await _fetch(Gazette(), ref, cache=cache)
    assert second.upstream_metadata["text_source"] == "consolidated"


def test_alinea_runs_past_four_letters_stay_points() -> None:
    body = [x.strip() for x in article_body("Lista:\rzzzz) quarta;\raaaaa) quinta.", "")]
    assert [x for x in body if x.startswith("POINT")] == ["POINT zzzz)", "POINT aaaaa)"]


async def test_an_unwritable_index_dir_fetches_without_a_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "datadumps"
    root.mkdir()
    root.chmod(0o555)
    monkeypatch.setenv("ACQUISITION_INDEX_DIR", str(root))
    try:
        acquirer = DiarioAcquirer.from_config("pt", SourceAdapter(kind="json_portal", name="dre"))
        assert acquirer._cache is None
        acquirer._client = httpx.AsyncClient(transport=httpx.MockTransport(Gazette()))
        doc = await acquirer.fetch(_ref("7", 2042))
        await acquirer._client.aclose()
    finally:
        root.chmod(0o755)
    assert doc.frbr_work_uri == "/akn/pt/act/dl/2042/7"
    assert not (root / "pt").exists()


def test_a_writable_index_dir_keeps_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACQUISITION_INDEX_DIR", str(tmp_path))
    acquirer = DiarioAcquirer.from_config("pt", SourceAdapter(kind="json_portal", name="dre"))
    assert acquirer._cache == tmp_path / "pt" / "raw"


def _article(text: str) -> Any:
    root = {"id": "1", "parent": "0", "label": "Diploma", "heading": "", "text": ""}
    art = {"id": "2", "parent": "1", "label": "Artigo 1.º", "heading": "Regime", "text": text}
    xml = to_akn(
        to_bluebell([root, art]),
        number="1",
        work="/akn/pt/act/2042/1",
        published="2042-01-01",
        expression="/akn/pt/act/2042/1/por@2042-01-01",
        as_of="2042-01-01",
    )
    return etree.fromstring(xml.encode()).xpath("//a:article", namespaces=NS)[0]


def _words(el: Any) -> str:
    return " ".join("".join(el.itertext()).split())


def test_html_breaks_separate_numbered_paragraphs_and_alineas() -> None:
    art = _article(
        "1 - O pedido é apresentado:<br/>a) Por via eletrónica;<br />b) Presencialmente."
        "\r\n2 - O prazo é de dez dias."
    )
    paras = art.xpath("a:paragraph", namespaces=NS)
    assert [p.findtext("a:num", namespaces=NS) for p in paras] == ["1", "2"]
    points = paras[0].xpath("a:point", namespaces=NS)
    assert [p.findtext("a:num", namespaces=NS) for p in points] == ["a)", "b)"]
    assert [_words(p.find("a:content", namespaces=NS)) for p in points] == [
        "Por via eletrónica;",
        "Presencialmente.",
    ]
    assert _words(paras[1].find("a:content", namespaces=NS)) == "O prazo é de dez dias."


def test_mixed_fragments_keep_raw_text_and_breaks() -> None:
    art = _article("1 - Se P<Pmin, vale 0<i≤5 <a fixar>.<BR>2 - Dois.\x0b3 - Três.&#12;4 - Quatro.")
    paras = art.xpath("a:paragraph", namespaces=NS)
    assert [p.findtext("a:num", namespaces=NS) for p in paras] == ["1", "2", "3", "4"]
    assert _words(paras[0].find("a:content", namespaces=NS)) == "Se P<Pmin, vale 0<i≤5 <a fixar>."
    quoted = article_body("<blockquote>1 - Um.</blockquote><h3>2 - Dois.</h3>", "")
    assert [x.strip() for x in quoted if x.strip().startswith("PARA")] == ["PARA 1", "PARA 2"]


def test_html_links_and_superscripts_keep_their_text_and_entities_decode_once() -> None:
    art = _article(
        '<p>1 - Nos termos do <a href="https://example.invalid/a">artigo 3.º</a>, o limite é '
        "10<sup><b>3</b>\n</sup> unidades<sup></sup>.</p>"
        "<p>2 - Lei &amp; regulamento &amp;lt;exemplo&amp;gt; *sem* ênfase.</p>"
    )
    first, second = art.xpath("a:paragraph/a:content/a:p", namespaces=NS)
    assert first.xpath("a:sup/text()", namespaces=NS) == ["3"]
    assert _words(first) == "Nos termos do artigo 3.º, o limite é 103 unidades."
    assert _words(second) == "Lei & regulamento &lt;exemplo&gt; *sem* ênfase."


def test_an_html_table_is_an_akn_table_in_its_paragraph() -> None:
    art = _article(
        "1 - As taxas são as seguintes:"
        "<table><caption>Quadro <b>1</b></caption>Valores<thead>"
        '<tr><th>Escalão</th><th colspan="2">Taxa</th></tr></thead><tr></tr>'
        '<tr><td rowspan="2">A<br/>(geral)<table><tr><td>nota</td></tr></table></td>'
        '<td colspan="dois"></td><td nowrap>(**) 5 % (**)</td></tr><tr><td>6 %</td></tr>'
        "</table><table></table>2 - As taxas são revistas."
    )
    paras = art.xpath("a:paragraph", namespaces=NS)
    assert [p.findtext("a:num", namespaces=NS) for p in paras] == ["1", "2"]
    content = paras[0].find("a:content", namespaces=NS)
    assert [etree.QName(c).localname for c in content] == ["p", "table", "p"]
    assert [_words(p) for p in content.xpath("a:p", namespaces=NS)] == [
        "As taxas são as seguintes:",
        "Quadro 1 Valores",
    ]
    (table,) = content.xpath(".//a:table", namespaces=NS)
    rows = [
        [(etree.QName(c).localname, dict(c.attrib), _words(c)) for c in tr]
        for tr in table.xpath("a:tr", namespaces=NS)
    ]
    assert rows == [
        [("th", {}, "Escalão"), ("th", {"colspan": "2"}, "Taxa")],
        [("td", {"rowspan": "2"}, "A (geral) nota"), ("td", {}, ""), ("td", {}, "(**) 5 % (**)")],
        [("td", {}, "6 %")],
    ]


@pytest.mark.parametrize(("opens", "closes"), [("«", "»"), ('"', '"')])
def test_a_quote_closed_in_a_table_cell_ends_the_quote(opens: str, closes: str) -> None:
    art = _article(
        f"1 - O quadro passa a ter a seguinte redação:<br/>{opens}Quadro"
        f"<table><tr><td>A</td><td>5 %{closes}</td><td></td></tr></table>"
        "2 - O prazo é de dez dias."
    )
    assert [
        p.findtext("a:num", namespaces=NS) for p in art.xpath("a:paragraph", namespaces=NS)
    ] == [
        "1",
        "2",
    ]


def test_plain_fragments_read_as_before() -> None:
    text = "1 - Se 0<i≤5 e b > a, vale &amp; conta.\r2 - O valor <a fixar> é anual."
    assert article_body(text, "") == [
        "  PARA 1",
        "    Se 0<i≤5 e b > a, vale &amp; conta.",
        "  PARA 2",
        "    O valor <a fixar> é anual.",
    ]


def test_printed_html_is_decoded_once_and_a_table_is_no_heading() -> None:
    printed = (
        "<p>Lei n.º 1/2042</p><p>A Assembleia decreta o seguinte:</p><p>Artigo 1.º</p>"
        "<table><caption>Quadro I</caption><tr><td>A</td><td>5 %</td></tr></table>"
        "<p>1 - O sinal &lt;b&gt; separa.</p><p>2 - Fim.</p>"
    )
    text = to_bluebell(from_printed(printed))
    assert "  ARTICLE 1\n    TABLE\n" in text
    assert "      O sinal <b> separa.\n" in text
