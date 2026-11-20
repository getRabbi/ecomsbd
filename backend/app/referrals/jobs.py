"""Outbox handler for referrals (V4 WP7.4).

``referral.payment_approved`` (written by the WP2.5 approve, ids only) → the
paying shop's referral, if any, is rewarded once: both shops get the plan
credit. It writes two shops, so it runs in a system session; a repeat finds
the row no longer PENDING and does nothing.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import OutboxEvent, OutboxTopic
from app.core.config import get_settings
from app.db.session import system_session
from app.referrals.service import ReferralRewards
from app.worker.jobs import register_handler

__all__: list[str] = []


@register_handler(OutboxTopic.REFERRAL_PAYMENT_APPROVED)
async def _on_payment_approved(_session: AsyncSession, event: OutboxEvent) -> None:
    payload = event.payload or {}
    submission_id = uuid.UUID(str(payload["submission_id"]))
    async with system_session("referrals: reward both shops of a referral") as db:
        await ReferralRewards(db, settings=get_settings()).payment_approved(submission_id)
