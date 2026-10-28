"""PT, DiarioAcquirer unit tests via httpx.MockTransport, on invented acts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from lxml import etree

import codify.acquisition.adapters  # noqa: F401  registers adapters
from codify.acquisition import DocumentRef
from codify.acquisition.adapters.pt.dre import (
    USER_AGENT,
    DiarioAcquirer,
    DrItemMissing,
    SiteChanged,
    number_and_year,
)
from codify.acquisition.adapters.pt.dre_akn import UnmappedFragment, to_bluebell
from codify.acquisition.politeness import PoliteTransport
from codify.jurisdictions import SourceAdapter
from codify.pipeline.events import Complete, Failed
from codify.pipeline.formats import akn_native

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
        PREAMBLE,
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


class Gazette:
    """The site's answers, and every request it was asked."""

    def __init__(
        self,
        frags: list[dict[str, Any]] | None = None,
        *,
        changed: bool = False,
        last: str = "2043-03-01",
    ):
        self.frags = CONSOLIDATED if frags is None else frags
        self.changed = changed
        self.last = last
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
            return httpx.Response(200, json={"versionToken": "mv-1"})
        if path.startswith("/dr/screenservices/dr/"):
            body = json.loads(request.content)
            v = body["screenData"]["variables"]
            info = {"hasApiVersionChanged": self.changed}
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


async def test_a_changed_site_api_stops_the_fetch() -> None:
    with pytest.raises(SiteChanged):
        await _fetch(Gazette(changed=True), _ref("7", 2042))


async def test_saved_answers_are_read_before_the_network(tmp_path: Path) -> None:
    first, _ = await _fetch(Gazette(), _ref("7", 2042), cache=tmp_path / "raw")
    assert first.upstream_metadata["from_cache"] is False
    again = Gazette()
    second, _ = await _fetch(again, _ref("7", 2042), cache=tmp_path / "raw")
    assert again.seen == []
    assert second.upstream_metadata["from_cache"] is True
    assert second.primary().content == first.primary().content
    assert second.fetched_at <= first.fetched_at


def test_the_client_is_polite_and_never_faster_than_the_gazette_allows() -> None:
    fast = SourceAdapter(kind="json_portal", name="dre", rate_limit_per_minute=600)
    acquirer = DiarioAcquirer(fast)
    assert isinstance(acquirer._client._transport, PoliteTransport)
    assert acquirer.politeness.rate_limit_per_minute == 40
    assert acquirer.politeness.concurrency == 1
    assert acquirer._client._transport.profile.user_agent == USER_AGENT
    assert USER_AGENT.startswith("Codify/")
    assert USER_AGENT.endswith(" (+https://github.com/TBI-AI-Incubator/codify)")


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


async def test_an_eli_and_detail_naming_different_acts_are_refused() -> None:
    ref = _ref(
        "4",
        2043,
        eli="https://data.dre.pt/eli/dec-lei/7/2042/03/02/p/dre",
        detail="decreto-lei/4-2043-900002",
    )
    with pytest.raises(ValueError, match="by https://data.dre.pt/eli/dec-lei/7/2042"):
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
