"""Add goods_code_references.lineage: without its band a code cannot say
who it binds."""

from __future__ import annotations

from alembic import op

revision: str = "0005_goods_code_lineage"
down_revision: str | None = "0004_annex_rows"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE goods_code_references "
        "ADD COLUMN IF NOT EXISTS lineage text[] NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE goods_code_references DROP COLUMN IF EXISTS lineage")
