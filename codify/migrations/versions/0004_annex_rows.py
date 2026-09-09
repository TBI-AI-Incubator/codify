"""Add annex_rows. A row is what an amending act replaces but not a
provision, so it is keyed like any citable unit, on version and eId."""

from __future__ import annotations

from alembic import op

revision: str = "0004_annex_rows"
down_revision: str | None = "0003_goods_code_references"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS annex_rows (
            id uuid PRIMARY KEY,
            version_id uuid NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
            akn_eid text NOT NULL,
            table_eid text NOT NULL,
            row_index integer NOT NULL,
            cells jsonb NOT NULL DEFAULT '[]'::jsonb,
            lineage text[] NOT NULL DEFAULT '{}',
            mechanisms text[] NOT NULL DEFAULT '{}',
            resolved_text text NOT NULL,
            search_tokens text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT annex_rows_version_eid_key UNIQUE (version_id, akn_eid)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS annex_rows_version_idx ON annex_rows (version_id)")
    # Reading one table back in document order, which is how it renders.
    op.execute(
        "CREATE INDEX IF NOT EXISTS annex_rows_table_idx "
        "ON annex_rows (version_id, table_eid, row_index)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS annex_rows")
