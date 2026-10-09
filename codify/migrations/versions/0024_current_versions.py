"""Add versions.is_current: each law's current original version, kept by trigger.

Search scoped to a jurisdiction passed every current version id as one array,
and the planner estimates `= ANY(array)` element by element against each
partition's statistics: 1.8 s of planning for 97 ms of execution across the
corpus. Joining on a flag plans in milliseconds.

Current means what `latest_versions_global` chose: the original (no parent) with
the latest expression date, then the latest ingest, then the lowest id. Those
columns are immutable (0008) except to a migration that disables the guard, so
an insert or a delete moves it, and an update of them is caught as well.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0024_current_versions"
down_revision: str | None = "0023_source_spans"
branch_labels: str | None = None
depends_on: str | None = None

_INDEX = "versions_current_law_idx"

_WINNER = """(
          SELECT c.id FROM versions c
          WHERE c.law_id = v.law_id AND c.parent_version_id IS NULL
          ORDER BY c.expression_date DESC, c.ingested_at DESC, c.id ASC
          LIMIT 1
      )"""

_REFRESH = f"""
CREATE OR REPLACE FUNCTION refresh_current_versions(law_ids uuid[]) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    -- Serialise per law on its row, in id order so two writers cannot deadlock.
    -- A row lock lives in the tuple, so a bulk delete cannot exhaust the lock
    -- table, and it does not conflict with an inserting version's key-share
    -- lock. Each statement below takes a fresh snapshot under READ COMMITTED;
    -- under a stricter level the unique index refuses a second current row.
    PERFORM 1 FROM laws WHERE id = ANY(law_ids) ORDER BY id FOR NO KEY UPDATE;
    -- Clear before set: the unique index is checked row by row.
    UPDATE versions v SET is_current = false
    WHERE v.law_id = ANY(law_ids) AND v.is_current
      AND v.id IS DISTINCT FROM {_WINNER};
    UPDATE versions v SET is_current = true
    WHERE v.law_id = ANY(law_ids) AND NOT v.is_current
      AND v.id = {_WINNER};
END $$;
"""

_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION versions_refresh_current() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM refresh_current_versions(
        ARRAY(SELECT DISTINCT law_id FROM changed WHERE parent_version_id IS NULL)
    );
    RETURN NULL;
END $$;
"""

_ROW_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION versions_refresh_current_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM refresh_current_versions(ARRAY[OLD.law_id, NEW.law_id]);
    RETURN NULL;
END $$;
"""


def upgrade() -> None:
    # `versions` is live: wait briefly for its lock, never queue reads behind it.
    op.execute("SET lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE versions ADD COLUMN IF NOT EXISTS is_current boolean NOT NULL DEFAULT false"
    )
    op.execute(_REFRESH)
    op.execute(_TRIGGER_FN)
    op.execute(_ROW_TRIGGER_FN)
    # Statement-level, so a bulk insert refreshes each law once.
    for event, table in (("INSERT", "NEW"), ("DELETE", "OLD")):
        name = f"versions_current_on_{event.lower()}"
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON versions")
        op.execute(
            f"CREATE TRIGGER {name} AFTER {event} ON versions "
            f"REFERENCING {table} TABLE AS changed FOR EACH STATEMENT "
            "EXECUTE FUNCTION versions_refresh_current()"
        )
    # Only a migration with the immutability guard off reaches this.
    op.execute("DROP TRIGGER IF EXISTS versions_current_on_update ON versions")
    op.execute(
        """
        CREATE TRIGGER versions_current_on_update
        AFTER UPDATE OF law_id, expression_date, ingested_at, parent_version_id ON versions
        FOR EACH ROW WHEN (
            (OLD.law_id, OLD.expression_date, OLD.ingested_at, OLD.parent_version_id)
            IS DISTINCT FROM
            (NEW.law_id, NEW.expression_date, NEW.ingested_at, NEW.parent_version_id)
        )
        EXECUTE FUNCTION versions_refresh_current_row()
        """
    )
    # After the triggers, in the same transaction, so no insert slips between.
    # The immutability guard would compare every row's akn_xml under this lock.
    op.execute("ALTER TABLE versions DISABLE TRIGGER versions_enforce_immutable")
    op.execute(
        """
        UPDATE versions v SET is_current = true
        FROM (
            SELECT DISTINCT ON (law_id) id FROM versions
            WHERE parent_version_id IS NULL
            ORDER BY law_id, expression_date DESC, ingested_at DESC, id ASC
        ) w
        WHERE v.id = w.id AND NOT v.is_current
        """
    )
    op.execute("ALTER TABLE versions ENABLE TRIGGER versions_enforce_immutable")
    with op.get_context().autocommit_block():
        # A cancelled concurrent build leaves an unusable index under the name.
        if _index_valid() is False:
            op.execute(f"DROP INDEX CONCURRENTLY {_INDEX}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} "
            "ON versions (law_id) WHERE is_current"
        )


def _index_valid() -> bool | None:
    """Whether the index exists and is usable; None when it does not exist."""
    row = (
        op.get_bind()
        .execute(
            sa.text("SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid = to_regclass(:n)"),
            {"n": _INDEX},
        )
        .one_or_none()
    )
    return None if row is None else bool(row[0])


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
    op.execute("SET lock_timeout = '5s'")
    op.execute("DROP TRIGGER IF EXISTS versions_current_on_insert ON versions")
    op.execute("DROP TRIGGER IF EXISTS versions_current_on_delete ON versions")
    op.execute("DROP TRIGGER IF EXISTS versions_current_on_update ON versions")
    op.execute("DROP FUNCTION IF EXISTS versions_refresh_current()")
    op.execute("DROP FUNCTION IF EXISTS versions_refresh_current_row()")
    op.execute("DROP FUNCTION IF EXISTS refresh_current_versions(uuid[])")
    op.execute("ALTER TABLE versions DROP COLUMN IF EXISTS is_current")
