"""Drop page_read_disputes.escalation_rung.

The DPI re-read ladder it recorded was measured and removed, and no code has
written the column since. Forward-only: the downgrade restores the column, not
any value.
"""

from __future__ import annotations

from alembic import op

revision: str = "0002_drop_escalation_rung"
down_revision: str | None = "0001_core_baseline"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # DROP COLUMN takes its CHECK constraint with it.
    op.execute("ALTER TABLE page_read_disputes DROP COLUMN IF EXISTS escalation_rung")


def downgrade() -> None:
    op.execute("ALTER TABLE page_read_disputes ADD COLUMN escalation_rung integer")
    op.execute(
        "ALTER TABLE page_read_disputes ADD CONSTRAINT "
        "page_read_disputes_escalation_rung_nonneg "
        "CHECK (escalation_rung IS NULL OR escalation_rung >= 0)"
    )
