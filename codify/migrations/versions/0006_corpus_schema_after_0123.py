"""Corpus schema the baseline's snapshot predates.

Cut at product head 0123; five migrations since changed tables this chain owns.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0006_corpus_schema_after_0123"
down_revision: str | Sequence[str] | None = "0005_goods_code_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Product 0131 added `reextract_refs` to a vocabulary of 29.
_RUN_KINDS = (
    "ingest",
    "translation",
    "lens",
    "batch",
    "acquire",
    "eu_acquis_discover",
    "admin_batch",
    "embed",
    "regen_summary",
    "repair",
    "reextract_refs",
)


_BASELINE_SQL = Path(__file__).with_name("0001_core_baseline.sql")


def _baseline_function(name: str) -> str:
    """The function as the baseline defines it, read rather than restated here."""
    text = _BASELINE_SQL.read_text()
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}()")
    end = text.index("$function$", text.index("$function$", start) + 1)
    return text[start : end + len("$function$")]


# The last product revision this migration installs anything from. Below it, the
# product chain would later create the same objects unguarded and fail.
_NEEDS_PRODUCT_AT = "0127"


def upgrade() -> None:
    bind = op.get_bind()
    product = ""
    if bind.exec_driver_sql("SELECT to_regclass('public.alembic_version')").scalar() is not None:
        product = bind.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() or ""
        if product < _NEEDS_PRODUCT_AT:
            raise RuntimeError(
                f"the product chain is at {product}: its 0124 to 0127 create these "
                "objects with no guard, so they would fail against what this "
                "installs. Bring the product chain to head first."
            )

    # --- product 0124: the workspace a run belongs to -------------------------
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS org_id TEXT")
    op.execute(
        "UPDATE runs SET org_id = params->>'org_id' "
        "WHERE org_id IS NULL AND params->>'org_id' IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_runs_org_created ON runs (org_id, created_at DESC)")

    # --- product 0126: title search over `laws` ------------------------------
    op.execute("ALTER TABLE laws ADD COLUMN IF NOT EXISTS title_tokens TEXT")
    op.execute("ALTER TABLE laws ADD COLUMN IF NOT EXISTS title_search_pipeline_version INTEGER")
    # Generated, so it rewrites the table; the BM25 index is built after it or
    # the rewrite rebuilds that index too.
    op.execute(
        """
        ALTER TABLE laws ADD COLUMN IF NOT EXISTS title_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', coalesce(title_tokens, ''))) STORED
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS laws_title_tsv_idx ON laws USING GIN (title_tsv)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS laws_title_bm25_idx ON laws
        USING bm25 (title_tokens) WITH (text_config='simple', k1=1.2, b=0.75)
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION laws_title_tokens_stale() RETURNS trigger AS $$
        BEGIN
            -- Only when the write does not carry new tokens with it: a writer
            -- changing a name and its tokens together is keeping them in step.
            IF (NEW.title IS DISTINCT FROM OLD.title
                OR NEW.short_title IS DISTINCT FROM OLD.short_title
                OR NEW.title_translations IS DISTINCT FROM OLD.title_translations)
               AND NEW.title_tokens IS NOT DISTINCT FROM OLD.title_tokens THEN
                NEW.title_search_pipeline_version := NULL;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE OR REPLACE TRIGGER laws_title_tokens_stale_trg BEFORE UPDATE ON laws
        FOR EACH ROW EXECUTE FUNCTION laws_title_tokens_stale()
        """
    )

    # --- repair: the baseline stored these with their `%s` rewritten ---------
    # A database stamped at 0001 never runs the corrected baseline again, so its
    # immutability trigger still reports `$5` where the row id belongs.
    op.execute(_baseline_function("enforce_versions_immutable"))

    # --- product 0127: `events` was erasable one TRUNCATE at a time ----------
    # OLD/NEW are unassigned at statement level, so name the row only when there
    # is one; the baseline's version reads them unconditionally.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_events_append_only() RETURNS TRIGGER AS $$
        BEGIN
            IF TG_LEVEL = 'STATEMENT' THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = format('events table is append-only (op=%s)', TG_OP);
            END IF;
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = format('events table is append-only (op=%s, id=%s)', TG_OP,
                                 COALESCE(OLD.id::text, NEW.id::text));
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE OR REPLACE TRIGGER events_no_truncate BEFORE TRUNCATE ON events
        FOR EACH STATEMENT EXECUTE FUNCTION enforce_events_append_only()
        """
    )

    # --- product 0130: BM25 length normalisation ----------------------------
    # Rebuilding this index is expensive, so only where the parameters differ.
    opts = bind.exec_driver_sql(
        "SELECT array_to_string(reloptions, ',') FROM pg_class "
        "WHERE relname = 'provisions_bm25_idx'"
    ).scalar()
    if opts is not None and not {"k1=1.6", "b=0.5"} <= set(opts.split(",")):
        op.execute("DROP INDEX provisions_bm25_idx")
        op.execute(
            """
            CREATE INDEX provisions_bm25_idx ON provisions
            USING bm25 (search_tokens) WITH (text_config='simple', k1=1.6, b=0.5)
            """
        )

    # --- product 0131: a run kind the vocabulary did not admit ---------------
    current = (
        bind.exec_driver_sql(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'runs_kind_check'"
        ).scalar()
        or ""
    )
    if "reextract_refs" not in current:
        # Appended to what is in force, in that order: rebuilding must only add
        # to the vocabulary, and the rendered constraint should match the
        # product chain's rather than differ by sort order alone.
        have = re.findall(r"'([a-z_]+)'", current)
        kinds = have + [k for k in _RUN_KINDS if k not in have]
        op.execute("ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_kind_check")
        op.execute(
            "ALTER TABLE runs ADD CONSTRAINT runs_kind_check CHECK (kind IN ("
            + ", ".join(f"'{k}'" for k in kinds)
            + "))"
        )


# The product migration that owns each delta, so a reversal only leaves behind
# what the product chain is recorded as having applied itself.
_OWNED_BY_PRODUCT = {
    "0124": (
        "DROP INDEX IF EXISTS ix_runs_org_created",
        "ALTER TABLE runs DROP COLUMN IF EXISTS org_id",
    ),
    "0126": (
        "DROP TRIGGER IF EXISTS laws_title_tokens_stale_trg ON laws",
        "DROP FUNCTION IF EXISTS laws_title_tokens_stale()",
        "DROP INDEX IF EXISTS laws_title_bm25_idx",
        "DROP INDEX IF EXISTS laws_title_tsv_idx",
        "ALTER TABLE laws DROP COLUMN IF EXISTS title_tsv",
        "ALTER TABLE laws DROP COLUMN IF EXISTS title_search_pipeline_version",
        "ALTER TABLE laws DROP COLUMN IF EXISTS title_tokens",
    ),
    "0127": ("DROP TRIGGER IF EXISTS events_no_truncate ON events",),
}


def reversals_for(product_revision: str) -> list[str]:
    """What to undo, given the revision the product chain records.

    Revisions are zero-padded, so the recorded one answers directly.
    """
    return [
        statement
        for owner, statements in _OWNED_BY_PRODUCT.items()
        if product_revision < owner
        for statement in statements
    ]


def downgrade() -> None:
    bind = op.get_bind()
    product = ""
    if bind.exec_driver_sql("SELECT to_regclass('public.alembic_version')").scalar() is not None:
        product = bind.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() or ""
    for statement in reversals_for(product):
        op.execute(statement)
    # The BM25 parameters and the run-kind vocabulary are left as they are: both
    # are values rather than objects, and the older ones are not more correct.
