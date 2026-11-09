"""Size bound shared by the AKN indexers that parse a whole version in memory."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlmodel import col

from codify.storage.models import Version

# Parsing holds the whole tree in memory, so a very large version is refused.
MAX_INDEXED_AKN_CHARS = 64_000_000


def oversize_reason(chars: int) -> str | None:
    if chars <= MAX_INDEXED_AKN_CHARS:
        return None
    return f"akn too large: {chars:,} characters, limit {MAX_INDEXED_AKN_CHARS:,}"


def bounded_akn(version_id: uuid.UUID) -> Select[Any]:
    """(length, AKN or NULL when over the limit, language), read in one snapshot."""
    length = func.char_length(col(Version.akn_xml))
    akn = case((length <= MAX_INDEXED_AKN_CHARS, col(Version.akn_xml)))
    return select(length, akn, col(Version.language)).where(col(Version.id) == version_id)
