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
from app.messaging.models import Channel, Conversation, Message, MessageAttempt
from app.messaging.service import capability
from app.notifications.transport import EmailMessage


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
            if (
                not row
                or row.status not in {"QUEUED", "RETRY", "SENDING"}
                or row.next_attempt_at > utc_now()
            ):
                return
            if row.first_attempt_at and utc_now() - row.first_attempt_at >= timedelta(hours=23):
                row.status, row.last_error = "UNKNOWN", "PROVIDER_DEDUPE_WINDOW_EXPIRED"
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
            if (
                not conversation
                or not conversation.consent
                or conversation.consent_version != row.consent_version
            ):
                row.status, row.last_error = "SUPPRESSED", "CONSENT_REQUIRED"
                return
            channel = await db.scalar(
                sa.select(Channel).where(Channel.kind == conversation.channel)
            )
            if not capability(conversation.channel)[0] or not channel or not channel.enabled:
                row.status, row.last_error = "FAILED", "CHANNEL_DISABLED"
                return
            result = await get_email_transport().send(
                EmailMessage(
                    to_address=get_vault().decrypt(
                        conversation.recipient_enc, context=f"message-contact:{conversation.id}"
                    ),
                    subject=row.subject,
                    text_body=row.body,
                    idempotency_key=f"customer-message/{row.id}",
                )
            )
            db.add(MessageAttempt(message_id=row.id, outcome=str(result.outcome)))
            row.provider_reference = result.provider_reference
            row.last_error = None if result.delivered else str(result.outcome)
            row.status = (
                "SENT"
                if result.delivered
                else ("RETRY" if result.retryable and row.attempts < 5 else "FAILED")
            )
            row.next_attempt_at = utc_now() + timedelta(seconds=min(3600, 30 * 2**row.attempts))
    finally:
        clear_context(token)


async def dispatch_messages(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    async with system_session("messaging: due IDs only") as db:
        rows = (
            await db.execute(
                sa.select(Message.tenant_id, Message.id)
                .where(
                    Message.status.in_(["QUEUED", "RETRY", "SENDING"]),
                    Message.next_attempt_at <= utc_now(),
                )
                .order_by(Message.next_attempt_at)
                .limit(50)
            )
        ).all()
    for tenant_id, message_id in rows:
        await deliver_message(tenant_id, message_id)
    return {"processed": len(rows)}
