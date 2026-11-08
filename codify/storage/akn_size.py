"""Size bound shared by the AKN indexers that parse a whole version in memory."""

from __future__ import annotations

# Peak memory runs at 25 to 40 times the document size, so this stays near 2.5 GiB.
MAX_INDEXED_AKN_CHARS = 64_000_000


def oversize_reason(chars: int) -> str | None:
    if chars <= MAX_INDEXED_AKN_CHARS:
        return None
    return f"akn too large: {chars:,} characters, limit {MAX_INDEXED_AKN_CHARS:,}"
