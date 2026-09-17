"""One `provision_embeddings` partition per jurisdiction, carrying its own HNSW.

The partition is created by a trigger on `jurisdictions` as the row is written
(migration 0020), so every writer of a jurisdiction gets one; this names it."""

from __future__ import annotations

import uuid


def embedding_partition_name(jurisdiction_id: uuid.UUID) -> str:
    """`provision_embeddings_p_<id>`: the whole id, so no two jurisdictions can
    share a table; the code is one join away when a human needs it."""
    return f"provision_embeddings_p_{uuid.UUID(str(jurisdiction_id)).hex}"
