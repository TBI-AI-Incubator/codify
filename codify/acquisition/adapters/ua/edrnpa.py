"""Ukraine's Ministry of Justice register of normative acts (open data): read its card and
text exports, and map each card to the Verkhovna Rada registry ref of the act it describes."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import IO, Any, Literal

from lxml import etree

from codify.pipeline.parsers.rada_html import UNIT_BREAK

DATASET_PAGE = "https://data.gov.ua/dataset/b22d184c-4826-4e1d-8577-972effc5700c"
ARCHIVE_URL = f"{DATASET_PAGE}/resource/5616dd04-949a-489c-8efc-54004293b238/download/25-edrnpa.zip"
PUBLISHER = "minjust-edrnpa"

_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SNAPSHOT = re.compile(r"(\d{4}-\d{2}-\d{2})\.xml$")
_REGISTERED = re.compile(
    r"Зареєстровано\s+в\s+Міністерстві\s+юстиції.{0,80}?\bза\s+[NН№]\s*(\d+/\d+)", re.S
)

CardClass = Literal[
    "treaty",
    "soviet",
    "law",
    "rada_other",
    "president",
    "cabinet_resolution",
    "cabinet_order",
    "cabinet_other",
    "central_bank",
    "minjust_registered",
    "constitutional_court",
    "unregistered",
]
Card = dict[str, list[str]]


class _Clean:
    """A binary stream with the control bytes XML forbids removed, such as the export's
    trailing DOS end-of-file byte."""

    def __init__(self, raw: IO[bytes]) -> None:
        self._raw = raw

    def read(self, n: int = -1) -> bytes:
        return _CONTROL.sub(b"", self._raw.read(n))


def _documents(raw: IO[bytes]) -> Iterator[etree._Element]:
    for _, el in etree.iterparse(
        _Clean(raw),
        events=("end",),
        tag="document",
        huge_tree=True,
        resolve_entities=False,
        no_network=True,
    ):
        yield el
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]


def iter_cards(raw: IO[bytes]) -> Iterator[Card]:
    """Each card as field name to values, whitespace collapsed; one value unless a list."""
    for el in _documents(raw):
        card: Card = {}
        for item in el.iterfind("item"):
            values = [" ".join((t.text or "").split()) for t in item.iter("text")]
            # Positions are kept: a list's nth date belongs to its nth number.
            card[item.get("name") or ""] = values if any(values) else []
        yield card


def iter_texts(raw: IO[bytes]) -> Iterator[tuple[str, str]]:
    """(register code, text) per text unit, one line per source line."""
    for el in _documents(raw):
        code = " ".join((el.findtext("item[@name='reestr_kod']/text") or "").split())
        body = el.find("item[@name='TEXT_ED']")
        pars = [] if body is None else body.findall(".//par")
        text = "\n".join("".join(p.itertext()) for p in pars)
        yield code, text


def snapshot_date(member_name: str) -> date | None:
    """The export date its file name carries."""
    m = _SNAPSHOT.search(member_name)
    return date.fromisoformat(m[1]) if m else None


def first(card: Card, name: str) -> str:
    values = card.get(name) or []
    return values[0] if values else ""


def joined(card: Card, name: str) -> str:
    return " + ".join(card.get(name) or [])


def parse_date(value: str) -> date | None:
    """A `DD.MM.YYYY` date; None for anything else."""
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value.strip())
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def card_class(card: Card) -> CardClass:
    publisher, kind = joined(card, "publisher"), joined(card, "type")
    if "міжнародний договір" in kind or ("договір" in kind and "(" in kind):
        return "treaty"
    if "РСР" in publisher or "УРСР" in kind or "Компарт" in publisher:
        return "soviet"
    if publisher == "Верховна Рада України" and any(
        k in kind for k in ("Закон", "Кодекс", "Конституц")
    ):
        return "law"
    if publisher.startswith("Верховна Рада України"):
        return "rada_other"
    if publisher.startswith("Президент України"):
        return "president"
    if publisher.startswith("Кабінет Міністрів України"):
        if kind == "Постанова":
            return "cabinet_resolution"
        return "cabinet_order" if kind == "Розпорядження" else "cabinet_other"
    if "Національного банку" in publisher:
        return "central_bank"
    if card.get("reg_number"):
        return "minjust_registered"
    if "Конституційний Суд" in publisher:
        return "constitutional_court"
    return "unregistered"


def _roman(numeral: str) -> int:
    values = {"I": 1, "V": 5, "X": 10}
    total = 0
    for a, b in zip(numeral, numeral[1:] + " ", strict=True):
        total += -values[a] if values.get(b, 0) > values[a] else values[a]
    return total


def rada_convocation(numeral: str) -> int:
    """Rada's absolute convocation number: cards write the 1990s ones absolute (XII, XIV) and
    later ones counted from independence (IV to IX)."""
    n = _roman(numeral)
    return n if n >= 11 else n + 11


def ref_candidates(card: Card) -> list[str]:
    """Rada refs the card's number and registration can take, most specific first, lower case."""
    number = " ".join(joined(card, "number").split())
    adopted = first(card, "date_acc")
    y4 = adopted[-4:]
    y2 = y4[2:]
    publisher, kind = joined(card, "publisher"), joined(card, "type")
    out: list[str] = []
    # A registered act's own registration comes first: its number may be another issuer's.
    dates = card.get("reg_date") or []
    for i, registered in enumerate(card.get("reg_number") or []):
        if m := re.match(r"(\d+)", registered):
            when = (dates[i] if i < len(dates) else "") or adopted
            out.append(f"z{int(m[1]):04d}-{when[-2:]}")
            break
    if number:
        out.append(number)
        if m := re.fullmatch(r"(\d+[а-яіїєґ]?)-([IVX]+)", number):
            out.append(f"{m[1]}-{rada_convocation(m[2])}")
        if publisher.startswith("Кабінет Міністрів"):
            base = number.removesuffix("-р")
            suffix = "р" if number.endswith("-р") or kind == "Розпорядження" else "п"
            out += [f"{base}-{y4}-{suffix}", f"{base}-{y2}-{suffix}"]
        if "Національного банку" in publisher and number.isdigit():
            out.append(f"v{int(number):04d}500-{y2}")
    return [c.lower() for c in out]


def norm_title(title: str) -> str:
    folded = title.lower().replace("’", "'").replace("`", "'").replace("ʼ", "'")
    return re.sub(r"[^\w']+", " ", folded.replace("№", "n")).strip()


_REF_YEAR = re.compile(r"(?:-|/)(\d{2}|\d{4})(?:-[а-яa-z]+)?$")


@dataclass
class Match:
    ref: str | None
    how: Literal["rule", "title", "none"]
    cause: str | None = None  # why an unmatched card found no ref


@dataclass
class RefMatcher:
    """Matches cards to registry refs by number rule, else by a title and year that name one
    work no earlier card took. Feed cards in file order: the title fallback depends on it."""

    refs: dict[str, str] = field(default_factory=dict)  # lower case to stored form
    by_title: dict[str, list[tuple[str, str | None]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    taken: set[str] = field(default_factory=set)

    @classmethod
    def of(cls, registry: Iterable[tuple[str, str]]) -> RefMatcher:
        """From (ref, title) rows of the Rada registry."""
        matcher = cls()
        for ref, title in registry:
            key = ref.lower()
            matcher.refs[key] = ref
            year = _REF_YEAR.search(ref)
            matcher.by_title[norm_title(title)].append((key, year[1][-2:] if year else None))
        return matcher

    def match(self, card: Card) -> Match:
        candidates = ref_candidates(card)
        hit = next((c for c in candidates if c in self.refs), None)
        if hit is not None:
            self.taken.add(hit)
            return Match(self.refs[hit], "rule")
        named = self.by_title.get(norm_title(joined(card, "name")), [])
        y2 = first(card, "date_acc")[-2:]
        same = [ref for ref, year in named if year in (None, y2)]
        if len(same) == 1 and same[0] not in self.taken:
            self.taken.add(same[0])
            return Match(self.refs[same[0]], "title")
        if len(same) > 1:
            cause = "title_ambiguous"
        elif same:
            cause = "title_taken"
        else:
            cause = "number_not_in_registry" if candidates else "no_number_scheme"
        return Match(None, "none", cause)


def registration_of(text: str) -> str | None:
    """The Ministry of Justice registration a text unit states near its head, `N/M`."""
    m = _REGISTERED.search(text[:1500])
    return m[1] if m else None


def compose_units(units: list[str], act_registration: str | None) -> str | None:
    """One source from a code's text units: the act's own unit, then each separately
    registered annex as its own unit. None when more than one unit could be the act."""
    if len(units) == 1:
        return units[0]
    main: list[str] = []
    annexes: list[str] = []
    for unit in units:
        registered = registration_of(unit)
        own = registered is None or registered == act_registration
        (main if own else annexes).append(unit)
    if len(main) != 1:
        return None
    return UNIT_BREAK.join([main[0], *annexes])


def _publication(value: str) -> dict[str, str]:
    source, published, issue, article = [*value.split("#"), "", "", ""][:4]
    when = parse_date(published)
    return {
        "source": source,
        "date": when.isoformat() if when else published,
        "issue": issue,
        "article": article,
    }


def card_record(card: Card, card_kind: CardClass, how: str) -> dict[str, Any]:
    """The card as stored against a registry row: JSON-safe, dates in ISO form."""
    adopted = parse_date(first(card, "date_acc"))
    entered = parse_date(first(card, "reestr_date"))
    dates = card.get("reg_date") or []
    registrations = []
    for i, number in enumerate(card.get("reg_number") or []):
        raw = dates[i] if i < len(dates) else ""
        when = parse_date(raw)
        registrations.append({"number": number, "date": when.isoformat() if when else raw})
    return {
        "register_code": first(card, "reestr_code"),
        "register_date": entered.isoformat() if entered else None,
        "class": card_kind,
        "matched_by": how,
        "issuer": card.get("publisher") or [],
        "type": joined(card, "type"),
        "number": first(card, "number"),
        "adopted": adopted.isoformat() if adopted else None,
        "title": joined(card, "name"),
        "status": first(card, "status"),
        "registrations": registrations,
        "publications": [_publication(p) for p in card.get("publication") or []],
        "keywords": card.get("keywords") or [],
    }


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def attribution(snapshot: date) -> str:
    """The attribution the dataset's licence and the open-data law require, with the
    caveat every text from it carries."""
    return (
        "Source: Ministry of Justice of Ukraine, Unified State Register of Normative Legal "
        "Acts (Єдиний державний реєстр нормативно-правових актів), open dataset on "
        f"data.gov.ua, {DATASET_PAGE}, snapshot of {snapshot.day} {_MONTHS[snapshot.month - 1]} "
        f"{snapshot.year}. Licensed "
        "under CC BY 4.0. Texts are as adopted and may not reflect later amendments."
    )


__all__ = [
    "ARCHIVE_URL",
    "DATASET_PAGE",
    "PUBLISHER",
    "UNIT_BREAK",
    "Card",
    "CardClass",
    "Match",
    "RefMatcher",
    "attribution",
    "card_class",
    "card_record",
    "compose_units",
    "first",
    "iter_cards",
    "iter_texts",
    "joined",
    "norm_title",
    "parse_date",
    "rada_convocation",
    "ref_candidates",
    "registration_of",
    "snapshot_date",
]
