"""Opaque, scope-bound keyset cursors with deterministic timestamp/UUID ordering."""

import base64
import hashlib
import json
import uuid
from datetime import datetime
from typing import Generic, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import tuple_

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


def cursor_scope(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()


def encode_cursor(row, scope: str) -> str:
    value = json.dumps([row.created_at.isoformat(), str(row.id), scope]).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def apply_cursor(statement, model, cursor: str | None, scope: str):
    if cursor:
        try:
            if len(cursor) > 1024:
                raise ValueError("Oversized cursor")
            timestamp, identifier, stored_scope = json.loads(
                base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            )
            timestamp = datetime.fromisoformat(timestamp)
            if stored_scope != scope or timestamp.tzinfo is None:
                raise ValueError("Cursor scope or timestamp mismatch")
            statement = statement.where(
                tuple_(model.created_at, model.id) < tuple_(timestamp, uuid.UUID(identifier))
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(status_code=422, detail="Invalid pagination cursor.") from exc
    return statement.order_by(model.created_at.desc(), model.id.desc())


async def read_page(session, statement, model, *, cursor, scope, limit):
    rows = list((await session.execute(
        apply_cursor(statement, model, cursor, scope).limit(limit + 1)
    )).scalars().all())
    return rows[:limit], encode_cursor(rows[limit - 1], scope) if len(rows) > limit else None
