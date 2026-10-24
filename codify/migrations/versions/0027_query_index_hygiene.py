"""Drop indexes no reader uses; add the ones foreign keys and lookups lack.

Dropped: the GIN over `provisions.text_search`, which lexical search stopped
reading when it moved to `search_tsv`; an HNSW over `version_unit_embeddings`
that the chain never created and no query orders by distance; and three
single-prefix btrees whose leading columns a wider index on the same table
already serves. The `text_search` column stays, as ad hoc readers outside this
package still query it; only its index goes.

Added: `registry_works.law_id` and `repair_proposals.run_id`, so the
`ON DELETE SET NULL` of a law or run is an index probe rather than a table
scan, and `runs.params->>'retried_from'`, for following a retry chain.

`provisions` vacuums after 2% new rows rather than 20%, keeping its visibility
map current enough for index-only scans on a table that mostly grows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_query_index_hygiene"
down_revision: str | Sequence[str] | None = "0026_provisions_created_at_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Restored by the downgrade, in the chain's own definitions.
_REDUNDANT = {
    "provisions_text_search_idx": "provisions USING gin (text_search)",
    "sections_version_id_idx": "sections (version_id)",
    "annex_rows_version_idx": "annex_rows (version_id)",
    "law_links_source_idx": "law_links (jurisdiction_id, source_ref)",
}

# Never created by this chain, so the downgrade does not create it either.
_UNTRACKED = "version_unit_embeddings_hnsw_idx"

_ADDED = {
    "registry_works_law_id_idx": "registry_works (law_id) WHERE law_id IS NOT NULL",
    "repair_proposals_run_id_idx": "repair_proposals (run_id)",
    "runs_retried_from_idx": (
        "runs ((params->>'retried_from')) WHERE (params->>'retried_from') IS NOT NULL"
    ),
}


def _index_valid(name: str) -> bool | None:
    """Whether the index exists and is usable; None when it does not exist."""
    row = (
        op.get_bind()
        .execute(
            sa.text("SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid = to_regclass(:n)"),
            {"n": name},
        )
        .one_or_none()
    )
    return None if row is None else bool(row[0])


def _build(name: str, definition: str) -> None:
    # A cancelled concurrent build leaves an unusable index under the name.
    if _index_valid(name) is False:
        op.execute(f"DROP INDEX CONCURRENTLY {name}")
    op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {definition}")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name in [_UNTRACKED, *_REDUNDANT]:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        for name, definition in _ADDED.items():
            _build(name, definition)
    op.execute("ALTER TABLE provisions SET (autovacuum_vacuum_insert_scale_factor = 0.02)")


def downgrade() -> None:
    op.execute("ALTER TABLE provisions RESET (autovacuum_vacuum_insert_scale_factor)")
    with op.get_context().autocommit_block():
        for name, definition in _REDUNDANT.items():
            _build(name, definition)
        for name in _ADDED:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
