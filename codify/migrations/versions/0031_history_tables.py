"""Add the dated statute book: publisher texts, immutable but for their work assignment,
and the expressions, intervals and links derived from them per work.
"""

from __future__ import annotations

from alembic import op

revision: str = "0031_history_tables"
down_revision: str | None = "0030_registry_work_records"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = (
    "history_withdrawals",
    "provision_links",
    "provision_intervals",
    "dated_expressions",
    "publisher_texts",
    "history_works",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS history_works (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            jurisdiction_id uuid NOT NULL REFERENCES jurisdictions(id) ON DELETE CASCADE,
            law_id uuid REFERENCES laws(id) ON DELETE SET NULL,
            registry_work_id uuid REFERENCES registry_works(id) ON DELETE SET NULL,
            converter_rev text,
            builder_rev text,
            built_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS history_works_jurisdiction_idx "
        "ON history_works (jurisdiction_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS history_works_law_idx "
        "ON history_works (law_id) WHERE law_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS publisher_texts (
            jurisdiction_id uuid NOT NULL REFERENCES jurisdictions(id) ON DELETE CASCADE,
            source_ref text NOT NULL,
            source_sha256 text NOT NULL,
            language text NOT NULL,
            publisher_valid_from date,
            publisher_valid_to date,
            published_on date,
            publisher_group text,
            source_meta jsonb NOT NULL DEFAULT '{}',
            source_locator text,
            work_id uuid REFERENCES history_works(id) ON DELETE SET NULL,
            work_rule text,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (jurisdiction_id, source_ref, source_sha256)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS publisher_texts_work_idx "
        "ON publisher_texts (work_id, publisher_valid_from) WHERE work_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS publisher_texts_recorded_idx "
        "ON publisher_texts (jurisdiction_id, recorded_at)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.enforce_publisher_texts_immutable()
         RETURNS trigger
         LANGUAGE plpgsql
        AS $function$
        BEGIN
            IF (NEW.jurisdiction_id, NEW.source_ref, NEW.source_sha256, NEW.language,
                NEW.publisher_valid_from, NEW.publisher_valid_to, NEW.published_on,
                NEW.publisher_group, NEW.source_meta, NEW.source_locator, NEW.recorded_at)
               IS NOT DISTINCT FROM
               (OLD.jurisdiction_id, OLD.source_ref, OLD.source_sha256, OLD.language,
                OLD.publisher_valid_from, OLD.publisher_valid_to, OLD.published_on,
                OLD.publisher_group, OLD.source_meta, OLD.source_locator, OLD.recorded_at)
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = format(
                    'publisher_texts row is immutable apart from work_id and work_rule (ref=%s)',
                    OLD.source_ref
                );
        END;
        $function$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE TRIGGER publisher_texts_enforce_immutable
        BEFORE UPDATE ON publisher_texts
        FOR EACH ROW EXECUTE FUNCTION public.enforce_publisher_texts_immutable()
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dated_expressions (
            work_id uuid NOT NULL REFERENCES history_works(id) ON DELETE CASCADE,
            language text NOT NULL,
            variant text NOT NULL DEFAULT '',
            valid_from date NOT NULL,
            valid_to date,
            jurisdiction_id uuid NOT NULL,
            source_ref text,
            source_sha256 text,
            akn_xml text COMPRESSION lz4,
            version_id uuid REFERENCES versions(id) ON DELETE SET NULL,
            gap_reason text,
            derivation text NOT NULL,
            confidence real,
            official boolean NOT NULL,
            derived_from jsonb,
            converter_rev text NOT NULL,
            builder_rev text NOT NULL,
            recorded_at timestamptz NOT NULL,
            PRIMARY KEY (work_id, language, variant, valid_from),
            FOREIGN KEY (jurisdiction_id, source_ref, source_sha256)
                REFERENCES publisher_texts,
            CHECK (valid_to IS NULL OR valid_to > valid_from),
            CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
            CHECK ((akn_xml IS NULL) = (gap_reason IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS dated_expressions_recorded_idx "
        "ON dated_expressions (jurisdiction_id, recorded_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS dated_expressions_version_idx "
        "ON dated_expressions (version_id) WHERE version_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS provision_intervals (
            work_id uuid NOT NULL REFERENCES history_works(id) ON DELETE CASCADE,
            language text NOT NULL,
            variant text NOT NULL DEFAULT '',
            akn_eid text NOT NULL,
            valid_from date NOT NULL,
            valid_to date,
            jurisdiction_id uuid NOT NULL,
            akn_type text NOT NULL,
            position integer NOT NULL,
            text text COMPRESSION lz4 NOT NULL,
            text_sha256 bytea NOT NULL,
            text_norm_sha256 bytea NOT NULL,
            derivation text NOT NULL,
            confidence real,
            official boolean NOT NULL,
            opened_by text,
            converter_rev text NOT NULL,
            builder_rev text NOT NULL,
            recorded_at timestamptz NOT NULL,
            PRIMARY KEY (work_id, language, variant, akn_eid, valid_from),
            CHECK (valid_to IS NULL OR valid_to > valid_from),
            CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS provision_intervals_recorded_idx "
        "ON provision_intervals (jurisdiction_id, recorded_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS provision_intervals_from_idx "
        "ON provision_intervals (jurisdiction_id, valid_from)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS provision_intervals_to_idx "
        "ON provision_intervals (jurisdiction_id, valid_to) WHERE valid_to IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS provision_links (
            work_id uuid NOT NULL REFERENCES history_works(id) ON DELETE CASCADE,
            language text NOT NULL,
            variant text NOT NULL DEFAULT '',
            linked_on date NOT NULL,
            from_eid text NOT NULL,
            to_eid text NOT NULL,
            rule text NOT NULL,
            confidence real,
            builder_rev text NOT NULL,
            recorded_at timestamptz NOT NULL,
            PRIMARY KEY (work_id, language, variant, linked_on, to_eid, from_eid)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS provision_links_from_idx "
        "ON provision_links (work_id, language, variant, from_eid)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS history_withdrawals (
            jurisdiction_id uuid NOT NULL,
            work_id uuid NOT NULL REFERENCES history_works(id) ON DELETE CASCADE,
            row_kind text NOT NULL,
            language text NOT NULL,
            variant text NOT NULL,
            akn_eid text,
            from_eid text,
            valid_from date NOT NULL,
            builder_rev text NOT NULL,
            withdrawn_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS history_withdrawals_polling_idx "
        "ON history_withdrawals (jurisdiction_id, withdrawn_at)"
    )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS public.enforce_publisher_texts_immutable()")
