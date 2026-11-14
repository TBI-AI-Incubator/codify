"""Add `registry_works.record`: the record a second register keeps for the same work.

A jurisdiction's registry row names a work by its ref. Another official register may
describe the same work with dates, issuer, status and publications the first lacks; the
row keeps that record whole, with the register it came from.
"""

from __future__ import annotations

from alembic import op

revision: str = "0030_registry_work_records"
down_revision: str | None = "0029_official_sources"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE registry_works ADD COLUMN IF NOT EXISTS record jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE registry_works DROP COLUMN IF EXISTS record")
