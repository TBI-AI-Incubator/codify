"""Size bound shared by the AKN indexers that parse a whole version in memory."""

from __future__ import annotations

# Parsing holds the whole tree in memory, so a very large version is refused.
MAX_INDEXED_AKN_CHARS = 64_000_000


def oversize_reason(chars: int) -> str | None:
    if chars <= MAX_INDEXED_AKN_CHARS:
        return None
    return f"akn too large: {chars:,} characters, limit {MAX_INDEXED_AKN_CHARS:,}"
