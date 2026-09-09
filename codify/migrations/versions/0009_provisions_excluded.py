"""Evidence-only exclusion on provisions: `excluded_from_pool` is true when the unit
carries no law by its shape and `exclusion_reason` says which shape (placeholder,
table, digits). NULL flag means not yet derived. The comparator's unit cache gains
the hash of the text it embedded, so a change in how text is folded misses the cache."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_provisions_excluded"
down_revision: str | Sequence[str] | None = "0008_versions_structure_halt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE provisions ADD COLUMN IF NOT EXISTS excluded_from_pool BOOLEAN")
    op.execute("ALTER TABLE provisions ADD COLUMN IF NOT EXISTS exclusion_reason TEXT")
    op.execute("ALTER TABLE version_unit_embeddings ADD COLUMN IF NOT EXISTS text_sha256 TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE version_unit_embeddings DROP COLUMN IF EXISTS text_sha256")
    op.execute("ALTER TABLE provisions DROP COLUMN IF EXISTS exclusion_reason")
    op.execute("ALTER TABLE provisions DROP COLUMN IF EXISTS excluded_from_pool")
