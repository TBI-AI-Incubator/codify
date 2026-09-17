"""Partition `provision_embeddings` by jurisdiction, with `version_id` beside it.

A scoped dense search filtered by version through the `provisions` join, so the
planner either scanned every embedding in scope exactly or walked the one global
HNSW graph and gave up at `hnsw.max_scan_tuples` with a handful of in-scope rows.
With the scope keys on the embeddings table, each jurisdiction's partition
carries its own HNSW index and the version filter runs inside the index scan.

The vector indexes are not built here. The parent index is created `ON ONLY`, so
a partition created later gets one automatically, while partitions that already
hold rows are built `CONCURRENTLY` from the runbook and attached; on a corpus of
ten million vectors that is hours, and no deploy can wait on it. Until a
partition's index is attached, searches on it fall back to the exact scan they
run today.

Writers are blocked for the copy (SHARE lock on the old table); drain them first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from codify.storage.partitions import embedding_partition_name

revision: str = "0020_embeddings_by_jurisdiction"
down_revision: str | Sequence[str] | None = "0019_search_term_lexicon"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HNSW = "USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"


def _drop_name_suffixes(table: str) -> None:
    """Constraints named while the old table still held the name got a digit,
    on the table and on its partitions. One rename per query: a rename on the
    parent reaches some children and not others, so a list goes stale."""
    bind = op.get_bind()
    query = sa.text(
        "SELECT c.conrelid::regclass::text, c.conname FROM pg_constraint c "
        "JOIN pg_partition_tree(to_regclass(:table)) t ON t.relid = c.conrelid "
        "WHERE c.conname ~ :pattern ORDER BY t.level, c.conname LIMIT 1"
    )
    params = {"table": table, "pattern": f"^{table}_.*[a-z]\\d+$"}
    while row := bind.execute(query, params).one_or_none():
        relation, name = row
        bare = name.rstrip("0123456789")
        op.execute(f'ALTER TABLE {relation} RENAME CONSTRAINT "{name}" TO "{bare}"')


def upgrade() -> None:
    op.execute("LOCK TABLE provision_embeddings IN SHARE MODE")
    # Out of the way first, so the partitioned table takes the natural names.
    op.execute("ALTER TABLE provision_embeddings RENAME TO provision_embeddings_old")
    for index in ("pkey", "provision_id_model_id_key", "hnsw_idx", "model_id_idx"):
        op.execute(
            f"ALTER INDEX IF EXISTS provision_embeddings_{index} "
            f"RENAME TO provision_embeddings_old_{index}"
        )
    op.execute(
        """
        CREATE TABLE provision_embeddings (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            provision_id uuid NOT NULL REFERENCES provisions(id) ON DELETE CASCADE,
            embedding halfvec(768) NOT NULL,
            model_id text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            version_id uuid NOT NULL,
            jurisdiction_id uuid NOT NULL REFERENCES jurisdictions(id),
            PRIMARY KEY (id, jurisdiction_id),
            CONSTRAINT provision_embeddings_provision_model_key
                UNIQUE (provision_id, model_id, jurisdiction_id)
        ) PARTITION BY LIST (jurisdiction_id)
        """
    )
    for (jurisdiction_id,) in op.get_bind().execute(sa.text("SELECT id FROM jurisdictions")).all():
        op.execute(
            f'CREATE TABLE "{embedding_partition_name(jurisdiction_id)}" '
            f"PARTITION OF provision_embeddings FOR VALUES IN ('{jurisdiction_id}')"
        )
    op.execute(
        """
        INSERT INTO provision_embeddings
            (id, provision_id, embedding, model_id, created_at, version_id, jurisdiction_id)
        SELECT e.id, e.provision_id, e.embedding, e.model_id, e.created_at,
               p.version_id, l.jurisdiction_id
        FROM provision_embeddings_old e
        JOIN provisions p ON p.id = e.provision_id
        JOIN versions v ON v.id = p.version_id
        JOIN laws l ON l.id = v.law_id
        """
    )
    op.execute("DROP TABLE provision_embeddings_old")
    _drop_name_suffixes("provision_embeddings")
    op.execute(
        "CREATE INDEX provision_embeddings_version_id_idx ON provision_embeddings (version_id)"
    )
    op.execute("CREATE INDEX provision_embeddings_model_id_idx ON provision_embeddings (model_id)")
    op.execute(f"CREATE INDEX provision_embeddings_hnsw_idx ON ONLY provision_embeddings {_HNSW}")


def downgrade() -> None:
    op.execute("LOCK TABLE provision_embeddings IN SHARE MODE")
    op.execute("ALTER TABLE provision_embeddings RENAME TO provision_embeddings_old")
    for index in ("pkey", "provision_model_key", "hnsw_idx", "model_id_idx", "version_id_idx"):
        op.execute(
            f"ALTER INDEX IF EXISTS provision_embeddings_{index} "
            f"RENAME TO provision_embeddings_old_{index}"
        )
    op.execute(
        """
        CREATE TABLE provision_embeddings (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            provision_id uuid NOT NULL REFERENCES provisions(id) ON DELETE CASCADE,
            embedding halfvec(768) NOT NULL,
            model_id text NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (provision_id, model_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO provision_embeddings (id, provision_id, embedding, model_id, created_at)
        SELECT id, provision_id, embedding, model_id, created_at FROM provision_embeddings_old
        """
    )
    op.execute("DROP TABLE provision_embeddings_old")
    _drop_name_suffixes("provision_embeddings")
    op.execute("CREATE INDEX provision_embeddings_model_id_idx ON provision_embeddings (model_id)")
    # The global HNSW is hours on a full corpus; rebuild it by hand if the
    # downgrade is meant to serve searches.
