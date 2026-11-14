"""EU act citations in English prose ("Regulation (EU) 2016/679", "Directive 95/46/EC",
"32016R0679") read into FRBR work URIs. Deterministic; the resolver links held acts."""

from __future__ import annotations

import re

from codify.agents.tools.frbr import construct_frbr_uri

# Class token on the refs this module's pass mints: the amendment passes leave them alone.
EU_CITATION_CLASS = "eu-citation"
_KINDS = {"regulation": "regulation", "directive": "directive", "decision": "decision"}
_CELEX_KINDS = {"R": "regulation", "L": "directive", "D": "decision"}
_MARKS = frozenset({"EU", "EC", "EEC", "EURATOM", "ECSC", "CFSP", "JHA", "CE"})

_SUFFIX = "EURATOM|ECSC|CFSP|JHA|EEC|EC|EU|CE"
_MARK = r"\((?P<mark>[A-Za-z]{2,8}(?:\s*,\s*[A-Za-z]{2,8})*)\)\s+"
_ITEM = (
    rf"(?<![\w/])(?:{_MARK})?(?P<no>No\.?\s+)?(?P<a>\d{{1,4}})/(?P<b>\d{{1,4}})"
    # Letters may follow: Formex text can run a footnote straight on ("2003/71/ECDirective").
    rf"(?:/(?P<suffix>{_SUFFIX}))?(?![\d/])"
)
_ITEM_BARE = rf"(?:\([A-Za-z, ]{{2,30}}\)\s+)?(?:No\.?\s+)?\d{{1,4}}/\d{{1,4}}(?:/(?:{_SUFFIX}))?"
# Never empty, so a run of digit pairs cannot backtrack exponentially.
_SEP = r"(?:\s*[,;]\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)"
# The head ("Implementing Regulation", "Directives") and any list items before this one.
_LEAD = re.compile(
    rf"(?:\b|(?-i:(?<=[A-Z])))(?P<kind>Regulation|Directive|Decision)s?\s+(?P<list>(?:{_ITEM_BARE}{_SEP})*)$",
    re.I,
)
_CELEX = r"(?<![\w/])3(?P<year>(?:19|20)\d\d)(?P<kind>[RLD])(?P<num>\d{4})(?![\w/])"
ITEM = re.compile(_ITEM, re.I)
CITATION = re.compile(rf"{_CELEX}|{_ITEM}", re.I)
# Wide enough for the long lists of directives older acts repeal.
_LEAD_WINDOW = 2000


def _year(token: str) -> int | None:
    n = int(token)
    if len(token) == 4 and 1952 <= n <= 2099:
        return n
    # Two-digit years ran until 1998.
    if len(token) == 2 and n >= 52:
        return 1900 + n
    return None


def _marks(text: str | None) -> set[str]:
    return {t.strip().upper() for t in (text or "").split(",")}


def work_uri(m: re.Match[str], before: str, *, eu_text: bool = False) -> str | None:
    """The work URI a `CITATION` match cites, given the text before it; None when a
    numbered citation has no EU-marked head. `eu_text` also takes an unmarked year-first one."""
    if m.group("num"):
        kind = _CELEX_KINDS[m.group("kind").upper()]
        num = m.group("num").lstrip("0")
        return construct_frbr_uri("eu", kind, m.group("year"), num) if num else None
    lead = _LEAD.search(before[-_LEAD_WINDOW:])
    if lead is None:
        return None
    a, b = m.group("a"), m.group("b")
    # "No", or a short number before a four-digit year ("58/2003"), puts the number first.
    number_first = m.group("no") or (len(a) < 4 and len(b) == 4)
    pairs = [(a, b), (b, a)] if number_first else [(b, a), (a, b)]
    number, year = next(((n, y) for n, t in pairs if (y := _year(t)) is not None), (a, None))
    marked = (
        _marks(m.group("mark")) | _marks(m.group("suffix")) | _marks_in(lead.group("list"))
    ) & _MARKS
    # Unmarked "No 1/2019 of the Joint Committee" is another body's; EU text cites its own bare.
    if not (marked or (eu_text and not m.group("no") and len(a) == 4 and year == int(a))):
        return None
    if year is None or not number.lstrip("0"):
        return None
    return construct_frbr_uri(
        "eu", _KINDS[lead.group("kind").lower()], str(year), number.lstrip("0")
    )


def _marks_in(listed: str) -> set[str]:
    out: set[str] = set()
    for m in ITEM.finditer(listed):
        out |= _marks(m.group("mark")) | _marks(m.group("suffix"))
    return out
