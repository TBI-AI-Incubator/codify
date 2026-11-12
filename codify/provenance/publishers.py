"""The publisher registry: URL builders, URL parsers and per-pattern verification flags.

A builder returns None where the publisher offers no such page. Fragments are only
emitted for a URL pattern whose flag in `PATTERN_VERIFIED` is set.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, parse_qs, quote, unquote, urlsplit

from pydantic import BaseModel, ConfigDict

from codify.frbr import UK_TYPE_TOKENS


class OfficialRef(BaseModel):
    """A publisher's own ids for an acquired text; ISO date string, so it travels in run params.

    `edition_id` names this exact text where the publisher has such an id, for example a
    language code where the same instrument is published in several.
    """

    model_config = ConfigDict(extra="forbid")

    publisher: str
    work_id: str
    edition_id: str | None = None
    edition_date: str | None = None


@dataclass(frozen=True)
class ParsedUrl:
    """What an official URL names. `lookup` holds the ids to try against `official_ids`."""

    publisher: str
    work_id: str | None = None
    edition_id: str | None = None
    fragment: str | None = None
    lookup: tuple[str, ...] = ()


@dataclass(frozen=True)
class Publisher:
    key: str
    name: str
    hosts: tuple[str, ...]
    parse: Callable[[SplitResult], ParsedUrl | None]
    act: Callable[[str, str | None], str | None]
    edition: Callable[[str, str], str | None] | None = None
    current: Callable[[str, str | None], str | None] | None = None
    # (work id, edition id, fragment) to a URL, and the pattern that URL belongs to.
    fragment: Callable[[str, str | None, str], str | None] | None = None
    fragment_pattern: Callable[[str], str] | None = None
    # An eId to the publisher's fragment, where it can be derived; None where it cannot.
    eid_fragment: Callable[[str], str | None] | None = None

    def act_url(self, work_id: str, edition_id: str | None = None) -> str | None:
        """The act page, or None until the pattern is verified."""
        return self.act(work_id, edition_id) if is_verified(f"{self.key}.act") else None

    def edition_url(self, work_id: str, edition_id: str) -> str | None:
        if self.edition is None or not is_verified(f"{self.key}.edition"):
            return None
        return self.edition(work_id, edition_id)

    def current_url(self, work_id: str, edition_id: str | None = None) -> str | None:
        if self.current is None or not is_verified(f"{self.key}.current"):
            return None
        return self.current(work_id, edition_id)

    def page_url(
        self, work_id: str, edition_id: str | None = None
    ) -> tuple[str, Literal["edition", "act"]] | None:
        """The best verified page: the edition's, else the act's, else no link."""
        if edition_id and (url := self.edition_url(work_id, edition_id)):
            return url, "edition"
        url = self.act_url(work_id, edition_id)
        return (url, "act") if url else None

    def fragment_for_eid(self, eid: str) -> str | None:
        return self.eid_fragment(eid) if self.eid_fragment else None

    def provision_url(self, work_id: str, edition_id: str | None, fragment: str) -> str | None:
        """The URL of one provision, or None until its pattern is verified."""
        if self.fragment is None:
            return None
        pattern = (
            self.fragment_pattern(fragment) if self.fragment_pattern else f"{self.key}.fragment"
        )
        return self.fragment(work_id, edition_id, fragment) if is_verified(pattern) else None


#: Pattern key to verified flag. Set a key to True once a sample link has been checked.
PATTERN_VERIFIED: dict[str, bool] = {
    "riigi-teataja.act": False,
    "riigi-teataja.edition": False,
    "riigi-teataja.current": False,
    "riigi-teataja.fragment": False,
    "legislation-gov-uk.act": False,
    "legislation-gov-uk.edition": False,
    "legislation-gov-uk.current": False,
    **{
        f"legislation-gov-uk.fragment.{kind}.{depth}": False
        for kind in ("section", "article", "regulation", "rule", "schedule")
        for depth in (1, 2, 3)
    },
    "eur-lex.act": False,
    "eur-lex.edition": False,
    "eur-lex.fragment": False,
    "oecd-compendium.act": False,
    "oecd-compendium.edition": False,
    "zakon-rada.act": False,
    "zakon-rada.edition": False,
    "zakon-rada.current": False,
    "zakon-rada.fragment": False,
    "qbz.act": False,
    "ocs.act": False,
    "ocs.edition": False,
    "ocs.current": False,
    "dre.act": False,
    "dre.edition": False,
    "dre.current": False,
    "boe.act": False,
    "gaceta-oficial-do.act": False,
}


def is_verified(pattern: str) -> bool:
    return PATTERN_VERIFIED.get(pattern, False)


def _host(netloc: str) -> str:
    return netloc.lower().removeprefix("www.")


# Riigi Teataja

_RT = "https://www.riigiteataja.ee"


def _rt_page(edition_id: str | None, suffix: str = "") -> str | None:
    return f"{_RT}/akt/{edition_id}{suffix}" if edition_id else None


_RT_PATH = re.compile(r"^/(?:en/)?akt/([0-9A-Za-z-]+?)(?:\.(?:xml|html))?/?$")


def _rt_parse(u: SplitResult) -> ParsedUrl | None:
    m = _RT_PATH.match(u.path)
    if m is None:
        return None
    fragment = u.fragment.lstrip("!") or None
    return ParsedUrl("riigi-teataja", None, m[1], fragment, (m[1],))


# legislation.gov.uk

_GB = "https://www.legislation.gov.uk"
_GB_EDITION = re.compile(r"\d{4}-\d{2}-\d{2}|enacted|made|created|adopted")
_GB_PATH = re.compile(
    r"^/(?:id/)?(?P<type>[a-z]+)/(?P<year>\d{4}|[A-Z][A-Za-z0-9]*/\d+(?:-\d+)?)/(?P<num>\d+)"
    r"(?P<rest>(?:/[^/]+)*)/?$"
)


def _gb_parse(u: SplitResult) -> ParsedUrl | None:
    m = _GB_PATH.match(u.path)
    if m is None or m["type"] not in UK_TYPE_TOKENS:
        return None
    work = f"{m['type']}/{m['year']}/{m['num']}"
    segments = [s for s in m["rest"].split("/") if s]
    if segments and segments[-1].startswith("data."):
        segments.pop()
    edition = next((s for s in segments if _GB_EDITION.fullmatch(s)), None)
    provision = [s for s in segments if s != edition and s not in ("welsh", "contents")]
    fragment = u.fragment or "/".join(provision) or None
    return ParsedUrl("legislation-gov-uk", work, edition, fragment, (work,))


def _gb_page(work: str, *parts: str | None) -> str:
    return "/".join([f"{_GB}/{work}", *(p for p in parts if p)])


def _gb_fragment(work: str, edition: str | None, fragment: str) -> str:
    return _gb_page(work, fragment, edition)


def _gb_fragment_pattern(fragment: str) -> str:
    """Keyed by kind and depth: `section/13` is depth 1, `section/13/2` depth 2, deeper is 3."""
    kind, *rest = fragment.split("/")
    return f"legislation-gov-uk.fragment.{kind}.{min(len(rest), 3)}"


_GB_PROVISIONS = ("section", "article", "regulation", "rule", "schedule")


def _gb_eid_fragment(eid: str) -> str | None:
    path = re.sub(r"_dup\d+$", "", eid).replace("-", "/")
    return path if path.split("/", 1)[0] in _GB_PROVISIONS else None


# EUR-Lex

_EUR = "https://eur-lex.europa.eu"
_CELEX = re.compile(r"(?:3|0)\d{4}[A-Z]+\d+", re.I)
_SECTOR3 = re.compile(r"3\d{4}[A-Z]+\d+(?:\(\d+\))?")
_CONSOLIDATED = re.compile(r"^0(\d{4}[A-Z]+\d+)-(\d{4})(\d{2})(\d{2})$")
_ELI_TYPE = {"reg": "R", "dir": "L", "dec": "D"}


def _celex_ids(celex: str) -> tuple[str, str | None]:
    """The work's sector-3 CELEX and, for a consolidated text, its own CELEX."""
    celex = celex.upper()
    m = _CONSOLIDATED.match(celex)
    if m:
        return f"3{m[1]}", celex
    base = _SECTOR3.match(celex)
    return (base[0] if base else celex), None


def _eu_eid_fragment(eid: str) -> str | None:
    """The last article of an eId; none for an annex, quoted amending text or a duplicate."""
    parts = eid.split("__")
    if any(p.startswith(("att_", "mod_", "qstr_")) or "_dup" in p for p in parts):
        return None
    articles = [p for p in parts if re.fullmatch(r"art_\d+", p)]
    return articles[-1] if articles else None


def _eur_parse(u: SplitResult) -> ParsedUrl | None:
    celex: str | None = None
    if m := re.match(r"^/resource/celex/([0-9A-Za-z()-]+)", u.path):
        celex = m[1]
    elif m := re.match(r"^/eli/(reg|dir|dec)/(\d{4})/(\d+)", u.path):
        celex = f"3{m[2]}{_ELI_TYPE[m[1]]}{int(m[3]):04d}"
    else:
        uri = parse_qs(u.query).get("uri", [""])[0]
        if uri.upper().startswith("CELEX:"):
            celex = uri[6:]
    if celex is None or not _CELEX.match(celex):
        return None
    work, edition = _celex_ids(celex)
    return ParsedUrl("eur-lex", work, edition, u.fragment or None, (work,))


# OECD Legal Instruments

_OECD = "https://legalinstruments.oecd.org"
_OECD_PATH = re.compile(r"^/(en|fr)/instruments/(OECD-LEGAL-\d+)/?$", re.I)


def _oecd_url(work_id: str, language: str) -> str:
    return f"{_OECD}/{language}/instruments/{work_id.replace('/', '-')}"


def _oecd_parse(u: SplitResult) -> ParsedUrl | None:
    m = _OECD_PATH.match(u.path)
    if m is None:
        return None
    work = m[2].upper().replace("-", "/")
    return ParsedUrl("oecd-compendium", work, m[1].lower(), None, (work,))


# Verkhovna Rada

_RADA = "https://zakon.rada.gov.ua"
_RADA_PATH = re.compile(
    r"^/laws/show/(?:(?P<lang>en)/)?(?P<nreg>.+?)(?:/(?P<ed>ed\d{8}))?"
    r"(?:/(?:page\d*|print|card))?/?$"
)


def _rada_parse(u: SplitResult) -> ParsedUrl | None:
    if m := re.match(r"^/go/([^/]+)/?$", u.path):
        nreg, edition = unquote(m[1]), None
    elif m := _RADA_PATH.match(u.path):
        nreg = unquote(m["nreg"])
        edition = "/".join(x for x in (m["lang"], m["ed"]) if x) or None
    else:
        return None
    return ParsedUrl("zakon-rada", nreg, edition, u.fragment or None, (nreg,))


def _rada_page(work_id: str, edition_id: str | None) -> str:
    """The page for an edition id made of an optional `en` and an optional `ed` date."""
    parts = (edition_id or "").split("/")
    lang = "en/" if "en" in parts else ""
    ed = next((f"/{p}" for p in parts if p.startswith("ed")), "")
    return f"{_RADA}/laws/show/{lang}{quote(work_id, safe='/')}{ed}"


# QBZ (Albania)

_QBZ = "https://qbz.gov.al"
_QBZ_PATH = re.compile(r"^/eli/(fz/\d{4}/\d+)(?:/.*)?$")


def _qbz_parse(u: SplitResult) -> ParsedUrl | None:
    m = _QBZ_PATH.match(u.path)
    return ParsedUrl("qbz", m[1], None, None, (m[1],)) if m else None


# Council of State (Thailand)

_OCS = "https://searchlaw.ocs.go.th"
_OCS_FRAGMENT = re.compile(r"^/public/doc/(.+)$")


def _ocs_parse(u: SplitResult) -> ParsedUrl | None:
    m = _OCS_FRAGMENT.match(u.fragment)
    if u.path.rstrip("/") != "/council-of-state" or m is None:
        return None
    return ParsedUrl("ocs", None, m[1], None, (m[1],))


def _ocs_edition(work_id: str, edition_id: str) -> str:
    return f"{_OCS}/council-of-state/#/public/doc/{edition_id}"


# Diário da República (Portugal)

_DRE = "https://data.dre.pt/eli"
_DRE_PATH = re.compile(
    r"^/eli/(?P<work>[a-z-]+/[0-9A-Za-z-]+/\d{4}(?:/\d{2}/\d{2})?)/p/(?P<kind>dre|cons)(?:/(?P<date>\d{8}))?(?:/pt(?:/html)?)?/?$"
)


def _dre_work(work: str) -> str:
    """The undated work: consolidated texts are addressed without the printed date."""
    return re.sub(r"/\d{2}/\d{2}$", "", work)


def _dre_page(work: str, *parts: str | None) -> str:
    return "/".join([f"{_DRE}/{_dre_work(work)}/p/cons", *(p for p in parts if p)])


def _dre_parse(u: SplitResult) -> ParsedUrl | None:
    m = _DRE_PATH.match(u.path)
    if m is None:
        return None
    work = _dre_work(m["work"])
    return ParsedUrl("dre", work, m["date"], None, (work,))


# Boletín Oficial del Estado (Spain)

_BOE = "https://www.boe.es"
_BOE_ELI = re.compile(r"^/eli/(es(?:-[a-z]{2})?/[a-z]+/\d{4}/\d{2}/\d{2}/[^/]+)(?:/.*)?$")


def _boe_parse(u: SplitResult) -> ParsedUrl | None:
    m = _BOE_ELI.match(u.path)
    return ParsedUrl("boe", m[1], None, None, (m[1],)) if m else None


# Gaceta Oficial (Dominican Republic)

_GACETA = "https://www.consultoria.gov.do"
_GACETA_PATH = re.compile(r"^/(uploads/[^/]+\.pdf)$")


def _gaceta_parse(u: SplitResult) -> ParsedUrl | None:
    m = _GACETA_PATH.match(u.path)
    return ParsedUrl("gaceta-oficial-do", m[1], None, None, (m[1],)) if m else None


def _build() -> tuple[Publisher, ...]:
    return (
        Publisher(
            "riigi-teataja",
            "Riigi Teataja",
            ("riigiteataja.ee",),
            _rt_parse,
            act=lambda w, e: _rt_page(e),
            edition=lambda w, e: _rt_page(e),
            current=lambda w, e: _rt_page(e, "?leiaKehtiv"),
            fragment=lambda w, e, f: _rt_page(e, f"#{f}"),
        ),
        Publisher(
            "legislation-gov-uk",
            "legislation.gov.uk",
            ("legislation.gov.uk",),
            _gb_parse,
            act=lambda w, e: _gb_page(w),
            edition=lambda w, e: _gb_page(w, e),
            current=lambda w, e: _gb_page(w),
            fragment=_gb_fragment,
            fragment_pattern=_gb_fragment_pattern,
            eid_fragment=_gb_eid_fragment,
        ),
        Publisher(
            "eur-lex",
            "EUR-Lex",
            ("eur-lex.europa.eu", "publications.europa.eu", "data.europa.eu"),
            _eur_parse,
            act=lambda w, e: f"{_EUR}/legal-content/EN/TXT/?uri=CELEX:{w}",
            edition=lambda w, e: f"{_EUR}/legal-content/EN/TXT/?uri=CELEX:{e}",
            fragment=lambda w, e, f: f"{_EUR}/legal-content/EN/TXT/?uri=CELEX:{e or w}#{f}",
            eid_fragment=_eu_eid_fragment,
        ),
        Publisher(
            "oecd-compendium",
            "OECD Legal Instruments",
            ("legalinstruments.oecd.org",),
            _oecd_parse,
            act=lambda w, e: _oecd_url(w, e or "en"),
            edition=lambda w, e: _oecd_url(w, e),
        ),
        Publisher(
            "zakon-rada",
            "Verkhovna Rada",
            ("zakon.rada.gov.ua",),
            _rada_parse,
            act=lambda w, e: _rada_page(w, None),
            edition=_rada_page,
            current=lambda w, e: _rada_page(w, None),
            fragment=lambda w, e, f: f"{_rada_page(w, e)}#{f}",
        ),
        Publisher(
            "qbz",
            "Qendra e Botimeve Zyrtare",
            ("qbz.gov.al",),
            _qbz_parse,
            act=lambda w, e: f"{_QBZ}/eli/{w}",
        ),
        Publisher(
            "ocs",
            "Office of the Council of State",
            ("searchlaw.ocs.go.th",),
            _ocs_parse,
            # With no edition id, the work id is the work-level timeline's.
            act=lambda w, e: _ocs_edition(w, e or w),
            edition=_ocs_edition,
            current=lambda w, e: _ocs_edition(w, w),
        ),
        Publisher(
            "dre",
            "Diário da República",
            ("data.dre.pt",),
            _dre_parse,
            act=lambda w, e: f"{_DRE}/{w}/p/dre",
            edition=lambda w, e: _dre_page(w, e),
            current=lambda w, e: _dre_page(w),
        ),
        Publisher(
            "boe",
            "Boletín Oficial del Estado",
            ("boe.es",),
            _boe_parse,
            act=lambda w, e: f"{_BOE}/eli/{w}",
        ),
        Publisher(
            "gaceta-oficial-do",
            "Gaceta Oficial",
            ("consultoria.gov.do",),
            _gaceta_parse,
            act=lambda w, e: f"{_GACETA}/{w}",
        ),
    )


PUBLISHERS: dict[str, Publisher] = {p.key: p for p in _build()}


#: Every shipped acquirer class and the publisher its texts come from; None where the
#: source is not the publisher of record.
ADAPTER_PUBLISHERS: dict[str, str | None] = {
    "AknNativeAcquirer": "legislation-gov-uk",
    "BoeAcquirer": "boe",
    "DiarioAcquirer": "dre",
    "EeDatadumpAcquirer": "riigi-teataja",
    "EuCellarAcquirer": "eur-lex",
    "EuDatadumpAcquirer": "eur-lex",
    "GacetaAcquirer": "gaceta-oficial-do",
    "OcsAcquirer": "ocs",
    "OecdCompendiumAcquirer": "oecd-compendium",
    "ParagrafAcquirer": None,
    "QbzAcquirer": "qbz",
    "ZakonRadaAcquirer": "zakon-rada",
}


def parse_official_url(url: str) -> ParsedUrl | None:
    """The publisher and ids an official URL names; None for any other URL."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = _host(parts.netloc)
    for publisher in PUBLISHERS.values():
        if host in publisher.hosts:
            parsed = publisher.parse(parts)
            if parsed is not None:
                return parsed
    return None


def celex_ref(celex: str, *, edition_date: str | None = None) -> OfficialRef:
    """The EUR-Lex ids of a CELEX; a consolidated text keeps its own CELEX as the edition."""
    work, edition = _celex_ids(celex)
    return OfficialRef(
        publisher="eur-lex", work_id=work, edition_id=edition, edition_date=edition_date
    )


def official_ref_from_url(url: str, *, edition_date: str | None = None) -> OfficialRef | None:
    """The ids a publisher URL carries, as an acquired text's `OfficialRef`."""
    parsed = parse_official_url(url)
    if parsed is None or parsed.work_id is None:
        return None
    return OfficialRef(
        publisher=parsed.publisher,
        work_id=parsed.work_id,
        edition_id=parsed.edition_id,
        edition_date=edition_date,
    )
