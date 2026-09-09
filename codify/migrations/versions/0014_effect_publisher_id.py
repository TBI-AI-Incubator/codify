"""Carry the publisher's own identifier for an amendment effect.

Effects accumulate at a publisher, so a feed is re-read rather than read once.
Without the publisher's key the only re-run is delete-and-rebuild, which
destroys rows another source contributed and churns ids every time.

The index is partial because effects arrive two ways: from a feed, which
identifies them, and from a document's own metadata, which does not.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Every run kind this chain has added, not only the new one: the drift test
# reads the head migration's set as the chain's whole contribution.
_RUN_KINDS = ("retire_law", "ingest_amendment_effects")
# The fetch step stores the feed here and passes its id on, so the vocabulary
# has to admit it or the step fails on its first write.
_ARTIFACT_KINDS = ("publisher_effects",)


def _current_kinds(constraint: str, table: str) -> list[str]:
    current = (
        op.get_bind()
        .exec_driver_sql(
            f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = '{constraint}'"
        )
        .scalar()
        or ""
    )
    return re.findall(r"'([a-z_.]+)'", current)


def _rebuild(constraint: str, table: str, column: str, kinds: list[str]) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK ({column} IN ("
        + ", ".join(f"'{k}'" for k in kinds)
        + "))"
    )


def _admit(constraint: str, table: str, wanted: tuple[str, ...]) -> None:
    kinds = _current_kinds(constraint, table)
    missing = [k for k in wanted if k not in kinds]
    if missing:
        _rebuild(constraint, table, "kind", [*kinds, *missing])


# 32 characters is the ceiling: alembic stamps into a varchar(32),
# and a longer id fails at the stamp rather than at the DDL.
revision: str = "0014_effect_publisher_id"
down_revision: str | Sequence[str] | None = "0013_amendment_effect_dates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Appended to whatever is in force, so this chain's rendering matches the
    # product chain's rather than replacing it.
    _admit("runs_kind_check", "runs", _RUN_KINDS)
    _admit("run_artifacts_kind_check", "run_artifacts", _ARTIFACT_KINDS)
    op.add_column("amendment_effects", sa.Column("publisher_effect_id", sa.Text(), nullable=True))
    op.create_index(
        "ux_amendment_effects_publisher_id",
        "amendment_effects",
        ["version_id", "publisher_effect_id"],
        unique=True,
        postgresql_where=sa.text("publisher_effect_id IS NOT NULL"),
    )


# Only what this revision added. `retire_law` came in with 0007 and is that
# revision's to remove; taking it out here would strip a kind still in use.
_ADDED_HERE = ("ingest_amendment_effects",)


def downgrade() -> None:
    # Rows first: the constraint cannot narrow while a run of the kind exists,
    # and a downgrade that fails halfway is worse than one that removes them.
    op.execute("DELETE FROM runs WHERE kind = ANY(ARRAY['ingest_amendment_effects'])")
    kinds = _current_kinds("runs_kind_check", "runs")
    if any(k in kinds for k in _ADDED_HERE):
        _rebuild("runs_kind_check", "runs", "kind", [k for k in kinds if k not in _ADDED_HERE])
    op.execute("DELETE FROM run_artifacts WHERE kind = ANY(ARRAY['publisher_effects'])")
    artifact_kinds = _current_kinds("run_artifacts_kind_check", "run_artifacts")
    if any(k in artifact_kinds for k in _ARTIFACT_KINDS):
        _rebuild(
            "run_artifacts_kind_check",
            "run_artifacts",
            "kind",
            [k for k in artifact_kinds if k not in _ARTIFACT_KINDS],
        )
    op.drop_index("ux_amendment_effects_publisher_id", table_name="amendment_effects")
    op.drop_column("amendment_effects", "publisher_effect_id")
