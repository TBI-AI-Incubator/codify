"""`provision_embeddings` is partitioned by jurisdiction, one HNSW per partition.

No partition is created at run time: `CREATE TABLE ... PARTITION OF` holds the
parent exclusively until its transaction commits, and `ATTACH PARTITION` clones
the parent's foreign keys under a lock that waits on every open writer of
`provisions` and `jurisdictions`. Either stops searches or ingests for as long as
the caller runs. A jurisdiction created after migration 0020 therefore writes to
the DEFAULT partition, which has its own index, until a migration promotes it:
create the table unattached, move its rows out of DEFAULT, attach it.
"""

from __future__ import annotations

import uuid

DEFAULT_PARTITION = "provision_embeddings_p_default"


def embedding_partition_name(jurisdiction_id: uuid.UUID) -> str:
    """`provision_embeddings_p_<id>`: the whole id, so no two jurisdictions can
    share a table; the code is one join away when a human needs it."""
    return f"provision_embeddings_p_{uuid.UUID(str(jurisdiction_id)).hex}"


def promote_sql(jurisdiction_id: uuid.UUID) -> list[str]:
    """The statements a migration runs, in one transaction with writers drained,
    to give a jurisdiction that grew up in DEFAULT a partition of its own."""
    name = embedding_partition_name(jurisdiction_id)
    # A uuid is its own safe literal, and the identifier is built from it.
    literal = str(uuid.UUID(str(jurisdiction_id)))
    return [
        f'CREATE TABLE "{name}" (LIKE provision_embeddings INCLUDING ALL)',
        f'INSERT INTO "{name}" SELECT * FROM {DEFAULT_PARTITION} '  # noqa: S608
        f"WHERE jurisdiction_id = '{literal}'",
        f"DELETE FROM {DEFAULT_PARTITION} WHERE jurisdiction_id = '{literal}'",  # noqa: S608
        f"ALTER TABLE provision_embeddings ATTACH PARTITION \"{name}\" FOR VALUES IN ('{literal}')",
    ]
