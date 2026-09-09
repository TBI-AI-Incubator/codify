"""Core corpus schema baseline.

Root of the core migration chain. Builds today's corpus schema (as of the
apps/api chain at head 0123) in one step, from the checked-in ``0001_core_baseline.sql``.
The SQL was extracted from a clean full-chain migration and verified against it
by a fresh-database schema diff. New corpus schema changes extend this chain, not
the product chain.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import op

_log = logging.getLogger("alembic.runtime.migration")

revision: str = "0001_core_baseline"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

_SQL_PATH = Path(__file__).with_suffix(".sql")

# Dropped in reverse of creation. CASCADE clears the FKs and triggers first.
_TABLES = (
    "run_artifacts",
    "runs",
    "translation_runs",
    "acquisitions",
    "events",
    "feedback",
    "lens_run_directives",
    "lens_runs",
    "scheme_matches",
    "adjudications",
    "repair_proposals",
    "page_read_disputes",
    "examinations",
    "finding_notes",
    "structural_findings",
    "findings",
    "version_unit_embeddings",
    "provision_embeddings",
    "lifecycle_events",
    "law_links",
    "registry_works",
    "amendment_effects",
    "cross_references",
    "provisions",
    "sections",
    "page_reads",
    "version_source_texts",
    "versions",
    "laws",
    "source_documents",
    "jurisdictions",
    "anticorruption_schemes",
    "anticorruption_phenomena",
    "anticorruption_factors",
)
_FUNCTIONS = (
    "set_updated_at",
    "enforce_versions_immutable",
    "enforce_events_append_only",
    "examinations_no_update",
    "page_read_disputes_no_update",
)


def upgrade() -> None:
    bind = op.get_bind()
    # Standalone: the corpus tables are absent, so build the whole schema.
    if bind.exec_driver_sql("SELECT to_regclass('public.versions')").scalar() is None:
        # Percent doubled: the driver reads `%s` in a statement as a parameter
        # placeholder and rewrote the `format()` calls inside trigger functions,
        # so their messages printed `$5` where the row id belonged.
        bind.exec_driver_sql(_SQL_PATH.read_text().replace("%", "%%"))
        return
    # Adoption: the product chain already built the corpus tables, so record the
    # version without re-creating them. `versions` alone is not enough, it has
    # existed since product revision 0001, so confirm the schema is actually at
    # this baseline's head before stamping. `provisions_bm25_idx` is created by
    # product migration 0123, the product head this baseline snapshots; if it is
    # missing the product schema predates this baseline, and silently stamping
    # would report the core chain at head over an older schema. Fail loudly.
    if bind.exec_driver_sql("SELECT to_regclass('public.provisions_bm25_idx')").scalar() is None:
        raise RuntimeError(
            "Corpus tables exist but this baseline's head schema is not present "
            "(provisions_bm25_idx, from product migration 0123, is missing). Bring "
            "the product chain to head before adopting the core chain."
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Symmetric to upgrade's self-adoption. If the product chain is present (its
    # version table exists), it owns the corpus tables and this baseline only
    # ever recorded a version against them, dropping them would destroy product
    # data, so leave the schema and let alembic clear the core version row.
    # Only a standalone core database, which this baseline actually built, drops.
    product_chain = bind.exec_driver_sql("SELECT to_regclass('public.alembic_version')").scalar()
    if product_chain is not None:
        _log.info(
            "core baseline downgrade: product chain present; retaining the shared "
            "corpus schema and clearing only the core version record."
        )
        return
    for table in _TABLES:
        bind.exec_driver_sql(f"DROP TABLE IF EXISTS public.{table} CASCADE")
    for func in _FUNCTIONS:
        bind.exec_driver_sql(f"DROP FUNCTION IF EXISTS public.{func}() CASCADE")
    # Extensions (vector, pgcrypto, pg_textsearch) are left in place; they may be
    # shared with other schemas in the same database.
