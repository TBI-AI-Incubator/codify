"""Add goods_code_references, so a code lookup need not scan AKN."""

from __future__ import annotations

from alembic import op

revision: str = "0003_goods_code_references"
down_revision: str | None = "0002_drop_escalation_rung"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS goods_code_references (
            id uuid PRIMARY KEY,
            version_id uuid NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
            akn_eid text NOT NULL,
            system text NOT NULL,
            code text NOT NULL,
            surface text NOT NULL,
            partial boolean NOT NULL DEFAULT false,
            source text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT goods_code_references_system_known
                CHECK (system IN ('hs', 'cn', 'taric')),
            CONSTRAINT goods_code_references_source_known
                CHECK (source IN ('column', 'prose')),
            CONSTRAINT goods_code_references_code_digits
                CHECK (code ~ '^[0-9]{4,10}$')
        )
        """
    )
    # "which measures cover this code" is the question; the prefix match is
    # what makes an HS6 lookup reach the CN8 codes under it.
    op.execute(
        "CREATE INDEX IF NOT EXISTS goods_code_references_code_idx "
        "ON goods_code_references (code text_pattern_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS goods_code_references_version_idx "
        "ON goods_code_references (version_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS goods_code_references")
