"""Cursor pagination ordered by ``created_at`` descending, then ``id``."""

from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from pik_api.errors import APIError


def paginate(db: Session, stmt, model, *, partner_id: str, starting_after: str | None, limit: int):
    if starting_after:
        cursor = db.get(model, starting_after)
        owner = getattr(cursor, "partner_id", None) if cursor is not None else None
        if cursor is None or owner != partner_id:
            raise APIError(
                400,
                "invalid_request_error",
                "invalid_cursor",
                "The starting_after cursor does not match a resource for this partner.",
            )
        stmt = stmt.where(
            or_(
                model.created_at < cursor.created_at,
                and_(model.created_at == cursor.created_at, model.id < cursor.id),
            )
        )
    rows = list(db.scalars(stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].id if has_more and page else None
    return page, has_more, next_cursor
