"""ARQ delivery, with durable attempts and a bounded provider dedupe window."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from app.api.deps import get_email_transport, get_vault
from app.core.clock import utc_now
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.db.session import session_scope, system_session
from app.messaging import providers
from app.messaging.models import (
    MARKETING,
    Campaign,
    Channel,
    Conversation,
    Message,
    MessageAttempt,
)
from app.messaging.service import capability, marketing_block, unsubscribe_url

#: Statuses the dispatcher still owns. Everything else is final or provider-driven.
LIVE = ("QUEUED", "RETRY", "SENDING")


async def deliver_message(tenant_id: uuid.UUID, message_id: uuid.UUID) -> None:
    token = set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name="messaging",
        )
    )
    try:
        # Commit the first-attempt clock BEFORE network I/O. A crashed worker must
        # never restart the provider's 24-hour idempotency window.
        async with session_scope() as db:
            row = await db.scalar(
                sa.select(Message).where(Message.id == message_id).with_for_update()
            )
            if not row or row.status not in set(LIVE) or row.next_attempt_at > utc_now():
                return
            if row.campaign_id and row.status != "SENDING":
                campaign = await db.get(Campaign, row.campaign_id)
                if campaign is not None and campaign.status == "CANCELLED":
                    row.status, row.last_error = "CANCELLED", "CAMPAIGN_CANCELLED"
                    return
                if campaign is not None and campaign.status == "PAUSED":
                    # Held, not spent: no attempt and no provider window started.
                    row.next_attempt_at = utc_now() + timedelta(minutes=5)
                    return
            if row.first_attempt_at and utc_now() - row.first_attempt_at >= timedelta(hours=23):
                row.status, row.last_error = "UNKNOWN", "PROVIDER_DEDUPE_WINDOW_EXPIRED"
                return
            if row.status == "SENDING" and row.channel == "WHATSAPP":
                # A WhatsApp send has no provider idempotency key. A crash after
                # the request left is never resent blind.
                row.status, row.last_error = "UNKNOWN", "SEND_INTERRUPTED"
                return
            row.first_attempt_at = row.first_attempt_at or utc_now()
            row.attempts += 1
            row.status = "SENDING"
            row.next_attempt_at = utc_now() + timedelta(minutes=2)
        async with session_scope() as db:
            row = await db.scalar(
                sa.select(Message).where(Message.id == message_id).with_for_update()
            )
            if not row or row.status != "SENDING":
                return
            conversation = await db.scalar(
                sa.select(Conversation)
                .where(Conversation.id == row.conversation_id)
                .with_for_update()
            )
            if conversation is None:
                row.status, row.last_error = "SUPPRESSED", "CONSENT_REQUIRED"
                return
            # Consent is checked again at the moment of sending: an opt-out
            # recorded after queueing always wins.
            if row.purpose == MARKETING:
                blocked = marketing_block(conversation)
                if blocked:
                    row.status, row.last_error = "SUPPRESSED", blocked
                    return
            elif not conversation.consent or conversation.consent_version != row.consent_version:
                row.status, row.last_error = "SUPPRESSED", "CONSENT_REQUIRED"
                return
            elif conversation.undeliverable_at is not None:
                row.status, row.last_error = "SUPPRESSED", "UNDELIVERABLE"
                return
            channel = await db.scalar(
                sa.select(Channel).where(Channel.kind == conversation.channel)
            )
            if not capability(conversation.channel)[0] or not channel or not channel.enabled:
                row.status, row.last_error = "FAILED", "CHANNEL_DISABLED"
                row.failed_at = utc_now()
                return
            language = None
            template_name = None
            if row.channel == "WHATSAPP":
                from app.messaging.service import template, template_language

                found = await template(db, row.template_key)
                template_name = found["provider_name"]
                language = template_language(found, row.locale)
            result = await providers.send(
                db,
                providers.Outbound(
                    message_id=row.id,
                    channel=row.channel,
                    purpose=row.purpose,
                    to=get_vault().decrypt(
                        conversation.recipient_enc, context=f"message-contact:{conversation.id}"
                    ),
                    subject=row.subject,
                    body=row.body,
                    template_name=template_name,
                    language=language,
                    params=list(row.params or []),
                    unsubscribe_url=unsubscribe_url(conversation)
                    if row.purpose == MARKETING and row.channel == "EMAIL"
                    else None,
                ),
                email_transport=get_email_transport(),
            )
            db.add(MessageAttempt(message_id=row.id, outcome=result.error or result.outcome))
            row.provider = result.provider
            row.provider_reference = result.reference or row.provider_reference
            row.last_error = result.error if result.outcome != "SENT" else None
            if result.outcome == "SENT":
                row.status, row.sent_at = "SENT", utc_now()
            elif result.outcome == "RETRY" and row.attempts < 5:
                row.status = "RETRY"
            elif result.outcome == "UNKNOWN":
                row.status = "UNKNOWN"
            else:
                row.status, row.failed_at = "FAILED", utc_now()
            row.next_attempt_at = utc_now() + timedelta(seconds=min(3600, 30 * 2**row.attempts))
    finally:
        clear_context(token)


async def dispatch_messages(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    async with system_session("messaging: due IDs only") as db:
        rows = (
            await db.execute(
                sa.select(Message.tenant_id, Message.id)
                .where(
                    Message.status.in_(LIVE),
                    Message.next_attempt_at <= utc_now(),
                )
                .order_by(Message.next_attempt_at)
                .limit(50)
            )
        ).all()
    for tenant_id, message_id in rows:
        await deliver_message(tenant_id, message_id)
    return {"processed": len(rows)}
