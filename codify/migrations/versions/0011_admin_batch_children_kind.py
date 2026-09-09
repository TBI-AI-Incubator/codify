"""Admit the `admin_batch_children` artifact kind, the resolved child specs an
admin batch stores so its parent workflow can create the children durably.

`run_artifacts` is core-owned, so this chain widens its vocabulary too, appended
to whatever is in force so the rendering matches a database the product chain
also migrated.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0011_admin_batch_children_kind"
down_revision: str | Sequence[str] | None = "0010_annex_rows_search_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# What this revision adds; test_kind_drift unions it with the product head.
_ARTIFACT_KINDS = ("admin_batch_children",)


def _current_kinds() -> list[str]:
    current = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'run_artifacts_kind_check'"
        )
        .scalar()
        or ""
    )
    return re.findall(r"'([a-z_.]+)'", current)


def _rebuild(kinds: list[str]) -> None:
    op.execute("ALTER TABLE run_artifacts DROP CONSTRAINT IF EXISTS run_artifacts_kind_check")
    op.execute(
        "ALTER TABLE run_artifacts ADD CONSTRAINT run_artifacts_kind_check CHECK (kind IN ("
        + ", ".join(f"'{k}'" for k in kinds)
        + "))"
    )


def upgrade() -> None:
    kinds = _current_kinds()
    missing = [k for k in _ARTIFACT_KINDS if k not in kinds]
    if missing:
        _rebuild([*kinds, *missing])


def downgrade() -> None:
    kinds = _current_kinds()
    if any(k in kinds for k in _ARTIFACT_KINDS):
        for kind in _ARTIFACT_KINDS:
            op.execute(f"DELETE FROM run_artifacts WHERE kind = '{kind}'")
        _rebuild([k for k in kinds if k not in _ARTIFACT_KINDS])
