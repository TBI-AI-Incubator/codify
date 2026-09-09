from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession


async def paginate_by_created_id(
    session: AsyncSession,
    model: Any,
    *,
    where: list[Any] | None = None,
    limit: int,
    cursor: uuid.UUID | None,
) -> tuple[list[Any], uuid.UUID | None]:
    """Cursor-paginate a `select(model)` ordered by `(created_at DESC, id DESC)`.

    Fetches `limit+1` rows and returns the surplus row's id as next_cursor.
    A cursor that no longer resolves under the supplied scope returns
    `([], None)` so callers stop paging instead of silently restarting at
    page 1.
    """
    scope = list(where) if where else []
    stmt = select(model)
    for clause in scope:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(desc(model.created_at), desc(model.id)).limit(limit + 1)

    if cursor is not None:
        anchor_stmt = select(model.created_at, model.id).where(model.id == cursor)
        for clause in scope:
            anchor_stmt = anchor_stmt.where(clause)
        anchor = (await session.execute(anchor_stmt)).first()
        if anchor is None:
            return [], None
        anchor_at, anchor_id = anchor
        stmt = stmt.where(
            (model.created_at < anchor_at)
            | ((model.created_at == anchor_at) & (model.id < anchor_id))
        )

    rows = list((await session.execute(stmt)).scalars().all())
    page = rows[:limit]
    next_cursor = page[-1].id if len(rows) > limit else None
    return page, next_cursor
