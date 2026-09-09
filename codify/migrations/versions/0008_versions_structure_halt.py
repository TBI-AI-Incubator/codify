"""Record that the structurer refused this version, and which run wrote it.
Neither is recoverable from the AKN, so a re-grade would flip a refused document
back to clean. Added after the immutability trigger, so outside what it denies."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_versions_structure_halt"
down_revision: str | Sequence[str] | None = "0007_runs_kind_retire_law"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE versions ADD COLUMN IF NOT EXISTS structure_halt JSONB")
    # Which run wrote this row. No FK: `runs` is migrated by the other chain, and
    # a cross-chain constraint would order the two. NULL means pre-feature.
    op.execute("ALTER TABLE versions ADD COLUMN IF NOT EXISTS ingest_run_id UUID")
    op.execute("CREATE INDEX IF NOT EXISTS ix_versions_ingest_run_id ON versions (ingest_run_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_versions_ingest_run_id")
    op.execute("ALTER TABLE versions DROP COLUMN IF EXISTS ingest_run_id")
    op.execute("ALTER TABLE versions DROP COLUMN IF EXISTS structure_halt")
