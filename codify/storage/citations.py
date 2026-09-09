"""Recognise a law cited by number rather than named by title.

Civil servants cite instruments as numbers, and the conventions vary by
jurisdiction and by the language the reader is typing in: `5/2010`,
`Law No. 5 of 2010`, `قرار بقانون رقم 9 لسنة 2025`. Title search cannot reach
those reliably, because it matches the words of a stored title and the reader is
not reproducing a title.

`laws` already stores `number` and `year` as columns, so this is a structural
lookup rather than a text one, and needs no per-jurisdiction citation grammar:
what the forms have in common is two integers, one of which is a year.
"""

from __future__ import annotations

import re

# Arabic-Indic and Eastern Arabic-Indic digits alongside ASCII, because the
# reader types the script the gazette is printed in.
_DIGITS = re.compile(r"[0-9٠-٩۰-۹]+")
_ASCII = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Wide enough for the oldest instruments the corpus carries,
# and for a Hijri year, which is numerically distinct from any instrument number
# a jurisdiction issues.
_YEAR_MIN, _YEAR_MAX = 1200, 2200


def parse_citation(query: str) -> tuple[str, int] | None:
    """`(number, year)` if the query reads as a numeric citation, else None.

    Deliberately narrow. It fires only where the query is exactly two numbers,
    one of them year-shaped, so a title search that happens to contain a year
    ("companies law 1929") is not hijacked into a structural lookup: the digits
    there number one, not two.
    """
    numbers = [m.group().translate(_ASCII).lstrip("0") or "0" for m in _DIGITS.finditer(query)]
    if len(numbers) != 2:
        return None
    first, second = numbers
    years = [n for n in (first, second) if _YEAR_MIN <= int(n) <= _YEAR_MAX]
    if len(years) != 1:
        # Two year-shaped numbers is a date range, and none is a numbering
        # scheme this cannot read. Both belong to title search.
        return None
    year = years[0]
    number = second if year == first else first
    return number, int(year)
