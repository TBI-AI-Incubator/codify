"""Cellar's amendment annotations as an effects feed.

Cellar records each amendment as OWL axiom annotations on the amends, repeals,
corrects, completes and derogates triples between two works: the subdivision
touched (a coded location), the kind of change (a coded type) and the date it
takes effect. No amending provision and no quoted text. The axioms are blank
nodes, so the publisher id is derived from the facts themselves.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date

import httpx

from codify.acquisition.adapters.eu.cellar import celex_to_frbr
from codify.acquisition.effects.types import FeedEffect, FeedResult

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
BATCH = 50

_ANN = "http://publications.europa.eu/ontology/annotation#"
_CDM = "http://publications.europa.eu/ontology/cdm#"
_RELATIONS = (
    "resource_legal_amends_resource_legal",
    "resource_legal_repeals_resource_legal",
    "resource_legal_implicitly_repeals_resource_legal",
    "resource_legal_corrects_resource_legal",
    "resource_legal_completes_resource_legal",
    "resource_legal_derogates_resource_legal",
)

# Generic amendment codes remain unclassified; the feed retains their raw value.
_CROSSWALK: dict[str, tuple[str, str]] = {
    "R": ("substitution", "textual"),
    "J": ("insertion", "textual"),
    "C": ("substitution", "textual"),
    "SU": ("repeal", "textual"),
    "A": ("repeal", "textual"),
    "AP": ("repeal", "textual"),
}
# A relation without a change type is the relation itself.
_BY_RELATION: dict[str, tuple[str, str]] = {
    "resource_legal_repeals_resource_legal": ("repeal", "textual"),
    "resource_legal_implicitly_repeals_resource_legal": ("repeal", "textual"),
    "resource_legal_corrects_resource_legal": ("substitution", "textual"),
    "resource_legal_completes_resource_legal": ("substitution", "textual"),
    "resource_legal_derogates_resource_legal": ("exceptionOfScope", "scope"),
}

_CODE = re.compile(r"\{([A-Z]+)\|[^}]*\}\s*([^{]*)")
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_CELEX_SECTOR_3 = re.compile(r"^3\d{4}[A-Z]")


def sparql_for(celexes: list[str]) -> str:
    """One query for a batch: every annotated relation whose target is one of the acts."""
    values = " ".join(f'"{c}"' for c in celexes)
    return (
        f"PREFIX cdm: <{_CDM}> PREFIX owl: <http://www.w3.org/2002/07/owl#> "
        f"PREFIX ann: <{_ANN}> "
        "SELECT ?celex ?p ?other ?loc ?role ?from WHERE { "
        f"VALUES ?celex {{ {values} }} "
        "?w cdm:resource_legal_id_celex ?cx . FILTER(STR(?cx)=?celex) "
        "?ax owl:annotatedTarget ?w ; owl:annotatedProperty ?p ; owl:annotatedSource ?o . "
        "FILTER(" + " || ".join(f"?p = cdm:{r}" for r in _RELATIONS) + ") "
        "OPTIONAL { ?o cdm:resource_legal_id_celex ?other } "
        "OPTIONAL { ?ax ann:reference_to_modified_location ?loc } "
        "OPTIONAL { ?ax ann:role2 ?role } "
        "OPTIONAL { ?ax ann:start_of_validity ?from } }"
    )


def _codes(raw: str) -> list[tuple[str, str]]:
    """`{AR|...} 8.3 {PA|...} 1` to [(AR, 8.3), (PA, 1)]."""
    return [(m.group(1), m.group(2).strip()) for m in _CODE.finditer(raw)]


def _roman(text: str) -> int | None:
    if not text or any(ch not in _ROMAN for ch in text):
        return int(text) if text.isdigit() else None
    total = 0
    for i, ch in enumerate(text):
        value = _ROMAN[ch]
        total += -value if i + 1 < len(text) and _ROMAN[text[i + 1]] > value else value
    return total


def location_eid(raw: str) -> tuple[str | None, str | None]:
    """(target eId, skip reason) for a coded location.

    Articles and their paragraphs map; a point collapses to its paragraph; an
    annex maps to its attachment; a division falls to the act; free text or an
    empty location is a work-level row. The reason names what was collapsed.
    """
    if not raw.strip():
        return None, "work_level"
    codes = _codes(raw)
    if not codes:
        return None, "unparsed_location"
    head, value = codes[0]
    if head == "AR":
        parts = re.split(r"[.\s]+", value.strip())
        if not parts or not re.fullmatch(r"\d+[A-Za-z]{0,2}", parts[0]):
            return None, "unparsed_location"
        eid = f"art_{parts[0].lower()}"
        para = next((v for c, v in codes[1:] if c == "PA"), parts[1] if len(parts) > 1 else "")
        if para and re.fullmatch(r"\d+[A-Za-z]{0,2}", para):
            eid = f"{eid}__para_{para.lower()}"
        collapsed = len(codes) > (2 if para else 1) or len(parts) > (2 if para else 1)
        return eid, "point_collapsed" if collapsed else None
    if head == "AN":
        lead = re.match(r"\s*([IVXLC]+|\d+)", value)
        ordinal = _roman(lead.group(1)) if lead else (1 if not value.strip() else None)
        if ordinal is None:
            return None, "unparsed_location"
        return f"att_{ordinal}", "point_collapsed" if len(codes) > 1 else None
    if head in {"TIT", "CH", "SCT", "PRT", "PTI"}:
        return None, "division_collapsed"
    return None, "unparsed_location"


def fallback_eid(eid: str) -> str | None:
    """The unit to write when the named one is not in the version: a paragraph
    falls to its article; an article or an attachment has nowhere to fall."""
    if "__para_" in eid:
        return eid.split("__para_", 1)[0]
    return None


def publisher_id(
    amending: str, relation: str, amended: str, location: str, role: str, when: str
) -> str:
    """A stable id for a blank-node axiom, from the facts it states."""
    key = "\x1f".join((amending, relation, amended, location, role, when))
    return "cellar:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]  # noqa: S324


def _work(celex: str) -> str | None:
    """Our work URI for a sector-3 act; None for a source we cannot hold."""
    if not _CELEX_SECTOR_3.match(celex):
        return None
    try:
        return celex_to_frbr(celex)[0]
    except ValueError:
        return None


def _date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw.strip()[:10].replace("/", "-"))
    except ValueError:
        return None


def parse_results(text: str, celexes: list[str]) -> dict[str, FeedResult]:
    """CSV rows of `sparql_for` to one FeedResult per asked-for CELEX."""
    results = {c: FeedResult() for c in celexes}
    for row in csv.DictReader(io.StringIO(text)):
        target = row["celex"]
        result = results.setdefault(target, FeedResult())
        relation = row["p"].rsplit("#", 1)[-1]
        role_codes = _codes(row.get("role") or "")
        role = role_codes[0][0] if role_codes else ""
        location = row.get("loc") or ""
        when = row.get("from") or ""
        amending = row.get("other") or ""
        if role in _CROSSWALK:
            action, category = _CROSSWALK[role]
        elif not role and relation in _BY_RELATION:
            action, category = _BY_RELATION[relation]
        else:
            action, category = None, None
            result.skipped["unmapped_type"] = result.skipped.get("unmapped_type", 0) + 1
        eid, reason = location_eid(location)
        if reason:
            result.skipped[reason] = result.skipped.get(reason, 0) + 1
        affecting = _work(amending) if amending else None
        if amending and affecting is None:
            result.skipped["foreign_source"] = result.skipped.get("foreign_source", 0) + 1
        effective = _date(when) if when else None
        if effective is None:
            result.skipped["undated"] = result.skipped.get("undated", 0) + 1
        result.effects.append(
            FeedEffect(
                publisher_id=publisher_id(amending, relation, target, location, role, when),
                affected_work=_work(target),
                affected_provision=location,
                affecting_work=affecting,
                affecting_provision="",
                raw_type=f"{role or '-'}:{relation.removeprefix('resource_legal_')}",
                akn_action=action,
                akn_category=category,
                in_force_date=effective,
                applied=None,
                is_meta=False,
            )
        )
    return results


class CellarEffectsAdapter:
    """Cellar's amendment annotations, one SPARQL query per fifty acts."""

    jurisdiction_code = "eu"
    batch_size = BATCH

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @staticmethod
    def publisher_path_for(work_uri: str) -> str | None:
        match = re.fullmatch(r"/akn/eu/act/(dir|reg|dec)/(\d{4})/([1-9]\d*)", work_uri)
        if match is None:
            return None
        kind, year, number = match.groups()
        descriptor = {"dir": "L", "reg": "R", "dec": "D"}[kind]
        celex = f"3{year}{descriptor}{int(number):04d}"
        return celex if celex_to_frbr(celex)[0] == work_uri else None

    @staticmethod
    def held_target_eid(eid: str, held: set[str]) -> str | None:
        matches = {
            candidate for candidate in held if candidate == eid or candidate.endswith("__" + eid)
        }
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def target_eid(affected_provision: str) -> str | None:
        return location_eid(affected_provision)[0]

    async def fetch(self, publisher_path: str) -> FeedResult:
        return (await self.fetch_many([publisher_path]))[publisher_path]

    async def fetch_many(self, publisher_paths: list[str]) -> dict[str, FeedResult]:
        """Batched: the SPARQL host is the one the polite bucket paces, so fifty
        acts a query is what makes the archive affordable."""
        out: dict[str, FeedResult] = {}
        for start in range(0, len(publisher_paths), BATCH):
            batch = publisher_paths[start : start + BATCH]
            response = await self._client.get(
                SPARQL_ENDPOINT,
                params={"query": sparql_for(batch)},
                headers={"accept": "text/csv"},
                timeout=httpx.Timeout(180.0, connect=10.0),
            )
            response.raise_for_status()
            out.update(parse_results(response.text, batch))
        return out


__all__ = [
    "BATCH",
    "CellarEffectsAdapter",
    "fallback_eid",
    "location_eid",
    "parse_results",
    "publisher_id",
    "sparql_for",
]
