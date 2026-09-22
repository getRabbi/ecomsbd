"""Serialize short shop mutations before claiming durable idempotency records."""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_context
from app.core.errors import ForbiddenError
from app.tenants.models import Tenant


async def lock_shop(session: AsyncSession) -> None:
    tenant_id = current_context().tenant_id
    if tenant_id is None:
        raise ForbiddenError("A shop is required")
    await session.execute(sa.select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
