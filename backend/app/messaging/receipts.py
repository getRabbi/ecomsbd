"""What providers report back: delivery receipts, opt-outs and bounces.

Statuses only ever move forward (sent -> delivered -> read) and only on a
verified provider callback. Nothing here infers a delivery that a provider did
not report, and message contents a customer sends are never stored: an inbound
WhatsApp message is read for an opt-out keyword and dropped.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.phone import try_normalize_bd_phone
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.context import ActorType, use_context
from app.db.tenancy import allow_cross_tenant
from app.messaging.models import MARKETING, Conversation, Message
from app.messaging.service import recipient_digest, set_marketing_consent

RANK = {"SENT": 1, "DELIVERED": 2, "READ": 3}
#: Opt-out words, compared after trimming, case-folding and Unicode NFC.
STOP_WORDS = frozenset({"stop", "unsubscribe", "stop all", "cancel", "বন্ধ", "বন্ধ করুন", "আনসাবস্ক্রাইব"})
SVIX_TOLERANCE_SECONDS = 5 * 60


@asynccontextmanager
async def as_shop(db: AsyncSession, tenant_id: uuid.UUID, job: str) -> AsyncIterator[None]:
    with use_context(
        tenant_id=tenant_id, actor_type=ActorType.PROVIDER, user_id=None, job_name=job
    ):
        yield
        await db.flush()


def _at(timestamp: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(timestamp), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return utc_now()


def apply_status(message: Message, status: str, at: datetime, error: str | None = None) -> bool:
    """Move a message forward. Returns whether anything changed."""
    if status == "FAILED":
        if message.status in {"DELIVERED", "READ", "FAILED"}:
            return False
        message.status, message.failed_at, message.last_error = "FAILED", at, error
        return True
    if RANK.get(status, 0) <= RANK.get(message.status, 0):
        return False
    message.status = status
    if status in {"SENT", "DELIVERED", "READ"}:
        message.sent_at = message.sent_at or at
    if status in {"DELIVERED", "READ"}:
        message.delivered_at = message.delivered_at or at
    if status == "READ":
        message.read_at = message.read_at or at
    return True


async def _conversation(db: AsyncSession, message: Message) -> Conversation | None:
    return await db.scalar(
        sa.select(Conversation).where(Conversation.id == message.conversation_id).with_for_update()
    )


async def opt_out(
    db: AsyncSession,
    conversation: Conversation,
    *,
    source: str,
    evidence: str,
    campaign: uuid.UUID | None = None,
) -> bool:
    if not conversation.marketing_consent and conversation.marketing_opted_out_at is not None:
        return False
    await set_marketing_consent(
        db, conversation, False, evidence, None, source=source, campaign_id=campaign
    )
    return True


# ------------------------------------------------------------ Resend (email) ---


def valid_svix(headers: dict[str, str], body: bytes, now: float | None = None) -> bool:
    """Resend signs webhooks with Svix: HMAC-SHA256 over ``id.timestamp.body``."""
    secret = get_settings().email_webhook_secret
    if secret is None:
        return False
    msg_id, stamp = headers.get("svix-id"), headers.get("svix-timestamp")
    signatures = headers.get("svix-signature") or ""
    if not msg_id or not stamp or not stamp.isdigit():
        return False
    if abs((now or time.time()) - int(stamp)) > SVIX_TOLERANCE_SECONDS:
        return False
    raw = secret.get_secret_value()
    try:
        key = base64.b64decode(raw.removeprefix("whsec_"))
    except ValueError:
        return False
    signed = f"{msg_id}.{stamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for candidate in signatures.split():
        version, _, value = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return True
    return False


RESEND_EVENTS = {
    "email.sent": "SENT",
    "email.delivered": "DELIVERED",
    "email.opened": "READ",
    "email.bounced": "FAILED",
}


async def resend_event(db: AsyncSession, payload: dict[str, Any]) -> bool:
    kind = payload.get("type")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reference = data.get("email_id") if data else None
    if not isinstance(kind, str) or not isinstance(reference, str) or not reference:
        return False
    with allow_cross_tenant("resend receipt: message by provider reference"):
        message = await db.scalar(
            sa.select(Message).where(
                Message.provider == "resend", Message.provider_reference == reference[:200]
            )
        )
    if message is None:
        return False  # Account email (verification, reset) or another system's mail.
    async with as_shop(db, message.tenant_id, "messaging:resend"):
        message = await db.scalar(
            sa.select(Message).where(Message.id == message.id).with_for_update()
        )
        if message is None:
            return False
        at = utc_now()
        if kind == "email.complained":
            conversation = await _conversation(db, message)
            if conversation is not None:
                await opt_out(
                    db,
                    conversation,
                    source="PROVIDER_COMPLAINT",
                    evidence="Recipient marked the email as spam (Resend complaint)",
                    campaign=message.campaign_id,
                )
            return True
        status = RESEND_EVENTS.get(kind)
        if status is None:
            return False
        changed = apply_status(message, status, at, "BOUNCED" if status == "FAILED" else None)
        if kind == "email.bounced":
            conversation = await _conversation(db, message)
            if conversation is not None and conversation.undeliverable_at is None:
                conversation.undeliverable_at, conversation.undeliverable_reason = at, "BOUNCED"
        return changed


# --------------------------------------------------------------- WhatsApp ---


WHATSAPP_STATUSES = {"sent": "SENT", "delivered": "DELIVERED", "read": "READ", "failed": "FAILED"}


def stop_word(text: str) -> bool:
    normalised = unicodedata.normalize("NFC", text).strip().strip(".!").casefold()
    return normalised in STOP_WORDS


async def whatsapp_value(db: AsyncSession, tenant_id: uuid.UUID, value: dict[str, Any]) -> int:
    """One ``changes[].value`` for a connected number. Returns rows changed."""
    changed = 0
    async with as_shop(db, tenant_id, "messaging:whatsapp"):
        for status in (value.get("statuses") or [])[:200]:
            if not isinstance(status, dict) or not isinstance(status.get("id"), str):
                continue
            mapped = WHATSAPP_STATUSES.get(str(status.get("status")))
            if mapped is None:
                continue
            message = await db.scalar(
                sa.select(Message)
                .where(Message.provider == "whatsapp", Message.provider_reference == status["id"])
                .with_for_update()
            )
            if message is None:
                continue
            errors = status.get("errors") or []
            code = errors[0].get("code") if errors and isinstance(errors[0], dict) else None
            error = f"WA_{code}" if isinstance(code, int) else None
            changed += apply_status(message, mapped, _at(status.get("timestamp")), error)
        for inbound in (value.get("messages") or [])[:200]:
            if not isinstance(inbound, dict) or inbound.get("type") != "text":
                continue
            body = (inbound.get("text") or {}).get("body")
            sender = try_normalize_bd_phone("+" + str(inbound.get("from") or ""))
            if not isinstance(body, str) or sender is None:
                continue
            if not stop_word(body):
                changed += await _announce_reply(db, sender.e164, str(inbound.get("id") or ""))
                continue
            conversation = await db.scalar(
                sa.select(Conversation)
                .where(
                    Conversation.channel == "WHATSAPP",
                    Conversation.recipient_hash == recipient_digest("WHATSAPP", sender.e164),
                )
                .with_for_update()
            )
            if conversation is not None:
                changed += await opt_out(
                    db,
                    conversation,
                    source="KEYWORD",
                    evidence="Customer replied STOP on WhatsApp",
                )
    return changed


async def _announce_reply(db: AsyncSession, e164: str, message_id: str) -> int:
    """A known customer wrote back on WhatsApp: a fact workflows may wait for.

    Only the fact is kept. The text itself is never stored (see module docs).
    """
    from app.common.outbox import OutboxEvent, OutboxTopic, enqueue

    if not message_id or len(message_id) > 150:
        return 0
    conversation = await db.scalar(
        sa.select(Conversation).where(
            Conversation.channel == "WHATSAPP",
            Conversation.recipient_hash == recipient_digest("WHATSAPP", e164),
        )
    )
    if conversation is None:
        return 0
    key = f"wa-reply:{message_id}"
    if await db.scalar(sa.select(OutboxEvent.id).where(OutboxEvent.dedupe_key == key)):
        return 0  # a redelivered webhook
    await enqueue(
        db,
        OutboxTopic.CUSTOMER_REPLIED,
        {"customer_id": str(conversation.customer_id), "channel": "WHATSAPP"},
        tenant_id=conversation.tenant_id,
        dedupe_key=key,
    )
    return 1


# ------------------------------------------------------------ unsubscribe ---


async def by_unsubscribe_token(db: AsyncSession, token: str) -> Conversation | None:
    if not token or len(token) > 100:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with allow_cross_tenant("unsubscribe link resolution"):
        return await db.scalar(
            sa.select(Conversation).where(Conversation.unsubscribe_hash == digest)
        )


async def unsubscribe(db: AsyncSession, token: str) -> bool:
    found = await by_unsubscribe_token(db, token)
    if found is None:
        return False
    async with as_shop(db, found.tenant_id, "messaging:unsubscribe"):
        conversation = await db.scalar(
            sa.select(Conversation).where(Conversation.id == found.id).with_for_update()
        )
        if conversation is None:
            return False
        last = await db.scalar(
            sa.select(Message.campaign_id)
            .where(Message.conversation_id == conversation.id, Message.purpose == MARKETING)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        await opt_out(
            db,
            conversation,
            source="UNSUBSCRIBE_LINK",
            evidence="Customer used the unsubscribe link",
            campaign=last,
        )
    return True
