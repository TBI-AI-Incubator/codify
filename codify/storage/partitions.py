"""One `provision_embeddings` partition per jurisdiction, carrying its own HNSW."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def embedding_partition_name(code: str) -> str:
    """`provision_embeddings_p_<code>`, the code folded to an identifier."""
    return "provision_embeddings_p_" + re.sub(r"[^a-z0-9]", "_", code.lower())


async def ensure_embedding_partition(
    session: AsyncSession, jurisdiction_id: uuid.UUID, code: str
) -> None:
    """Create the jurisdiction's partition if it is missing. Instant on a new
    jurisdiction; an embedding for one with no partition fails at insert instead
    of landing somewhere it cannot be found."""
    # DDL takes no bind parameters; a UUID renders as its own literal.
    await session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{embedding_partition_name(code)}" '
            f"PARTITION OF provision_embeddings FOR VALUES IN ('{uuid.UUID(str(jurisdiction_id))}')"
        )
    )
