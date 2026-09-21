from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_vault
from app.common.idempotency import request_hash
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.errors import ConflictError, IdempotencyConflictError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.customers.models import Customer
from app.messaging.models import Channel, ConsentEvent, Conversation, Message, MessageTemplate
from app.orders.models import Order

BUILTIN = {
    "key": "order_update",
    "subject_en": "Order {order_number} update",
    "subject_bn": "অর্ডার {order_number} আপডেট",
    "body_en": "Hello {customer_name}, please contact your seller for an update on order {order_number}.",
    "body_bn": "প্রিয় {customer_name}, অর্ডার {order_number} সম্পর্কে জানতে আপনার বিক্রেতার সঙ্গে যোগাযোগ করুন।",
}


def capability(kind: str) -> tuple[bool, str | None]:
    settings = get_settings()
    if (
        kind == "EMAIL"
        and settings.email_transport_can_deliver
        and (settings.email_api_base_url or "").rstrip("/") == "https://api.resend.com"
    ):
        return True, None
    return False, f"{kind}_OFFICIAL_PROVIDER_REQUIRED"


async def required(db: AsyncSession, model: Any, row_id: uuid.UUID) -> Any:
    row = await db.scalar(sa.select(model).where(model.id == row_id))
    if row is None or getattr(row, "deleted_at", None) is not None:
        raise NotFoundError()
    return row


def validate_template(values: dict[str, Any]) -> None:
    for key in ("subject_en", "subject_bn", "body_en", "body_bn"):
        value = values[key]
        if not value.strip() or (key.startswith("subject") and any(c in value for c in "\r\n")):
            raise ValidationError("A non-empty transactional template is required")
        remainder = value.replace("{customer_name}", "").replace("{order_number}", "")
        if "{" in remainder or "}" in remainder:
            raise ValidationError("Only customer_name and order_number placeholders are supported")


async def templates(db: AsyncSession) -> list[dict[str, Any]]:
    rows = (await db.scalars(sa.select(MessageTemplate).order_by(MessageTemplate.key))).all()
    return [BUILTIN, *[{key: getattr(row, key) for key in BUILTIN} for row in rows]]


async def set_contact(
    db: AsyncSession,
    customer_id: uuid.UUID,
    channel: str,
    recipient: str,
    consent: bool,
    evidence: str,
    actor_id: uuid.UUID,
) -> Conversation:
    await lock_shop(db)
    await required(db, Customer, customer_id)
    recipient = recipient.strip()
    if channel != "EMAIL" or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", recipient):
        raise ValidationError("A valid email recipient is required")
    row = await db.scalar(
        sa.select(Conversation)
        .where(Conversation.customer_id == customer_id, Conversation.channel == channel)
        .with_for_update()
    )
    if row is None:
        row = Conversation(id=new_id(), customer_id=customer_id, channel=channel, consent_version=0)
        db.add(row)
    row.recipient_enc = get_vault().encrypt(recipient, context=f"message-contact:{row.id}")
    local, domain = recipient.rsplit("@", 1)
    row.recipient_masked = f"{local[:1]}***@{domain}"
    row.consent = consent
    row.consent_version += 1
    await db.flush()
    db.add(
        ConsentEvent(conversation_id=row.id, consent=consent, evidence=evidence, actor_id=actor_id)
    )
    await db.flush()
    return row


async def queue_message(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    order_id: uuid.UUID,
    template_key: str,
    locale: str,
    idempotency_key: str,
) -> Message:
    await lock_shop(db)
    digest = request_hash([str(conversation_id), str(order_id), template_key, locale])
    existing = await db.scalar(sa.select(Message).where(Message.idempotency_key == idempotency_key))
    if existing:
        if existing.request_hash != digest:
            raise IdempotencyConflictError()
        return existing
    conversation = await required(db, Conversation, conversation_id)
    customer = await required(db, Customer, conversation.customer_id)
    order = await required(db, Order, order_id)
    if order.customer_id != customer.id:
        raise ValidationError("The order belongs to a different customer")
    if not conversation.consent:
        raise ConflictError(
            "Customer has not consented or has opted out", details={"blocker": "CONSENT_REQUIRED"}
        )
    channel = await db.scalar(sa.select(Channel).where(Channel.kind == conversation.channel))
    available, blocker = capability(conversation.channel)
    if not available or not channel or not channel.enabled:
        raise ConflictError(
            "Channel is disabled", details={"blocker": blocker or "CHANNEL_DISABLED"}
        )
    template = next((t for t in await templates(db) if t["key"] == template_key), None)
    if template is None:
        raise NotFoundError("Template not found")

    def render(value: str) -> str:
        return value.replace(
            "{customer_name}", customer.name or ("গ্রাহক" if locale == "bn" else "customer")
        ).replace("{order_number}", order.order_number)

    message = Message(
        conversation_id=conversation.id,
        order_id=order.id,
        template_key=template_key,
        locale=locale,
        subject=render(template[f"subject_{locale}"]),
        body=render(template[f"body_{locale}"]),
        consent_version=conversation.consent_version,
        idempotency_key=idempotency_key,
        request_hash=digest,
        next_attempt_at=utc_now(),
    )
    db.add(message)
    await db.flush()
    return message


async def retry_message(db: AsyncSession, message_id: uuid.UUID) -> Message:
    row = await db.scalar(sa.select(Message).where(Message.id == message_id).with_for_update())
    if row is None:
        raise NotFoundError()
    if row.status != "FAILED" or (
        row.first_attempt_at and utc_now() - row.first_attempt_at >= timedelta(hours=23)
    ):
        raise ConflictError("This message cannot be safely retried")
    row.status, row.attempts, row.next_attempt_at = "QUEUED", 0, utc_now()
    await db.flush()
    return row
