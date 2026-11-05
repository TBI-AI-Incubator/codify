"""Let goods_code_references hold an HS chapter and say what an annex excludes.

A chapter is two digits ("72 - Iron and steel"), below the old four-digit floor.
`excluded` marks a code listed after "Except", so a measure naming chapter 72
does not read as covering the headings it carves out.
"""

from __future__ import annotations

from alembic import op

revision: str = "0028_goods_code_scope"
down_revision: str | None = "0027_query_index_hygiene"
branch_labels: str | None = None
depends_on: str | None = None


def _digits_check(lengths: str) -> None:
    op.execute(
        "ALTER TABLE goods_code_references ADD CONSTRAINT goods_code_references_code_digits "
        f"CHECK (code ~ '^[0-9]{lengths}$') NOT VALID"
    )
    # Commit the brief ACCESS EXCLUSIVE lock before the validation scan.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE goods_code_references "
            "VALIDATE CONSTRAINT goods_code_references_code_digits"
        )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE goods_code_references "
        "ADD COLUMN IF NOT EXISTS excluded boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE goods_code_references "
        "DROP CONSTRAINT IF EXISTS goods_code_references_code_digits"
    )
    _digits_check("{2,10}")


def downgrade() -> None:
    # The old schema has no carve-outs: a kept excluded row would read as covered.
    op.execute("DELETE FROM goods_code_references WHERE length(code) < 4 OR excluded")
    op.execute(
        "ALTER TABLE goods_code_references "
        "DROP CONSTRAINT IF EXISTS goods_code_references_code_digits"
    )
    _digits_check("{4,10}")
    op.execute("ALTER TABLE goods_code_references DROP COLUMN IF EXISTS excluded")
