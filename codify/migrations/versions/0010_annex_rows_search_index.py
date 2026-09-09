"""Index annex_rows for text search, and stamp which tokeniser wrote a row.

The rows carried `search_tokens` from 0004 with nothing indexing them, so finding
one meant `ILIKE` over `resolved_text`: 4.6s per term over 1.66M rows. Mirrors the
provisions arm (0123), the tsvector deciding membership and BM25 ranking.

Without `search_pipeline_version` a tokeniser bump leaves those rows holding
tokens no query matches and no column to select them by. `smallint`, as provisions.

No GIN on the tsvector, unlike provisions: at 120,244 rows the planner drives both
plans off the BM25 index, and dropping the GIN changed nothing (1,238ms against
1,242ms). Add it if a plan is shown that uses it.

`k1=1.2, b=0.75` is the title setting rather than the provision one, because a row
is short and uniform like a title. Unmeasured for rows, so a default, not a win.

The generated column rewrites the table; `lock_timeout` keeps its ACCESS EXCLUSIVE
lock from queueing behind an ingest and blocking every reader.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_annex_rows_search_index"
down_revision: str | Sequence[str] | None = "0009_provisions_excluded"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BM25_OPTS = {"k1=1.2", "b=0.75"}
_TSV_EXPR = "to_tsvector('simple'::regconfig, COALESCE(search_tokens, ''::text))"


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("SET lock_timeout = '5s'")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")
    # `smallint`, matching the provisions stamp: the same tokeniser writes both.
    op.execute("ALTER TABLE annex_rows ADD COLUMN IF NOT EXISTS search_pipeline_version SMALLINT")

    # `IF NOT EXISTS` accepts whatever is already there, so a column generated
    # from the wrong expression, or not generated at all, would be stamped as
    # applied and never match. Read it and replace a divergent one.
    existing = bind.exec_driver_sql(
        "SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attribute a "
        "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = 'annex_rows'::regclass AND a.attname = 'search_tsv' "
        "AND NOT a.attisdropped"
    ).first()
    if existing is not None and (existing[0] or "").replace(" ", "") != _TSV_EXPR.replace(" ", ""):
        op.execute("ALTER TABLE annex_rows DROP COLUMN search_tsv")
        existing = None
    if existing is None:
        op.execute(
            f"ALTER TABLE annex_rows ADD COLUMN search_tsv tsvector "
            f"GENERATED ALWAYS AS ({_TSV_EXPR}) STORED"
        )

    # Same reasoning as 0006 for the provisions index: an index carrying other
    # parameters is not the one this migration documents.
    opts = bind.exec_driver_sql(
        "SELECT array_to_string(reloptions, ',') FROM pg_class "
        "WHERE relname = 'annex_rows_bm25_idx'"
    ).scalar()
    if opts is not None and not _BM25_OPTS <= set(opts.split(",")):
        op.execute("DROP INDEX annex_rows_bm25_idx")
        opts = None
    if opts is None:
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS annex_rows_bm25_idx ON annex_rows
            USING bm25 (search_tokens)
            WITH (text_config='simple', k1=1.2, b=0.75)
            """
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS annex_rows_bm25_idx")
    op.execute("ALTER TABLE annex_rows DROP COLUMN IF EXISTS search_tsv")
    op.execute("ALTER TABLE annex_rows DROP COLUMN IF EXISTS search_pipeline_version")
