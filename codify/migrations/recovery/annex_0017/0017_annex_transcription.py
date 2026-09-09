"""Admit the `transcribe_annex_images` run kind and the `annex_transcription`
artifact kind it stores each version's page transcripts under.

`runs` and `run_artifacts` are core-owned, so this chain widens both
vocabularies, appended to whatever is in force.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0017_annex_transcription"
down_revision: str | Sequence[str] | None = "0016_delegated_powers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# What this revision adds; test_kind_drift unions each with the product head.
_RUN_KINDS = ("transcribe_annex_images",)
_ARTIFACT_KINDS = ("annex_transcription",)

_CONSTRAINTS = {
    "runs": ("runs_kind_check", _RUN_KINDS),
    "run_artifacts": ("run_artifacts_kind_check", _ARTIFACT_KINDS),
}


def _current_kinds(constraint: str) -> list[str]:
    current = (
        op.get_bind()
        .exec_driver_sql(
            f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = '{constraint}'"
        )
        .scalar()
        or ""
    )
    return re.findall(r"'([a-z_.]+)'", current)


def _rebuild(table: str, constraint: str, kinds: list[str]) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK (kind IN ("
        + ", ".join(f"'{k}'" for k in kinds)
        + "))"
    )


def upgrade() -> None:
    for table, (constraint, added) in _CONSTRAINTS.items():
        kinds = _current_kinds(constraint)
        missing = [k for k in added if k not in kinds]
        if missing:
            _rebuild(table, constraint, [*kinds, *missing])


def downgrade() -> None:
    for table, (constraint, added) in _CONSTRAINTS.items():
        kinds = _current_kinds(constraint)
        if any(k in kinds for k in added):
            for kind in added:
                op.execute(f"DELETE FROM {table} WHERE kind = '{kind}'")
            _rebuild(table, constraint, [k for k in kinds if k not in added])
