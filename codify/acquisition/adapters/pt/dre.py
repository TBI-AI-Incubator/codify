"""PT: the Diário da República, read through the public calls its own pages make.
An act resolves from its ELI or detail path; its consolidated text, else its printed
text, becomes AKN named by the `pt` config's FRBR patterns, its work ELI an alias."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from codify.acquisition.adapters.pt.dre_akn import (
    REPEALED,
    fragments,
    from_printed,
    to_akn,
    to_bluebell,
)
from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DocumentRef,
    GazetteRef,
)
from codify.acquisition.index import index_path, write_index
from codify.acquisition.politeness import make_polite_client
from codify.frbr import build_frbr_expression_uri, build_frbr_work_uri
from codify.jurisdictions import SourceAdapter

try:
    _RELEASE = version("codify-core")
except PackageNotFoundError:
    _RELEASE = "0"

USER_AGENT = f"Codify/{_RELEASE} (+https://github.com/TBI-AI-Incubator/codify)"

ELI_BASE = "https://data.dre.pt/eli"
GAZETTE_NAME = "Diário da República"
# The anonymous token the site's own pages send.
CSRF_TOKEN = "T6C+9iB49TLra4jEsMeSckDMNhQ="  # noqa: S105  (public, not a secret)
DETAIL = (
    "Legislacao_Conteudos/Conteudo_Detalhe/DataActionGetAllConteudoDetalheData",
    "B3l5BYZxaLmWUxGR3tmKGw",
    "Legislacao_Conteudos.Conteudo_Detalhe",
)
FRAG = (
    "LegislacaoConsolidada/LegCons_Detalhe/DataActionGetDiplomaFragByIdAndApplicationSetting",
    "nc_pvjc74SJq_vOvUM4nCg",
    "LegislacaoConsolidada.LegCons_Detalhe",
)
DATA = (
    "LegislacaoConsolidada/LegCons_Detalhe/DataActionGetData",
    "0J5toCe4aZFDi88AoH0G0Q",
    "LegislacaoConsolidada.LegCons_Detalhe",
)
DOCTYPES = {"Lei": "lei", "Decreto-Lei": "dl", "Lei Orgânica": "lo", "Portaria": "portaria"}
ELI_TYPES = {"lei": "lei", "dl": "dec-lei", "portaria": "port"}

_ELI = re.compile(r"/eli/([a-z-]+)/(\d+(?:-[a-z]+)?)/(\d{4})(/\d{2}/\d{2})?")
_CONS_PATH = re.compile(r"/dr/legislacao-consolidada/([a-z-]+)/(\d{4})-(\d+)")
_DETAIL_PATH = re.compile(r"/dr/detalhe/([a-z-]+/[0-9a-z-]+)")
_NUMBER = re.compile(r"^(\d+(?:-[A-Z]+)?)/(\d{2}|\d{4})(?:/\d+)?$")
_REDIRECTS = {301, 302, 303, 307, 308}


logger = structlog.get_logger()


def writable_dir(path: Path) -> Path | None:
    """`path`, created if need be, when files can be written there; otherwise None."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path if os.access(path, os.W_OK) else None


class DrItemMissing(LookupError):
    """The gazette answered no act, or no text, for this ref."""


class SiteChanged(RuntimeError):
    """The site's calls changed version; the tokens above need refreshing."""


Action = tuple[str, str, str]


def number_and_year(numero: str, published: str) -> tuple[str, str]:
    """`102-D/2020` → (102-D, 2020); `129/98` takes its year from publication; a
    series after the year (`320/2026/1`) is no part of the number."""
    m = _NUMBER.match(numero.strip().upper())
    if m is None:
        raise DrItemMissing(f"unread act number {numero!r}")
    year = m.group(2) if len(m.group(2)) == 4 else published[:4]
    return m.group(1), year


def work_eli(eli: str) -> str:
    """The work ELI (…/p/dre) from any of its language or format forms."""
    return re.sub(r"/pt(?:/html)?/?$", "", eli.replace("http://", "https://").rstrip("/"))


class DiarioAcquirer(BaseAcquirer):
    JURISDICTION: ClassVar[str] = "pt"
    DEFAULT_BASE_URL: ClassVar[str] = "https://diariodarepublica.pt"
    DEFAULT_RATE: ClassVar[int] = 40
    _CONCURRENCY: ClassVar[int] = 1
    _client: httpx.AsyncClient

    def __init__(
        self,
        adapter: SourceAdapter,
        *,
        client: httpx.AsyncClient | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        super().__init__(adapter, client=client)
        # Never faster than one request each 1.5 s, whatever the config asks.
        rate = min(self.politeness.rate_limit_per_minute or self.DEFAULT_RATE, self.DEFAULT_RATE)
        self.politeness = self.politeness.model_copy(
            update={"rate_limit_per_minute": rate, "user_agent": USER_AGENT}
        )
        self._client = client or make_polite_client(
            adapter, user_agent=USER_AGENT, profile=self.politeness
        )
        self._licence = adapter.licence
        self._cache = cache_dir
        self._module_version: str | None = None
        # When each answer read from the cache was saved.
        self._read_at: dict[str, datetime] = {}

    @classmethod
    def from_config(cls, jurisdiction_code: str, adapter: object) -> DiarioAcquirer:
        """As configured, reading saved responses before the network when the cache is writable."""
        if jurisdiction_code != cls.JURISDICTION:
            raise ValueError(
                f"DiarioAcquirer only supports {cls.JURISDICTION!r}, got {jurisdiction_code!r}"
            )
        assert isinstance(adapter, SourceAdapter)
        raw = index_path(cls.JURISDICTION).parent / "raw"
        cache = writable_dir(raw)
        if cache is None:
            logger.warning("dre_cache_disabled", path=str(raw), reason="not writable")
        return cls(adapter, cache_dir=cache)

    def _cache_path(self, key: str) -> Path | None:
        if self._cache is None:
            return None
        return self._cache / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"

    async def _cached(
        self,
        key: str,
        get: Callable[[], Awaitable[dict[str, Any]]],
        keep: Callable[[dict[str, Any]], bool] = lambda _: True,
    ) -> dict[str, Any]:
        # shortcut: a saved answer is never refreshed; delete the file to refetch.
        path = self._cache_path(key)
        if path is not None and path.exists():
            self._read_at[key] = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            return dict(json.loads(path.read_text()))
        value = await get()
        # Only a positive answer is saved: an error page may be passing.
        if path is not None and keep(value):
            path.parent.mkdir(parents=True, exist_ok=True)
            write_index(path, value)
        return value

    async def _resolve(self, url: str) -> str:
        """Where an ELI lands on the site, following redirects without fetching the page."""

        async def go() -> dict[str, Any]:
            here = url
            for _ in range(5):
                resp = await self._client.get(here, follow_redirects=False)
                if resp.status_code not in _REDIRECTS or "location" not in resp.headers:
                    if resp.status_code not in (404, 410):
                        resp.raise_for_status()
                    break
                here = urljoin(here, resp.headers["location"])
                if urlparse(here).path.startswith("/dr/"):
                    break
            return {"final": here}

        def landed(v: dict[str, Any]) -> bool:
            return bool(_CONS_PATH.search(v["final"]) or _DETAIL_PATH.search(v["final"]))

        return str((await self._cached(f"GET {url}", go, landed))["final"])

    async def _version(self) -> str:
        if self._module_version is None:
            resp = await self._client.get(f"{self._base}/dr/moduleservices/moduleversioninfo")
            resp.raise_for_status()
            self._module_version = str(resp.json()["versionToken"])
        return self._module_version

    async def _call(
        self,
        action: Action,
        variables: dict[str, Any],
        key: str,
        keep: Callable[[dict[str, Any]], bool] = lambda _: True,
    ) -> dict[str, Any]:
        path, api, view = action

        async def go() -> dict[str, Any]:
            body = {
                "versionInfo": {"moduleVersion": await self._version(), "apiVersion": api},
                "viewName": view,
                "screenData": {"variables": variables},
            }
            resp = await self._client.post(
                f"{self._base}/dr/screenservices/dr/{path}",
                json=body,
                headers={"X-CSRFToken": CSRF_TOKEN},
            )
            resp.raise_for_status()
            try:
                out = resp.json()
            except ValueError as exc:
                raise DrItemMissing(f"{path} answered no JSON") from exc
            if (out.get("versionInfo") or {}).get("hasApiVersionChanged"):
                raise SiteChanged(f"{path}: the site reports a new API version")
            if out.get("exception") or not isinstance(out.get("data"), dict):
                raise DrItemMissing(f"{path}: {out.get('exception')!r}")
            return dict(out["data"])

        return await self._cached(key, go, keep)

    async def _detail(self, link: str) -> dict[str, Any]:
        tipo, key = link.split("/", 1)
        data = await self._call(
            DETAIL,
            {"Tipo": tipo, "Key": key},
            f"detail {link}",
            lambda d: bool((d.get("DetalheConteudo") or {}).get("Id")),
        )
        detail = data.get("DetalheConteudo")
        if not isinstance(detail, dict) or not detail.get("Id"):
            raise DrItemMissing(f"no act at {link!r}")
        return detail

    async def _consolidated(
        self, tipo: str, ano: str, frag_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        today = date.today().isoformat()
        v: dict[str, Any] = {
            "DiplomaFragId": frag_id,
            "Ano": int(ano),
            "Tipo": tipo,
            "Key": f"{ano}-{frag_id}",
            "DataSelecionada": today,
        }
        frag = await self._call(FRAG, v, f"frag {tipo}/{ano}-{frag_id}")
        legis = frag["ConsolidadaConteudoDetalhe"]["DiplomaFrag"]["DiplomaLegisId"]
        v |= {"DiplomaLegisId": legis, "GetDiplomaFragByIdAndApplicationSetting": frag}
        key = f"data {tipo}/{ano}-{frag_id}"
        data = await self._call(
            DATA, v, key, lambda d: bool((d.get("LegConsBase") or {}).get("List"))
        )
        return frag, data, key

    def consolidated_eli(self, ref: DocumentRef) -> str:
        """The undated ELI a ref's class, number and year name, for classes that have one."""
        eli_type = ELI_TYPES.get(ref.doctype)
        if eli_type is None:
            raise ValueError(
                f"{ref.doctype!r} has no ELI type here; pass extra['eli'] or extra['detail']"
            )
        return f"{ELI_BASE}/{eli_type}/{ref.number.lower()}/{ref.year}/p/cons"

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        """The current text (`as_of="latest"`), or the text as printed (`"enacted"`)."""
        if ref.as_of not in ("latest", "enacted"):
            raise ValueError(f"the gazette's consolidation is read as of today, not {ref.as_of}")
        eli = ref.extra.get("eli", "").replace("http://", "https://").rstrip("/")
        given = ref.extra.get("detail", "")
        link = dm.group(1) if (dm := _DETAIL_PATH.search(given)) else given.strip("/")
        detail = await self._detail(link) if link and not eli else None
        if not eli:
            eli = str((detail or {}).get("ELI") or "") or self.consolidated_eli(ref)
        m = _ELI.search(eli)
        if m is None:
            raise ValueError(f"not a Diário da República ELI: {eli!r}")
        eli_type, eli_num, eli_year, dated = m.groups()

        cons: dict[str, Any] | None = None
        data_key = ""
        # The enacted text needs the consolidation only to find its detail page.
        current = ref.as_of == "latest" or not (link or dated)
        cons_eli = f"{ELI_BASE}/{eli_type}/{eli_num}/{eli_year}/p/cons/pt/html"
        landed = await self._resolve(cons_eli) if current else ""
        if cm := _CONS_PATH.search(landed):
            frag, data, data_key = await self._consolidated(*cm.groups())
            frags = fragments(data)
            if frags and ref.as_of == "latest":
                last = str(data.get("DataUltimaConsolidada") or "")[:10]
                cons = {
                    "url": landed,
                    "date": last
                    if last > "1900-01-01"
                    else max((f["version_date"] for f in frags), default=""),
                    "in_update": bool(
                        frag["ConsolidadaConteudoDetalhe"]["DiplomaFrag"].get("EmAtualizacao")
                    ),
                    "fragments": frags,
                    "raw": data,
                    "legis_id": str(
                        frag["ConsolidadaConteudoDetalhe"]["DiplomaFrag"]["DiplomaLegisId"]
                    ),
                }
            sitemap = str(
                frag["ConsolidadaConteudoDetalhe"]["DiplomaLegis"].get("LinkSitemap") or ""
            )
            if not link and (dm := _DETAIL_PATH.search(sitemap)):
                link = dm.group(1)
        if not link:
            if not dated:
                raise DrItemMissing(
                    f"{eli} has no consolidation; a dated ELI or detail path names the printed text"
                )
            dm = _DETAIL_PATH.search(await self._resolve(f"{work_eli(eli)}/pt/html"))
            if dm is None:
                raise DrItemMissing(f"{eli} resolves to no act")
            link = dm.group(1)
        detail = detail or await self._detail(link)

        if cons and cons["legis_id"] != str(detail.get("Id")):
            raise ValueError(f"the consolidation at {cons['url']} is not the act at {link}")
        source_type = str(detail.get("TipoDiploma") or "")
        doctype = DOCTYPES.get(source_type)
        if doctype != ref.doctype:
            raise ValueError(f"ref asks for {ref.doctype!r}; the gazette types it {source_type!r}")
        published = str(detail.get("DataPublicacao") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
            raise DrItemMissing(f"{link} has no publication date")
        number, year = number_and_year(str(detail.get("Numero") or ""), published)
        named = {(ref.number.upper(), str(ref.year)), (eli_num.upper(), eli_year)}
        if named != {(number, year)}:
            raise ValueError(
                f"ref asks for {ref.number}/{ref.year} by {eli}; {link} is {number}/{year}"
            )
        work = build_frbr_work_uri(self.JURISDICTION, doctype, year, number)
        as_of = (cons["date"] if cons else "") or published
        expression = build_frbr_expression_uri(work, "por", as_of)
        printed = str(detail.get("Texto") or "")
        title = " ".join(str(detail.get("Titulo") or "").split())
        alias = work_eli(str(detail.get("ELI") or ""))
        issue = str((detail.get("DiarioRepublica") or {}).get("Numero") or "")
        publication = {
            "date": published,
            "name": GAZETTE_NAME,
            "showAs": " ".join(str(detail.get("Publicacao") or GAZETTE_NAME).split()),
        } | ({"number": issue} if issue else {})

        def convert() -> str:
            frags = cons["fragments"] if cons else from_printed(printed)
            return to_akn(
                to_bluebell(frags, printed=printed),
                number=number,
                work=work,
                published=published,
                expression=expression,
                as_of=as_of,
                eli=alias,
                title=title,
                publication=publication,
            )

        from codify.pipeline.formats.eu_directive import on_the_cpu_pool

        # Bluebell parsing is CPU-bound; it runs on the pipeline's pool, not the loop.
        akn = str(await on_the_cpu_pool(convert))
        detail_url = f"{self._base}/dr/detalhe/{link}"
        saved = [t for k in (f"detail {link}", data_key) if (t := self._read_at.get(k))]
        upstream: dict[str, Any] = {
            "dr_id": str(detail.get("Id") or ""),
            "eli": alias,
            "detail_url": detail_url,
            "text_source": "consolidated" if cons else "printed",
            "consolidation_date": cons["date"] if cons else "",
            "consolidated_url": cons["url"] if cons else "",
            "consolidated_eli": (
                f"{ELI_BASE}/{eli_type}/{eli_num}/{eli_year}/p/cons/{as_of.replace('-', '')}"
                if cons
                else ""
            ),
            "consolidation_in_update": cons["in_update"] if cons else False,
            "repealed_fragments": (
                sum(f["state"] == REPEALED for f in cons["fragments"]) if cons else 0
            ),
            "series": str(detail.get("Serie") or ""),
            "supplement": str(detail.get("Suplemento") or ""),
            "issuer": str(detail.get("Emissor") or ""),
            "in_force": str(detail.get("Vigencia") or ""),
            "pdf": str(detail.get("URL_PDF") or ""),
            "from_cache": bool(saved),
        }
        card = {"detail": detail, "consolidation": cons["raw"] if cons else None}
        pages = str(detail.get("Pagina") or "").split("-")[0].strip()
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=work,
            expression_uri=expression,
            bodies=[
                Body(media_type="application/xml", content=akn.encode(), language="por"),
                # The source answers themselves, so the conversion can be audited.
                Body(
                    media_type="application/json",
                    content=json.dumps(card, ensure_ascii=False).encode(),
                    role="card",
                ),
            ],
            source_url=cons["url"] if cons else detail_url,
            # Saved answers are as old as the crawl that saved them.
            fetched_at=min(saved) if saved else datetime.now(UTC),
            licence=self._licence,
            citation_alt=title or None,
            gazette_ref=GazetteRef(
                name=GAZETTE_NAME,
                year=int(published[:4]),
                issue=issue or "",
                page=pages or None,
                date=published,
            ),
            upstream_metadata=upstream,
        )


__all__ = [
    "CSRF_TOKEN",
    "DiarioAcquirer",
    "DrItemMissing",
    "ELI_BASE",
    "GAZETTE_NAME",
    "SiteChanged",
    "USER_AGENT",
    "number_and_year",
    "work_eli",
]
