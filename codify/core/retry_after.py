"""The Retry-After header, in either of its two forms."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header, delay-seconds or HTTP-date form."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:  # a date without a zone is not a usable hint
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
