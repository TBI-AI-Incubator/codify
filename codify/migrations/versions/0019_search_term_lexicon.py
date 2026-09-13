"""Create the search lexicon on standalone Core installations.

Platform 0129 already creates this table. Adopt its compatible definition
without rebuilding its contents; older product chains must upgrade first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_search_term_lexicon"
down_revision: str | Sequence[str] | None = "0018_annex_admission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT to_regclass('public.alembic_version')").scalar() is not None:
        product = bind.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() or ""
        if product < "0129":
            raise RuntimeError("Upgrade the Platform migration chain through 0129 before Core 0019")

    inspector = sa.inspect(bind)
    if inspector.has_table("search_terms", schema="public"):
        columns = {
            column["name"]: column
            for column in inspector.get_columns("search_terms", schema="public")
        }
        if (
            set(columns) != {"term", "ndoc"}
            or not isinstance(columns["term"]["type"], sa.Text)
            or not isinstance(columns["ndoc"]["type"], sa.Integer)
            or columns["term"]["nullable"]
            or columns["ndoc"]["nullable"]
            or columns["term"]["default"] is not None
            or columns["ndoc"]["default"] not in {"0", "'0'::integer"}
            or inspector.get_pk_constraint("search_terms", schema="public")["constrained_columns"]
            != ["term"]
        ):
            raise RuntimeError("Incompatible existing search_terms definition")
        indexes = bind.exec_driver_sql(
            "SELECT pg_get_indexdef(i.indexrelid), i.indisvalid, i.indisready "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE i.indrelid = 'public.search_terms'::regclass "
            "AND c.relname = 'search_terms_trgm_idx'"
        ).one_or_none()
        if indexes is None or tuple(indexes) != (
            "CREATE INDEX search_terms_trgm_idx ON public.search_terms "
            "USING gin (term gin_trgm_ops)",
            True,
            True,
        ):
            raise RuntimeError("Missing or incompatible search_terms trigram index")
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "search_terms",
        sa.Column("term", sa.Text(), primary_key=True),
        sa.Column("ndoc", sa.Integer(), nullable=False, server_default="0"),
        schema="public",
    )
    op.execute(
        "CREATE INDEX search_terms_trgm_idx ON public.search_terms USING gin (term gin_trgm_ops)"
    )
    op.execute(
        "INSERT INTO public.search_terms (term, ndoc) "
        "SELECT t.term, count(DISTINCT p.id) FROM public.provisions p, "
        "LATERAL unnest(string_to_array(p.search_tokens, ' ')) AS t(term) "
        "WHERE p.search_tokens IS NOT NULL AND octet_length(t.term) <= 128 GROUP BY t.term"
    )


def downgrade() -> None:
    # Platform may own this table and live applications still maintain it.
    # Retain the derived lexicon; re-upgrade validates and adopts it.
    pass
