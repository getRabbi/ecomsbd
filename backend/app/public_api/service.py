from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.idempotency import request_hash
from app.common.operation_lock import lock_shop
from app.core.errors import AuthenticationError, IdempotencyConflictError
from app.public_api.auth import ApiPrincipal
from app.public_api.models import ApiKey, ApiWriteReceipt


async def write_once(
    db: AsyncSession,
    principal: ApiPrincipal,
    endpoint: str,
    key: str,
    payload: Any,
    execute: Callable[[], Awaitable[dict]],
) -> dict:
    await lock_shop(db)
    # Serialize against revocation too: a queued write cannot use a revoked key.
    credential = await db.scalar(
        sa.select(ApiKey).where(ApiKey.id == principal.key_id).with_for_update()
    )
    if credential is None or credential.revoked_at:
        raise AuthenticationError("API key has been revoked")
    digest = request_hash(payload)
    receipt = await db.scalar(
        sa.select(ApiWriteReceipt).where(
            ApiWriteReceipt.key_id == principal.key_id,
            ApiWriteReceipt.endpoint == endpoint,
            ApiWriteReceipt.idempotency_key == key,
        )
    )
    if receipt:
        if receipt.request_hash != digest:
            raise IdempotencyConflictError()
        return receipt.response
    response = await execute()
    db.add(
        ApiWriteReceipt(
            key_id=principal.key_id,
            endpoint=endpoint,
            idempotency_key=key,
            request_hash=digest,
            response=response,
        )
    )
    await db.flush()
    return response
