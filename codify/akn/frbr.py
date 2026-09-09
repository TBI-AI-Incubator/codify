"""FRBR-URI helpers, one place to parse `/akn/{country}/{doctype}/{date}/{number}`.

The country segment was being re-extracted with subtly different parsers and
fallbacks across the emitter and the comparator; centralise it here.
"""

from __future__ import annotations


def country_of(uri: str, *, default: str = "xx") -> str:
    """Country segment of an AKN FRBR URI (`/akn/{country}/...`).

    Tolerates `http(s)://host` prefixes (splits on the `/akn/` marker) and
    returns ``default`` when the URI carries no parseable country segment.
    """
    parts = uri.split("/akn/", 1)
    if len(parts) == 2:
        seg = parts[1].split("/", 1)[0].strip()
        if seg:
            return seg
    return default


def work_parts(uri: str) -> tuple[str, str, str | None, str, str] | None:
    """`(country, doctype, subtype, date, number)` of a work URI, the subtype
    None for the four-segment shape; None when the URI is neither shape."""
    parts = uri.split("/akn/", 1)
    if len(parts) != 2:
        return None
    # Interior empty segments stay, so a malformed identity fails the count.
    segments = parts[1].strip("/").split("/")
    if not all(segments):
        return None
    if len(segments) == 4:
        country, doctype, date, number = segments
        return country, doctype, None, date, number
    if len(segments) == 5:
        country, doctype, subtype, date, number = segments
        return country, doctype, subtype, date, number
    return None
