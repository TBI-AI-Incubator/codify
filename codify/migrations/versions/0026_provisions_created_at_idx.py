"""Index provisions by (version_id, created_at); drop the version_id-only index.

`SELECT max(created_at) FROM provisions WHERE version_id = $1` read every
provision of the version, and run once per version over a large set that was
minutes of random reads. With `created_at` second in the key it is one index
descent. The new key leads with `version_id`, so the old single-column index
serves nothing the new one does not.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_provisions_created_at_idx"
down_revision: str | Sequence[str] | None = "0025_atlas_build_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW = "provisions_version_id_created_at_idx"
_OLD = "provisions_version_id_idx"


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


def _build(name: str, columns: str) -> None:
    # A cancelled concurrent build leaves an unusable index under the name.
    if _index_valid(name) is False:
        op.execute(f"DROP INDEX CONCURRENTLY {name}")
    op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON provisions ({columns})")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _build(_NEW, "version_id, created_at")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_OLD}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _build(_OLD, "version_id")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_NEW}")
