"""Riigi Teataja amendment notes as `<textualMod>` entries, and source ids by final eId."""

from __future__ import annotations

import re
from collections import Counter

from lxml import etree

from codify.akn._schema import AKN_NS, parse_xml

MARK = "x-rt-id"
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
_REPEAL = re.compile(r"\s*kehtetud?\b", re.I)
# Words that mark an entry into force other than the plain date in <joustumine>.
_QUALIFIED = re.compile(r"osaliselt|muudetud|jõust", re.I)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _q(tag: str) -> str:
    return f"{{{AKN_NS}}}{tag}"


def _local(el: etree._Element) -> str:
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


def _unit_eid(mark: etree._Element, by_source_id: dict[str, str]) -> str | None:
    for ancestor in mark.iterancestors():
        if _local(ancestor) in _UNIT_TAGS and (sid := ancestor.get("id")) in by_source_id:
            return by_source_id[sid]
    return None


def annotate(
    akn_xml: str, source_root: etree._Element, work_uri: str
) -> tuple[str, dict[str, str], dict[str, int]]:
    """Move unit marks into an eId to source id map and add the notes, counting each skip."""
    root = parse_xml(akn_xml, huge_tree=True)
    anchors: dict[str, str] = {}
    for el in root.iter():
        if isinstance(el.tag, str) and (sid := el.attrib.pop(MARK, None)):
            anchors[el.get("eId", "")] = sid
    by_source_id = {sid: eid for eid, sid in anchors.items()}

    counts: Counter[str] = Counter()
    mods: list[etree._Element] = []
    dates: dict[str, int] = {}
    citations: dict[str, str] = {}
    for mark in source_root.iter("{*}muutmismarge"):
        am = mark.find("{*}avaldamismarge")
        akt = (am.findtext("{*}aktViide") or "").strip() if am is not None else ""
        eid = _unit_eid(mark, by_source_id)
        parent = mark.getparent()
        whole_act = eid is None and parent is not None and _local(parent) == "oigusakt"
        if not re.fullmatch(r"\d+", akt):
            counts["skipped_no_amending_act"] += 1
        elif eid is None and not whole_act:
            counts["skipped_no_unit"] += 1
        else:
            texts = [" ".join((t.text or "").split()) for t in mark.findall("{*}tavatekst")]
            first = mark[0] if len(mark) else None
            repeal = (
                first is not None
                and _local(first) == "tavatekst"
                and bool(_REPEAL.match(first.text or ""))
            )
            when = (mark.findtext("{*}joustumine") or "").strip()[:10]
            dated = bool(_ISO_DATE.fullmatch(when)) and not any(_QUALIFIED.search(t) for t in texts)
            mod = etree.Element(_q("textualMod"), type="repeal" if repeal else "substitution")
            mod.set("eId", f"pmod_ee_{len(mods) + 1}")
            etree.SubElement(mod, _q("source"), href=f"{AKT_URL}{akt}")
            if cite := _citation(mark):
                citations.setdefault(akt, cite)
            fragment = f"#{eid}" if eid else ""
            etree.SubElement(mod, _q("destination"), href=f"{work_uri}/main{fragment}")
            if whole_act:
                counts["whole_act"] += 1
            if dated:
                index = dates.setdefault(when, len(dates) + 1)
                etree.SubElement(mod, _q("force"), period=f"#tg_{index}")
            mods.append(mod)
            counts["kept"] += 1
            counts["repeal" if repeal else "amendment"] += 1
            counts["dated" if dated else "undated"] += 1

    if mods:
        _add_meta(root, mods, dates, citations)
    return etree.tostring(root, encoding="unicode"), anchors, dict(counts)


def _add_meta(
    root: etree._Element,
    mods: list[etree._Element],
    dates: dict[str, int],
    citations: dict[str, str],
) -> None:
    act = root[0]
    meta = act.find(_q("meta"))
    refs = meta.find(_q("references")) if meta is not None else None
    if meta is None or refs is None:
        return
    if dates:
        life = etree.Element(_q("lifecycle"), source="#codify")
        temporal = etree.Element(_q("temporalData"), source="#codify")
        for when, index in dates.items():
            etree.SubElement(
                life,
                _q("eventRef"),
                eId=f"evt_{index}",
                date=when,
                type="amendment",
                source="#codify",
            )
            group = etree.SubElement(temporal, _q("temporalGroup"), eId=f"tg_{index}")
            etree.SubElement(
                group,
                _q("timeInterval"),
                start=f"#evt_{index}",
                refersTo="#entryIntoForce",
            )
        refs.addprevious(life)
    analysis = etree.Element(_q("analysis"), source="#codify")
    passive = etree.SubElement(analysis, _q("passiveModifications"))
    passive.extend(mods)
    refs.addprevious(analysis)
    for akt, cite in citations.items():
        etree.SubElement(
            refs,
            _q("TLCReference"),
            eId=f"rt_{akt}",
            name="amendingAct",
            href=f"{AKT_URL}{akt}",
            showAs=cite,
        )
    if dates:
        refs.addprevious(temporal)
        etree.SubElement(
            refs,
            _q("TLCConcept"),
            eId="entryIntoForce",
            href="/ontology/concept/entryIntoForce",
            showAs="Entry into force",
        )
