"""Cursor pagination.

Master spec section 106: never return unlimited orders or customers; use cursor
pagination for high-volume mutable lists, with a configurable default and cap.

Offset pagination is deliberately not offered for these lists. With rows being
inserted while a seller scrolls, offsets skip and repeat records; a cursor on
``(created_at, id)`` is stable.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.clock import ensure_utc
from app.core.errors import ValidationError

__all__ = ["Cursor", "Page", "PageParams", "decode_cursor", "encode_cursor"]


@dataclass(frozen=True, slots=True)
class Cursor:
    """Position in a ``(created_at DESC, id DESC)`` ordering."""

    created_at: datetime
    id: uuid.UUID


def encode_cursor(cursor: Cursor) -> str:
    payload = {"t": ensure_utc(cursor.created_at).isoformat(), "i": str(cursor.id)}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> Cursor:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return Cursor(
            created_at=datetime.fromisoformat(payload["t"]),
            id=uuid.UUID(payload["i"]),
        )
    except (KeyError, ValueError, TypeError, binascii.Error) as exc:
        raise ValidationError("Malformed pagination cursor", details={"cursor": value}) from exc


class PageParams(BaseModel):
    """Query parameters for a cursor-paginated list."""

    cursor: str | None = Field(default=None, description="Opaque cursor from a previous page")
    limit: int = Field(default=30, ge=1, le=100)

    def as_cursor(self) -> Cursor | None:
        return decode_cursor(self.cursor) if self.cursor else None


class Page[T](BaseModel):
    """One page of results plus the cursor for the next one."""

    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False

    @classmethod
    def build(
        cls,
        rows: list[Any],
        *,
        limit: int,
        serializer: Any,
    ) -> Page[T]:
        """Build a page from ``limit + 1`` fetched rows.

        Fetching one extra row is how ``has_more`` is known without a second
        ``COUNT(*)`` over a large table.
        """
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = encode_cursor(Cursor(created_at=last.created_at, id=last.id))
        return cls(
            items=[serializer(row) for row in visible],
            next_cursor=next_cursor,
            has_more=has_more,
        )
