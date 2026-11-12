"""Riigi Teataja amendment notes as `<textualMod>` entries, and source ids by final eId."""

from __future__ import annotations

import re
from collections import Counter

import structlog
from lxml import etree

from codify.akn._schema import AKN_NS, parse_xml

logger = structlog.get_logger()

MARK = "x-rt-id"
FOLDED = "x-rt-folded"
AKT_URL = "https://www.riigiteataja.ee/akt/"
_UNIT_TAGS = frozenset(
    {
        "osa",
        "peatykk",
        "jagu",
        "jaotis",
        "alljaotis",
        "paragrahv",
        "loige",
        "alampunkt",
        "punkt",
    }
)
_REPEAL = re.compile(
    r"[\s\-–,;]*(?:kehtetud?\b|välja jäetud\b|riigikohtu\b.*\btunnistab\b.*\bkehtetuks)", re.I
)
# Words that mark an entry into force other than the plain date in <joustumine>.
_QUALIFIED = re.compile(r"osaliselt|muudetud|jõust", re.I)
_PLAIN_LABEL = re.compile(r"[\s\-–.,;]*jõust(?:ub|us)?[\s.,;]*", re.I)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def q(tag: str) -> str:
    return f"{{{AKN_NS}}}{tag}"


def local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _citation(mark: etree._Element) -> str:
    """The amending act's own publication, as the note states it."""
    am = mark.find("{*}avaldamismarge")
    if am is None:
        return ""
    part = (am.findtext("{*}RTosa") or "").strip()
    day = (am.findtext("{*}avaldamineKuupaev") or "").strip()
    if _ISO_DATE.fullmatch(day):
        day = ".".join(reversed(day.split("-")))
    year = (am.findtext("{*}RTaasta") or "").strip()
    nr = (am.findtext("{*}RTnr") or "").strip()
    art = (am.findtext("{*}RTartikkel") or "").strip()
    return ", ".join(x for x in (f"{part} {year}".strip(), day, nr, art) if x)


_AMBIGUOUS = "\0"


def _unit_eid(mark: etree._Element, by_source_id: dict[str, str]) -> str | None:
    for ancestor in mark.iterancestors():
        if local(ancestor) in _UNIT_TAGS and not ancestor.get(FOLDED):
            return by_source_id.get(ancestor.get("id") or "")
    return None


def annotate(
    akn_xml: str, source_root: etree._Element, work_uri: str
) -> tuple[str, dict[str, str]]:
    """Move unit marks into an eId to source id map and add the notes, logging each skip."""
    root = parse_xml(akn_xml, huge_tree=True)
    anchors: dict[str, str] = {}
    by_source_id: dict[str, str] = {}
    for el in root.iter():
        if isinstance(el.tag, str) and (sid := el.attrib.pop(MARK, None)):
            anchors[el.get("eId", "")] = sid
            # A source id used twice names no single unit.
            by_source_id[sid] = _AMBIGUOUS if sid in by_source_id else el.get("eId", "")

    counts: Counter[str] = Counter()
    mods: list[etree._Element] = []
    dates: dict[str, int] = {}
    citations: dict[str, str] = {}
    act_events: dict[tuple[str, str], int] = {}
    for mark in source_root.iter("{*}muutmismarge"):
        am = mark.find("{*}avaldamismarge")
        akt = (am.findtext("{*}aktViide") or "").strip() if am is not None else ""
        eid = _unit_eid(mark, by_source_id)
        parent = mark.getparent()
        whole_act = eid is None and parent is not None and local(parent) == "oigusakt"
        texts = [" ".join((t.text or "").split()) for t in mark.findall("{*}tavatekst")]
        when = (mark.findtext("{*}joustumine") or "").strip()[:10]
        dated = bool(_ISO_DATE.fullmatch(when)) and not any(
            _QUALIFIED.search(t) and not _PLAIN_LABEL.fullmatch(t) for t in texts
        )
        if not re.fullmatch(r"\d+", akt):
            counts["skipped_no_amending_act"] += 1
        elif eid == _AMBIGUOUS:
            counts["skipped_repeated_unit_id"] += 1
        elif whole_act:
            if dated:
                act_events.setdefault((akt, when), len(act_events) + 1)
                citations.setdefault(akt, _citation(mark) or akt)
                counts["whole_act_event"] += 1
            else:
                counts["skipped_whole_act_undated"] += 1
        elif eid is None:
            counts["skipped_no_unit"] += 1
        else:
            first = mark[0] if len(mark) else None
            repeal = (
                first is not None
                and local(first) == "tavatekst"
                and bool(_REPEAL.match(first.text or ""))
            )
            mod = etree.Element(q("textualMod"), type="repeal" if repeal else "substitution")
            mod.set("eId", f"pmod_ee_{len(mods) + 1}")
            etree.SubElement(mod, q("source"), href=f"{AKT_URL}{akt}")
            if cite := _citation(mark):
                citations.setdefault(akt, cite)
            etree.SubElement(mod, q("destination"), href=f"{work_uri}/main#{eid}")
            if dated:
                index = dates.setdefault(when, len(dates) + 1)
                etree.SubElement(mod, q("force"), period=f"#tg_{index}")
            mods.append(mod)
            counts["kept"] += 1

    if mods or act_events:
        _add_meta(root, mods, dates, citations, act_events)
    if counts:
        logger.info("riigi_teataja_notes", work=work_uri, **counts)
    return etree.tostring(root, encoding="unicode"), anchors


def _add_meta(
    root: etree._Element,
    mods: list[etree._Element],
    dates: dict[str, int],
    citations: dict[str, str],
    act_events: dict[tuple[str, str], int],
) -> None:
    meta = root[0].find(q("meta"))
    refs = meta.find(q("references")) if meta is not None else None
    if meta is None or refs is None:
        return
    events = [(f"evt_act_{i}", when, f"#rt_{akt}") for (akt, when), i in act_events.items()]
    events += [(f"evt_{i}", when, "#codify") for when, i in dates.items()]
    if events:
        life = etree.Element(q("lifecycle"), source="#codify")
        for eid, when, source in events:
            etree.SubElement(
                life, q("eventRef"), eId=eid, date=when, type="amendment", source=source
            )
        refs.addprevious(life)
    if mods:
        analysis = etree.Element(q("analysis"), source="#codify")
        etree.SubElement(analysis, q("passiveModifications")).extend(mods)
        refs.addprevious(analysis)
    if dates:
        temporal = etree.Element(q("temporalData"), source="#codify")
        for index in dates.values():
            group = etree.SubElement(temporal, q("temporalGroup"), eId=f"tg_{index}")
            etree.SubElement(
                group, q("timeInterval"), start=f"#evt_{index}", refersTo="#entryIntoForce"
            )
        refs.addprevious(temporal)
    for akt, cite in citations.items():
        etree.SubElement(
            refs,
            q("TLCReference"),
            eId=f"rt_{akt}",
            name="amendingAct",
            href=f"{AKT_URL}{akt}",
            showAs=cite,
        )
    if dates:
        etree.SubElement(
            refs,
            q("TLCConcept"),
            eId="entryIntoForce",
            href="/ontology/concept/entryIntoForce",
            showAs="Entry into force",
        )
