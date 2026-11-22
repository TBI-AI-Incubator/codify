"""Add official_sources, official_anchors and official_ids.

`official_sources` records where each version's text came from at its publisher:
the publisher's own act and edition ids, the fetch URL and the licence. A source
fragment that cannot be derived from an eId is kept in `official_anchors`, keyed
by eId so it survives a rederive. `official_ids` maps a publisher's id to a work
URI with no law key, so an id for an act the corpus does not hold is kept.
"""

from __future__ import annotations

from alembic import op

revision: str = "0029_official_sources"
down_revision: str | None = "0028_goods_code_scope"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS official_sources (
            version_id uuid PRIMARY KEY REFERENCES versions(id) ON DELETE CASCADE,
            publisher text NOT NULL,
            work_id text NOT NULL,
            edition_id text,
            edition_date date,
            fetch_url text,
            retrieved_at timestamptz,
            content_sha256 text,
            licence text,
            recorded_by text NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS official_sources_work_idx "
        "ON official_sources (publisher, work_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS official_anchors (
            version_id uuid NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
            akn_eid text NOT NULL,
            fragment text NOT NULL,
            PRIMARY KEY (version_id, akn_eid)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS official_anchors_fragment_idx "
        "ON official_anchors (version_id, fragment)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS official_ids (
            publisher text NOT NULL,
            id text NOT NULL,
            frbr_work_uri text NOT NULL,
            id_kind text NOT NULL,
            PRIMARY KEY (publisher, id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS official_ids_work_idx ON official_ids (frbr_work_uri)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS official_ids")
    op.execute("DROP TABLE IF EXISTS official_anchors")
    op.execute("DROP TABLE IF EXISTS official_sources")
