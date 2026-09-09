"""Idempotency keys for money-touching and externally-visible operations.

Master spec section 77: client-mutating endpoints accept an ``Idempotency-Key``
header where a network retry could duplicate money or external work — booking,
manual payout creation, payout import finalisation, billing checkout and
sensitive bulk actions.

Two rules define the contract:

*   same key **and** same request body -> replay the stored response;
*   same key **and** a different body -> ``409 IDEMPOTENCY_KEY_CONFLICT``.

Silently executing the second request would double-book a parcel; silently
returning the first response would tell the seller something happened that
did not. Both are worse than an explicit conflict.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.core.errors import IdempotencyConflictError
from app.core.ids import new_id
from app.db.base import Base, TenantOwned
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = [
    "IdempotencyKey",
    "IdempotencyOutcome",
    "begin_idempotent",
    "complete_idempotent",
    "request_hash",
]

#: How long a key is honoured. Long enough to cover a retry storm and an
#: offline device reconnecting, short enough to keep the table small.
DEFAULT_RETENTION = timedelta(hours=48)


class IdempotencyKey(Base, TenantOwned):
    """A recorded idempotent request and, once finished, its response."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "key", "endpoint", name="uq_idempotency_keys_tenant_id_key_endpoint"
        ),
        sa.Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    endpoint: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    request_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    #: NULL while the original request is still running.
    response_code: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: Domain reference (order id, payout id) for support traceability.
    reference: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None


class IdempotencyOutcome:
    """Result of claiming an idempotency key."""

    __slots__ = ("record", "replayed")

    def __init__(self, record: IdempotencyKey, *, replayed: bool) -> None:
        self.record = record
        self.replayed = replayed

    @property
    def should_execute(self) -> bool:
        """Whether the caller should actually perform the operation."""
        return not self.replayed


def request_hash(payload: Any) -> str:
    """Stable hash of a request body.

    Sorted keys and a compact separator make the hash independent of client JSON
    formatting, so a retry from a different serializer still matches.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def begin_idempotent(
    session: AsyncSession,
    *,
    key: str,
    endpoint: str,
    payload: Any,
    retention: timedelta = DEFAULT_RETENTION,
) -> IdempotencyOutcome:
    """Claim an idempotency key.

    Returns an outcome describing whether to execute or replay. Raises
    :class:`~app.core.errors.IdempotencyConflictError` when the same key arrives
    with a different body.
    """
    digest = request_hash(payload)
    now = utc_now()

    existing = (
        await session.execute(
            sa.select(IdempotencyKey).where(
                IdempotencyKey.key == key,
                IdempotencyKey.endpoint == endpoint,
            )
        )
    ).scalar_one_or_none()

    if existing is not None and existing.expires_at > now:
        if existing.request_sha256 != digest:
            raise IdempotencyConflictError(
                "This idempotency key was already used with a different request body",
                details={"endpoint": endpoint},
            )
        return IdempotencyOutcome(existing, replayed=existing.is_complete)

    if existing is not None:
        # Expired: reuse the row rather than colliding with its unique constraint.
        existing.request_sha256 = digest
        existing.response_code = None
        existing.response_body = None
        existing.reference = None
        existing.created_at = now
        existing.completed_at = None
        existing.expires_at = now + retention
        await session.flush()
        return IdempotencyOutcome(existing, replayed=False)

    record = IdempotencyKey(
        key=key,
        endpoint=endpoint,
        request_sha256=digest,
        expires_at=now + retention,
    )
    session.add(record)
    await session.flush()
    return IdempotencyOutcome(record, replayed=False)


async def complete_idempotent(
    session: AsyncSession,
    record: IdempotencyKey,
    *,
    status_code: int,
    body: dict[str, Any] | None = None,
    reference: str | None = None,
) -> None:
    """Store the response so a later retry replays it instead of re-executing."""
    record.response_code = status_code
    record.response_body = body
    record.reference = reference
    record.completed_at = utc_now()
    await session.flush()
