"""Stamp each cross-reference with the resolver policy that wrote it.

Without this the resolver can never revisit a row: its selector takes only rows
with all three target ids null, so a stamp made by a rule later found wrong
stays forever and a re-run measures nothing. A corpus measurement found references
stamped onto an explanatory entry, and others onto an unrelated ordinance,
none of them reachable again by re-running the resolver.

Nullable and unstamped on every existing row, which is what makes the backfill
the same selector as the ordinary path: below the current version, or unstamped,
means "resolve me".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_xref_resolver_version"
down_revision: str | Sequence[str] | None = "0011_admin_batch_children_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "cross_references_resolution_origin_check"
_INDEX = "cross_references_resolver_version_idx"
_ORIGIN_CHECK = (
    "((resolution_origin IS NULL) OR (resolution_origin = ANY "
    "(ARRAY['href'::text, 'text'::text, 'registry'::text])))"
)


def _column_exists(name: str, expected_type: str) -> bool:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT format_type(a.atttypid, a.atttypmod) AS type, "
                "a.attnotnull, a.atthasdef, a.attidentity, a.attgenerated, "
                "a.attcollation = t.typcollation AS default_collation "
                "FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid "
                "WHERE a.attrelid = 'cross_references'::regclass "
                "AND a.attname = :name AND NOT a.attisdropped"
            ),
            {"name": name},
        )
        .one_or_none()
    )
    if row is None:
        return False
    if tuple(row) != (expected_type, False, False, "", "", True):
        raise RuntimeError(f"Incompatible cross_references column: {name}")
    return True


def _constraint_exists() -> bool:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT contype, pg_get_expr(conbin, conrelid), connoinherit "
                "FROM pg_constraint WHERE conrelid = 'cross_references'::regclass "
                "AND conname = :name"
            ),
            {"name": _CONSTRAINT},
        )
        .one_or_none()
    )
    if row is None:
        return False
    if tuple(row) != ("c", _ORIGIN_CHECK, False):
        raise RuntimeError(f"Incompatible cross_references constraint: {_CONSTRAINT}")
    return True


def _index_valid() -> bool | None:
    # Resolve the name in the table's schema, including non-index collisions.
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT pg_get_indexdef(i.indexrelid), i.indisvalid, i.indisready, "
                "i.indislive, quote_ident(n.nspname) AS schema_name "
                "FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace "
                "JOIN pg_class x ON x.relnamespace = n.oid AND x.relname = :name "
                "LEFT JOIN pg_index i ON i.indexrelid = x.oid "
                "WHERE t.oid = 'cross_references'::regclass"
            ),
            {"name": _INDEX},
        )
        .one_or_none()
    )
    if row is None:
        return None
    expected = (
        f"CREATE INDEX {_INDEX} ON {row.schema_name}.cross_references "
        "USING btree (resolver_version) WHERE (resolver_version IS NULL)"
    )
    if row[0] != expected:
        raise RuntimeError(f"Incompatible cross_references index: {_INDEX}")
    return bool(row[1] and row[2] and row[3])


def upgrade() -> None:
    # Autocommit boundaries can leave the DDL present without a revision stamp.
    # Accept only this migration's definitions before resuming that work.
    version_exists = _column_exists("resolver_version", "smallint")
    origin_exists = _column_exists("resolution_origin", "text")
    constraint_exists = _constraint_exists()
    index_valid = _index_valid()
    if not version_exists:
        op.add_column(
            "cross_references", sa.Column("resolver_version", sa.SmallInteger(), nullable=True)
        )
    if not origin_exists:
        op.add_column("cross_references", sa.Column("resolution_origin", sa.Text(), nullable=True))
    if not constraint_exists:
        op.execute(
            f"ALTER TABLE cross_references ADD CONSTRAINT {_CONSTRAINT} "
            "CHECK (resolution_origin IS NULL OR resolution_origin IN "
            "('href', 'text', 'registry')) NOT VALID"
        )
    # Commit the brief ACCESS EXCLUSIVE lock before the validation scan.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE cross_references VALIDATE CONSTRAINT {_CONSTRAINT}")
    if index_valid is not True:
        with op.get_context().autocommit_block():
            # A cancelled concurrent build leaves an unusable index behind.
            if index_valid is False:
                op.execute(f"DROP INDEX CONCURRENTLY {_INDEX}")
            op.execute(
                f"CREATE INDEX CONCURRENTLY {_INDEX} "
                "ON cross_references (resolver_version) WHERE resolver_version IS NULL"
            )


def downgrade() -> None:
    # Refuse incompatible objects before committing any destructive work.
    _column_exists("resolver_version", "smallint")
    _column_exists("resolution_origin", "text")
    _constraint_exists()
    _index_valid()
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
    op.execute(f"ALTER TABLE cross_references DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute("ALTER TABLE cross_references DROP COLUMN IF EXISTS resolution_origin")
    op.execute("ALTER TABLE cross_references DROP COLUMN IF EXISTS resolver_version")
