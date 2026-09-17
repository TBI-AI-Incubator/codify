"""One `provision_embeddings` partition per jurisdiction, carrying its own HNSW."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def embedding_partition_name(jurisdiction_id: uuid.UUID) -> str:
    """`provision_embeddings_p_<id>`: the whole id, so no two jurisdictions can
    share a table; the code is one join away when a human needs it."""
    return f"provision_embeddings_p_{uuid.UUID(str(jurisdiction_id)).hex}"


async def ensure_embedding_partition(session: AsyncSession, jurisdiction_id: uuid.UUID) -> None:
    """Create the jurisdiction's partition if it is missing. Instant on a new
    jurisdiction; an embedding for one with no partition fails at insert instead
    of landing somewhere it cannot be found."""
    # DDL takes no bind parameters; a UUID renders as its own literal.
    name = embedding_partition_name(jurisdiction_id)
    await session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{name}" '
            f"PARTITION OF provision_embeddings FOR VALUES IN ('{uuid.UUID(str(jurisdiction_id))}')"
        )
    )
