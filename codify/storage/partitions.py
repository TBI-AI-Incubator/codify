"""One `provision_embeddings` partition per jurisdiction, carrying its own HNSW."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def embedding_partition_name(code: str, jurisdiction_id: uuid.UUID) -> str:
    """`provision_embeddings_p_<code>_<id prefix>`: the code so an operator can
    read it, the id so two codes that fold alike cannot share a table."""
    folded = re.sub(r"[^a-z0-9]", "_", code.lower())[:16]
    return f"provision_embeddings_p_{folded}_{uuid.UUID(str(jurisdiction_id)).hex[:8]}"


async def ensure_embedding_partition(
    session: AsyncSession, jurisdiction_id: uuid.UUID, code: str
) -> None:
    """Create the jurisdiction's partition if it is missing. Instant on a new
    jurisdiction; an embedding for one with no partition fails at insert instead
    of landing somewhere it cannot be found."""
    # DDL takes no bind parameters; a UUID renders as its own literal.
    name = embedding_partition_name(code, jurisdiction_id)
    await session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{name}" '
            f"PARTITION OF provision_embeddings FOR VALUES IN ('{uuid.UUID(str(jurisdiction_id))}')"
        )
    )
