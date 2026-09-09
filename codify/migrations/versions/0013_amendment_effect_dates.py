"""Give an amendment effect the date it takes effect, and whether it was applied.

Without these an effect is a fact with no place on a timeline, so a statute
cannot be rendered as it stood on a date. Both are nullable: the publisher's
record is itself incomplete, and an effect we hold no date for is reported as
undated rather than guessed at.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_amendment_effect_dates"
down_revision: str | Sequence[str] | None = "0012_xref_resolver_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("amendment_effects", sa.Column("in_force_date", sa.Date(), nullable=True))
    # Nullable rather than defaulted: rows written before this migration were
    # ingested from a feed whose applied flag we discarded, so "unknown" is the
    # truth for them and a default of true would assert something we never read.
    op.add_column("amendment_effects", sa.Column("applied", sa.Boolean(), nullable=True))
    # The reconstruction selects by date over one version's effects.
    op.create_index(
        "ix_amendment_effects_version_in_force",
        "amendment_effects",
        ["version_id", "in_force_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_amendment_effects_version_in_force", table_name="amendment_effects")
    op.drop_column("amendment_effects", "applied")
    op.drop_column("amendment_effects", "in_force_date")
