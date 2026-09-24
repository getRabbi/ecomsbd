"""The campaign scheduler: start scheduled sends, enrol flows, queue due recipients."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.db.session import session_scope, system_session
from app.messaging import campaigns as engine
from app.messaging.models import Campaign


async def step(tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> None:
    token = set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name="campaigns",
        )
    )
    try:
        async with session_scope() as db:
            await lock_shop(db)
            campaign = await db.scalar(
                sa.select(Campaign).where(Campaign.id == campaign_id).with_for_update()
            )
            if campaign is None:
                return
            now = utc_now()
            if (
                campaign.status == "SCHEDULED"
                and campaign.scheduled_at
                and (campaign.scheduled_at <= now)
            ):
                blocker = await engine.ready(db, campaign)
                if blocker:
                    campaign.status, campaign.last_error = "PAUSED", blocker
                    return
                campaign.status, campaign.started_at = "SENDING", now
                await engine.materialize(db, campaign, now)
            if campaign.status == "ACTIVE":
                await engine.run_flow(db, campaign, now)
            if campaign.status in {"SENDING", "ACTIVE"}:
                await engine.advance(db, campaign, now)
    finally:
        clear_context(token)


async def run_campaigns(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    now = utc_now()
    async with system_session("campaigns: due campaign IDs") as db:
        rows = (
            await db.execute(
                sa.select(Campaign.tenant_id, Campaign.id)
                .where(
                    sa.or_(
                        Campaign.status.in_(["SENDING", "ACTIVE"]),
                        sa.and_(Campaign.status == "SCHEDULED", Campaign.scheduled_at <= now),
                    )
                )
                .order_by(Campaign.updated_at)
                .limit(100)
            )
        ).all()
    for tenant_id, campaign_id in rows:
        await step(tenant_id, campaign_id)
    return {"processed": len(rows)}
