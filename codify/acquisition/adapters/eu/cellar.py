"""EU EUR-Lex Cellar adapter for CELEX retrieval and SPARQL discovery."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import ClassVar, Literal

import httpx
import structlog

from codify.acquisition.base import (
    AcquiredDocument,
    BaseAcquirer,
    Body,
    DiscoveryQuery,
    DocumentRef,
)
from codify.acquisition.politeness import make_polite_client
from codify.akn._schema import parse_xml
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter

CELLAR_BASE = "https://publications.europa.eu/resource/celex"
SPARQL_ENDPOINT = "http://publications.europa.eu/webapi/rdf/sparql"
DEFAULT_LICENCE = "EU-RU/2011/833"

logger = structlog.get_logger()

_FORMEX = "application/xml;type=fmx4"
# Old acts have an html manifestation, not xhtml; one header asks for either.
_XHTML = "application/xhtml+xml, text/html"

# CELEX, sector? + year + descriptor + number, optional corrigendum / suffix.
# Corrigenda resolve to the same FRBR work, distinct-expression handling deferred.
_CELEX_RE = re.compile(r"^(\d)?(\d{4})([A-Z]+)(\d+)(?:R\(\d+\)|[A-Z]\d{2})?$")
_DESCRIPTOR_TO_DOCTYPE = {
    "L": "directive",
    "R": "regulation",
    "D": "decision",
}


def celex_to_frbr(celex: str) -> tuple[str, str]:
    """Return (frbr_work_uri, doctype) for a CELEX. Raises on unknown shape."""
    m = _CELEX_RE.match(celex)
    if not m:
        raise ValueError(f"unrecognised CELEX: {celex!r}")
    _sector, year, descriptor, num = m.groups()
    doctype = _DESCRIPTOR_TO_DOCTYPE.get(descriptor, descriptor.lower())
    number = num.lstrip("0") or "0"
    return build_frbr_work_uri("eu", doctype, int(year), number), doctype


class EuCellarAcquirer(BaseAcquirer):
    """eurlex_cellar adapter."""

    JURISDICTION: ClassVar[str] = "eu"
    DEFAULT_RATE: ClassVar[int] = 60
    DEFAULT_BASE_URL: ClassVar[str] = CELLAR_BASE

    def __init__(
        self,
        adapter: SourceAdapter,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(adapter, client=client)
        self._client: httpx.AsyncClient = client or make_polite_client(adapter)

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        celex = ref.extra.get("celex")
        if not celex:
            raise ValueError("EU fetch requires DocumentRef.extra['celex']")
        languages = ref.languages or ["eng"]

        bodies: list[Body] = []
        etag: str | None = None
        last_modified: str | None = None

        formats: dict[str, str] = {}
        for lang in languages:
            xml, headers, media_type = await self._fetch_lang(celex, lang)
            formats[lang] = "html" if media_type == "text/html" else "formex"
            bodies.append(
                Body(
                    media_type=media_type,
                    content=xml.encode("utf-8"),
                    role="primary" if lang == languages[0] else "translation",
                    language=lang,
                )
            )
            etag = etag or headers.get("etag")
            last_modified = last_modified or headers.get("last-modified")

        frbr_work_uri, _ = celex_to_frbr(celex)
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=frbr_work_uri,
            expression_uri=f"{frbr_work_uri}/{languages[0]}@",
            bodies=bodies,
            source_url=f"{CELLAR_BASE}/{celex}",
            fetched_at=datetime.now(UTC),
            etag=etag,
            last_modified=last_modified,
            licence=DEFAULT_LICENCE,
            upstream_metadata={"celex": celex, "languages": languages, "formats": formats},
        )

    async def discover(self, query: DiscoveryQuery) -> AsyncIterator[DocumentRef]:
        sparql = _build_sparql(query)
        if sparql is None:
            return
        resp = await self._client.post(
            SPARQL_ENDPOINT,
            data={"query": sparql, "format": "application/sparql-results+json"},
            headers={"accept": "application/sparql-results+json"},
        )
        resp.raise_for_status()
        for binding in resp.json().get("results", {}).get("bindings", []):
            celex = binding.get("celex", {}).get("value")
            if not celex:
                continue
            try:
                _, doctype = celex_to_frbr(celex)
            except ValueError:
                continue
            yield DocumentRef(
                jurisdiction_code="eu",
                doctype=doctype,
                year=int(celex[1:5]),
                number=celex[6:].lstrip("0") or "0",
                languages=["eng"],
                extra={"celex": celex},
            )

    async def discover_directives(
        self,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
        in_force_only: bool = True,
        limit: int = 5000,
        offset: int = 0,
    ) -> list[tuple[DocumentRef, list[str]]]:
        """Return directives and their EUR-Lex directory codes."""
        sparql = _build_directives_sparql(
            year_from=year_from,
            year_to=year_to,
            in_force_only=in_force_only,
            limit=limit,
            offset=offset,
        )
        resp = await self._client.post(
            SPARQL_ENDPOINT,
            data={"query": sparql, "format": "application/sparql-results+json"},
            headers={"accept": "application/sparql-results+json"},
        )
        resp.raise_for_status()
        out: list[tuple[DocumentRef, list[str]]] = []
        for binding in resp.json().get("results", {}).get("bindings", []):
            celex = binding.get("celex", {}).get("value")
            if not celex:
                continue
            codes_str = binding.get("dirCodes", {}).get("value", "")
            codes = [c.strip() for c in codes_str.split(",") if c.strip()]
            try:
                _, doctype = celex_to_frbr(celex)
            except ValueError:
                continue
            ref = DocumentRef(
                jurisdiction_code="eu",
                doctype=doctype,
                year=int(celex[1:5]),
                number=celex[6:].lstrip("0") or "0",
                languages=["eng"],
                extra={"celex": celex},
            )
            out.append((ref, codes))
        return out

    async def _fetch_lang(
        self, celex: str, language: str
    ) -> tuple[str, dict[str, str], Literal["application/xml", "text/html"]]:
        """The act body for one language: FORMEX where Cellar has it, else the
        XHTML manifestation (older acts were never published as FORMEX)."""
        import asyncio

        branch_resp = await self._client.get(
            f"{CELLAR_BASE}/{celex}",
            headers={"accept": "application/xml; notice=branch", "accept-language": language},
        )
        branch_resp.raise_for_status()
        work_uri = _extract_work_uri(branch_resp.text)
        media_type: Literal["application/xml", "text/html"] = "application/xml"
        # One quick retry on 404 (Cellar occasionally races branch→work
        # propagation). Pre-1980 directives are real 404s; don't burn cycles.
        doc_resp = await self._client.get(
            work_uri, headers={"accept": _FORMEX, "accept-language": language}
        )
        if doc_resp.status_code == 404:
            await doc_resp.aclose()
            await asyncio.sleep(1)
            doc_resp = await self._client.get(
                work_uri, headers={"accept": _FORMEX, "accept-language": language}
            )
        if doc_resp.status_code == 404:
            await doc_resp.aclose()
            logger.info("cellar_formex_missing", celex=celex, language=language)
            doc_resp = await self._client.get(
                work_uri, headers={"accept": _XHTML, "accept-language": language}
            )
            media_type = "text/html"
        if doc_resp.status_code == 300:
            doc_resp = await self._follow_300(
                doc_resp, accept=_XHTML if media_type == "text/html" else _FORMEX
            )
        doc_resp.raise_for_status()
        # What came back decides the type: a FORMEX request can resolve to HTML.
        media_type = _media_type_of(doc_resp)
        # Cellar serves cache headers on the branch notice; doc stream sometimes lacks them.
        merged = {**dict(branch_resp.headers), **dict(doc_resp.headers)}
        for k in ("etag", "last-modified"):
            if not doc_resp.headers.get(k) and branch_resp.headers.get(k):
                merged[k] = branch_resp.headers[k]
        return doc_resp.text, merged, media_type

    async def _follow_300(self, response: httpx.Response, *, accept: str) -> httpx.Response:
        """Pick the act-body stream from a Cellar 300 Multiple-Choice response.

        Cellar publishes each act as a numbered set of streams `DOC_1 / DOC_2
        / ... / DOC_N`. The convention is:
          - `DOC_1` is the OJ-issue index notice (`<DOC>` root, ~2KB, no articles)
          - `DOC_2` is the act body (`<ACT>` root, the legislative content)
          - `DOC_3..N` are annexes (`<ANNEX>` root)

        Strategy: probe each stream in the format asked for, pick the first one
        that looks like an act (a FORMEX act root, or an HTML document when XHTML
        was asked for). Fall back to the largest stream.
        """
        items = _parse_300_choices(response.text)
        await response.aclose()
        if not items:
            raise RuntimeError("no DOC_n streams in 300 Multiple-Choice response")

        want_html = accept.startswith(_XHTML)
        candidates: list[tuple[httpx.Response, str]] = []
        for href, _name in items:
            try:
                resp = await self._client.get(href, headers={"accept": accept})
                resp.raise_for_status()
            except Exception:  # noqa: BLE001, S112
                continue
            head = resp.text[:300]
            if want_html:
                if re.search(r"<(!doctype\s+)?html", head, re.IGNORECASE):
                    return resp
                candidates.append((resp, "html?"))
                continue
            m = re.search(r"<([A-Z][A-Z0-9._]*)", head.split(">", 1)[1] if ">" in head else head)
            root_name = m.group(1) if m else ""
            if root_name in _ACT_ROOTS:
                # First act-rooted stream wins.
                return resp
            candidates.append((resp, root_name))

        # Nothing looked like an act body: return the largest stream, close the rest.
        if not candidates:
            raise RuntimeError("no usable streams in 300 Multiple-Choice response")
        candidates.sort(key=lambda r: len(r[0].text), reverse=True)
        for resp, _ in candidates[1:]:
            await resp.aclose()
        return candidates[0][0]


def _media_type_of(response: httpx.Response) -> Literal["application/xml", "text/html"]:
    """HTML by the content-type, or by the body when Cellar labels it generically."""
    ctype = response.headers.get("content-type", "").lower()
    if "html" in ctype:
        return "text/html"
    if "xml" in ctype:
        return "application/xml"
    return (
        "text/html"
        if re.search(r"<(!doctype\s+)?html", response.text[:300], re.I)
        else "application/xml"
    )


# Roots that represent legislative-act content (vs OJ index, annex, etc).
_ACT_ROOTS = frozenset({"ACT", "REG", "DEC", "DIR", "CONS_ACT", "CONS_TEXT"})


def _parse_300_choices(body: str) -> list[tuple[str, str]]:
    """Extract (href, stream_name) pairs from a Cellar 300-Multiple-Choice body.

    Cellar serves RDF/XML for the choice listing. We parse it as XML and
    walk the tree, falling back to a permissive regex on parse failure
    (some endpoints emit slightly malformed XML around DOC_n entries).
    """
    try:
        root = parse_xml(body)
    except Exception:  # noqa: BLE001
        return _regex_300_fallback(body)

    out: list[tuple[str, str]] = []
    # Walk every element; collect those whose href attribute contains /DOC_N
    # together with the nearest descendant text matching the stream name.
    for el in root.iter():
        href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href") or ""
        if not _DOC_N_RE.search(href):
            continue
        # Stream name is the text content of the same element or a child.
        name = (el.text or "").strip() or _first_text(el).strip()
        if href and name:
            out.append((href, name))
    if out:
        return out
    return _regex_300_fallback(body)


def _first_text(el: object) -> str:
    for child in getattr(el, "iter", lambda: [])():
        text = getattr(child, "text", None)
        if text and text.strip():
            return str(text)
    return ""


_DOC_N_RE = re.compile(r"/DOC_\d+")


def _regex_300_fallback(body: str) -> list[tuple[str, str]]:
    return re.findall(
        r'href="([^"]+/DOC_\d+)"[^<]*<.*?stream_name">([^<]+)<',
        body,
        re.DOTALL,
    )


def _extract_work_uri(branch_xml: str) -> str:
    root = parse_xml(branch_xml)
    work = root.find(".//WORK")
    if work is None:
        raise RuntimeError("WORK element missing from branch notice")
    uri_el = work.find(".//URI/VALUE")
    if uri_el is None or not uri_el.text:
        raise RuntimeError("WORK URI missing from branch notice")
    text = str(uri_el.text)
    if text.startswith("http://"):
        text = "https://" + text[len("http://") :]
    return text


def _build_sparql(query: DiscoveryQuery) -> str | None:
    """Minimal SPARQL, sector 3 (legislation), year window, in-force."""
    where = [
        "?work cdm:resource_legal_id_celex ?celex .",
        'FILTER(STRSTARTS(?celex, "3"))',
    ]
    if query.year_from:
        where.append(f'FILTER(SUBSTR(?celex, 2, 4) >= "{query.year_from}")')
    if query.year_to:
        where.append(f'FILTER(SUBSTR(?celex, 2, 4) <= "{query.year_to}")')
    limit = query.limit or 100
    return (
        "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
        "SELECT DISTINCT ?celex WHERE {\n  " + "\n  ".join(where) + f"\n}}\nLIMIT {limit}\n"
    )


def _build_directives_sparql(
    *,
    year_from: int | None,
    year_to: int | None,
    in_force_only: bool,
    limit: int,
    offset: int,
) -> str:
    """SPARQL: in-force directives with their EUR-Lex directory codes.

    CELEX descriptor 'L' = directive (the 6th character of CELEX, e.g.
    32016L0943 = directive 943 of 2016, sector 3 = legislation).
    """
    where = [
        "?work cdm:resource_legal_id_celex ?celex .",
        'FILTER(STRSTARTS(STR(?celex), "3"))',
        'FILTER(SUBSTR(STR(?celex), 6, 1) = "L")',
        "OPTIONAL {",
        "  ?work cdm:resource_legal_is_about_concept_directory-code ?dirConcept .",
        "  ?dirConcept skos:prefLabel ?dirCode .",
        '  FILTER(LANG(?dirCode) = "en")',
        "}",
    ]
    if in_force_only:
        where.append('?work cdm:resource_legal_in-force "true"^^xsd:boolean .')
    if year_from:
        where.append(f'FILTER(SUBSTR(STR(?celex), 2, 4) >= "{year_from}")')
    if year_to:
        where.append(f'FILTER(SUBSTR(STR(?celex), 2, 4) <= "{year_to}")')
    return (
        "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
        "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
        'SELECT ?celex (GROUP_CONCAT(DISTINCT ?dirCode; SEPARATOR=",") AS ?dirCodes)\n'
        "WHERE {\n  " + "\n  ".join(where) + "\n}\n"
        "GROUP BY ?celex\n"
        "ORDER BY ?celex\n"
        f"LIMIT {limit} OFFSET {offset}\n"
    )


__all__ = ["EuCellarAcquirer", "celex_to_frbr"]
