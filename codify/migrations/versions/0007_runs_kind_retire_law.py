"""Admit the `retire_law` run kind, the child `retire-laws` fans out per law.

`runs` is core-owned, so this chain alone widens its vocabulary, appended to
whatever is in force so the rendering matches a database the product chain
also migrated.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0007_runs_kind_retire_law"
down_revision: str | Sequence[str] | None = "0006_corpus_schema_after_0123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# What this revision adds; test_kind_drift unions it with the product head.
_RUN_KINDS = ("retire_law",)


def _current_kinds() -> list[str]:
    current = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'runs_kind_check'"
        )
        .scalar()
        or ""
    )
    return re.findall(r"'([a-z_]+)'", current)


def _rebuild(kinds: list[str]) -> None:
    op.execute("ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_kind_check")
    op.execute(
        "ALTER TABLE runs ADD CONSTRAINT runs_kind_check CHECK (kind IN ("
        + ", ".join(f"'{k}'" for k in kinds)
        + "))"
    )


def upgrade() -> None:
    # Appended to what is in force, so the rendering matches the product chain's.
    kinds = _current_kinds()
    missing = [k for k in _RUN_KINDS if k not in kinds]
    if missing:
        _rebuild([*kinds, *missing])


def downgrade() -> None:
    kinds = _current_kinds()
    if any(k in kinds for k in _RUN_KINDS):
        for kind in _RUN_KINDS:
            op.execute(f"DELETE FROM runs WHERE kind = '{kind}'")
        _rebuild([k for k in kinds if k not in _RUN_KINDS])
