"""Generic match reads do not require a plugin's catalogue tables."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from codify.storage.models import SchemeMatchRow
from codify.storage.scheme_matches import (
    list_scheme_matches_for_run,
    list_scheme_matches_for_version,
)


def _result(rows: list[SchemeMatchRow]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value = rows
    return result


def _row() -> SchemeMatchRow:
    return SchemeMatchRow(
        lens_run_id=uuid4(),
        lens_name="example",
        scheme_id="example-scheme",
        confidence=0.8,
        findings=[str(uuid4())],
        rationale="Synthetic match",
    )


async def test_read_without_plugin_catalogue_uses_only_match_query() -> None:
    row = _row()
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = _result([row])
    matches = await list_scheme_matches_for_run(cast(AsyncSession, session), row.lens_run_id)
    assert len(matches) == 1
    assert matches[0].scheme_id == row.scheme_id
    assert matches[0].remediation_templates == []
    assert session.execute.await_count == 1


async def test_caller_can_supply_catalogue_without_extra_query() -> None:
    row = _row()
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = _result([row])
    catalogue = {row.scheme_id: ["clarify_requirement"]}
    matches = await list_scheme_matches_for_run(
        cast(AsyncSession, session), row.lens_run_id, remediation_templates=catalogue
    )
    assert matches[0].remediation_templates == ["clarify_requirement"]
    catalogue[row.scheme_id].append("another_action")
    assert matches[0].remediation_templates == ["clarify_requirement"]
    assert session.execute.await_count == 1


async def test_version_read_forwards_caller_catalogue() -> None:
    row = _row()
    latest = MagicMock()
    latest.scalar_one_or_none.return_value = row.lens_run_id
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [latest, _result([row])]
    matches = await list_scheme_matches_for_version(
        cast(AsyncSession, session),
        uuid4(),
        "example",
        remediation_templates={row.scheme_id: ("clarify_requirement",)},
    )
    assert matches[0].remediation_templates == ["clarify_requirement"]
    assert session.execute.await_count == 2
