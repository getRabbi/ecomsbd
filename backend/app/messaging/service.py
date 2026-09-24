from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hasher, get_vault
from app.common.idempotency import request_hash
from app.common.operation_lock import lock_shop
from app.common.phone import try_normalize_bd_phone
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.errors import ConflictError, IdempotencyConflictError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.customers.models import Customer
from app.messaging.models import (
    MARKETING,
    TRANSACTIONAL,
    Channel,
    ConsentEvent,
    Conversation,
    Message,
    MessageTemplate,
)
from app.messaging.providers import CONTACT_KINDS, shop_blocker
from app.messaging.providers import capability as provider_capability
from app.orders.models import Order

BUILTIN = {
    "key": "order_update",
    "subject_en": "Order {order_number} update",
    "subject_bn": "অর্ডার {order_number} আপডেট",
    "body_en": "Hello {customer_name}, please contact your seller for an update on order {order_number}.",
    "body_bn": "প্রিয় {customer_name}, অর্ডার {order_number} সম্পর্কে জানতে আপনার বিক্রেতার সঙ্গে যোগাযোগ করুন।",
}
BUILTIN_VIEW = {
    **BUILTIN,
    "purpose": TRANSACTIONAL,
    "channel": "EMAIL",
    "provider_name": None,
    "provider_language_bn": None,
    "provider_language_en": None,
    "provider_category": None,
    "provider_status": None,
    "variables": None,
    "archived": False,
    "builtin": True,
}
#: Placeholders a template may use. Order numbers belong to order updates only;
#: a marketing message is not about an order.
PLACEHOLDERS = {
    TRANSACTIONAL: ("customer_name", "order_number", "shop_name"),
    MARKETING: ("customer_name", "shop_name"),
}
TEMPLATE_FIELDS = (
    "key",
    "purpose",
    "channel",
    "subject_en",
    "subject_bn",
    "body_en",
    "body_bn",
    "provider_name",
    "provider_language_bn",
    "provider_language_en",
    "provider_category",
    "provider_status",
    "variables",
    "archived",
)
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_EMAIL = re.compile(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+")
MARKETING_FOOTER = {
    "en": "\n\n—\nYou receive this because you agreed to offers from {shop_name}. "
    "Unsubscribe: {unsubscribe_url}",
    "bn": "\n\n—\n{shop_name} থেকে অফার পেতে আপনি সম্মতি দিয়েছেন। আর না পেতে: {unsubscribe_url}",
}


def capability(kind: str) -> tuple[bool, str | None]:
    return provider_capability(kind)


async def required(db: AsyncSession, model: Any, row_id: uuid.UUID) -> Any:
    row = await db.scalar(sa.select(model).where(model.id == row_id))
    if row is None or getattr(row, "deleted_at", None) is not None:
        raise NotFoundError()
    return row


# ------------------------------------------------------------ templates ---


def validate_template(values: dict[str, Any]) -> None:
    purpose = values.get("purpose") or TRANSACTIONAL
    channel = values.get("channel") or "EMAIL"
    allowed = PLACEHOLDERS[purpose]
    texts = ["body_en", "body_bn"] + (["subject_en", "subject_bn"] if channel == "EMAIL" else [])
    for key in texts:
        value = values[key]
        if not value.strip() or (key.startswith("subject") and any(c in value for c in "\r\n")):
            raise ValidationError("A non-empty template is required in both languages")
        used = set(_PLACEHOLDER.findall(value))
        remainder = _PLACEHOLDER.sub("", value)
        if "{" in remainder or "}" in remainder or not used <= set(allowed):
            raise ValidationError(
                "Unsupported placeholder",
                details={"allowed": list(allowed), "code": "TEMPLATE_PLACEHOLDER"},
            )
    if channel == "WHATSAPP":
        name = values.get("provider_name") or ""
        if not re.fullmatch(r"[a-z0-9_]{1,512}", name):
            raise ValidationError(
                "Enter the approved WhatsApp template name exactly as Meta shows it",
                details={"code": "WHATSAPP_TEMPLATE_NAME"},
            )
        if not values.get("provider_language_bn") and not values.get("provider_language_en"):
            raise ValidationError(
                "Enter at least one approved template language",
                details={"code": "WHATSAPP_TEMPLATE_LANGUAGE"},
            )
        variables = values.get("variables") or []
        if len(variables) > 10 or not set(variables) <= set(allowed):
            raise ValidationError(
                "Template parameters must be listed placeholders",
                details={"allowed": list(allowed), "code": "TEMPLATE_PLACEHOLDER"},
            )


def template_view(row: MessageTemplate) -> dict[str, Any]:
    return {
        **{key: getattr(row, key) for key in TEMPLATE_FIELDS},
        "provider_checked_at": row.provider_checked_at,
        "builtin": False,
    }


async def templates(
    db: AsyncSession, *, purpose: str | None = None, include_archived: bool = False
) -> list[dict[str, Any]]:
    query = sa.select(MessageTemplate).order_by(MessageTemplate.key)
    if not include_archived:
        query = query.where(MessageTemplate.archived.is_(False))
    rows = [template_view(row) for row in (await db.scalars(query)).all()]
    items = [BUILTIN_VIEW, *rows]
    return [item for item in items if purpose is None or item["purpose"] == purpose]


async def template(db: AsyncSession, key: str) -> dict[str, Any]:
    found = next((t for t in await templates(db) if t["key"] == key), None)
    if found is None:
        raise NotFoundError("Template not found")
    return found


def template_language(found: dict[str, Any], locale: str) -> str | None:
    """The approved WhatsApp language for a locale, falling back to the other one."""
    other = "en" if locale == "bn" else "bn"
    return found.get(f"provider_language_{locale}") or found.get(f"provider_language_{other}")


def usable(found: dict[str, Any], channel: str, purpose: str) -> str | None:
    """Why a template cannot carry this message, or None."""
    if found["archived"]:
        return "TEMPLATE_ARCHIVED"
    if found["purpose"] != purpose:
        return "TEMPLATE_PURPOSE_MISMATCH"
    if found["channel"] != channel:
        return "TEMPLATE_CHANNEL_MISMATCH"
    if channel == "WHATSAPP" and found["provider_status"] != "APPROVED":
        return "WHATSAPP_TEMPLATE_NOT_APPROVED"
    if (
        channel == "WHATSAPP"
        and purpose == TRANSACTIONAL
        and found["provider_category"] not in {"UTILITY", None}
    ):
        # A marketing-category template is never an order update.
        return "WHATSAPP_TEMPLATE_CATEGORY"
    return None


def render(text: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), ""), text)


# -------------------------------------------------------------- contacts ---


def recipient_digest(channel: str, recipient: str) -> str:
    return get_hasher().phone_search_hash(f"messaging:{channel}:{recipient.lower()}")


def _normalise(channel: str, recipient: str) -> tuple[str, str]:
    recipient = recipient.strip()
    if channel == "EMAIL":
        if not _EMAIL.fullmatch(recipient):
            raise ValidationError("A valid email recipient is required")
        local, domain = recipient.rsplit("@", 1)
        return recipient, f"{local[:1]}***@{domain}"
    if channel == "WHATSAPP":
        number = try_normalize_bd_phone(recipient)
        if number is None:
            raise ValidationError("A valid Bangladeshi mobile number is required")
        return number.e164, f"{number.national[:5]}****{number.national[-2:]}"
    raise ValidationError("Choose an official channel", details={"code": "CHANNEL_NOT_SUPPORTED"})


async def set_contact(
    db: AsyncSession,
    customer_id: uuid.UUID,
    channel: str,
    recipient: str | None,
    consent: bool,
    evidence: str,
    actor_id: uuid.UUID,
    marketing_consent: bool = False,
    use_customer_phone: bool = False,
) -> Conversation:
    await lock_shop(db)
    customer = await required(db, Customer, customer_id)
    if channel not in CONTACT_KINDS:
        raise ValidationError(
            "Choose an official channel", details={"code": "CHANNEL_NOT_SUPPORTED"}
        )
    if use_customer_phone and channel == "WHATSAPP":
        from app.customers.service import CustomerService

        recipient = (
            CustomerService(db, hasher=get_hasher(), vault=get_vault()).phone_number(customer).e164
        )
    address, masked = _normalise(channel, recipient or "")
    row = await db.scalar(
        sa.select(Conversation)
        .where(Conversation.customer_id == customer_id, Conversation.channel == channel)
        .with_for_update()
    )
    if row is None:
        row = Conversation(id=new_id(), customer_id=customer_id, channel=channel, consent_version=0)
        db.add(row)
    changed = row.recipient_hash != recipient_digest(channel, address)
    row.recipient_enc = get_vault().encrypt(address, context=f"message-contact:{row.id}")
    row.recipient_masked = masked
    row.recipient_hash = recipient_digest(channel, address)
    if changed:
        # A new address has not bounced yet, and its unsubscribe link is new.
        row.undeliverable_at = row.undeliverable_reason = None
        row.unsubscribe_hash = row.unsubscribe_enc = None
    row.consent = consent
    row.consent_version += 1
    await db.flush()
    db.add(
        ConsentEvent(conversation_id=row.id, consent=consent, evidence=evidence, actor_id=actor_id)
    )
    if marketing_consent != row.marketing_consent:
        await set_marketing_consent(db, row, marketing_consent, evidence, actor_id)
    await db.flush()
    return row


async def set_marketing_consent(
    db: AsyncSession,
    row: Conversation,
    consent: bool,
    evidence: str,
    actor_id: uuid.UUID | None,
    *,
    source: str = "SELLER",
    campaign_id: uuid.UUID | None = None,
) -> Conversation:
    now = utc_now()
    row.marketing_consent = consent
    if consent:
        row.marketing_consent_at, row.marketing_opted_out_at = now, None
    else:
        row.marketing_opted_out_at = now
    db.add(
        ConsentEvent(
            conversation_id=row.id,
            consent=consent,
            evidence=evidence[:500],
            actor_id=actor_id,
            scope=MARKETING,
            source=source,
            campaign_id=campaign_id,
        )
    )
    await db.flush()
    return row


def unsubscribe_url(row: Conversation) -> str:
    """The one-click link for this address, minted once and kept sealed."""
    if row.unsubscribe_enc:
        token = get_vault().decrypt(row.unsubscribe_enc, context=f"unsubscribe:{row.id}")
    else:
        token = secrets.token_urlsafe(24)
        row.unsubscribe_enc = get_vault().encrypt(token, context=f"unsubscribe:{row.id}")
        row.unsubscribe_hash = hashlib.sha256(token.encode()).hexdigest()
    return f"{get_settings().public_base_url.rstrip('/')}/v1/messaging/unsubscribe/{token}"


def marketing_block(row: Conversation) -> str | None:
    """Why marketing cannot reach this conversation right now, or None."""
    if row.undeliverable_at is not None:
        return "UNDELIVERABLE"
    if row.marketing_opted_out_at is not None and not row.marketing_consent:
        return "OPTED_OUT"
    if not row.marketing_consent:
        return "NO_MARKETING_CONSENT"
    return None


async def channel_blocker(db: AsyncSession, kind: str) -> str | None:
    available, blocker = capability(kind)
    if not available:
        return blocker or "CHANNEL_DISABLED"
    channel = await db.scalar(sa.select(Channel).where(Channel.kind == kind))
    if channel is None or not channel.enabled:
        return "CHANNEL_DISABLED"
    return await shop_blocker(db, kind)


# -------------------------------------------------------------- messages ---


async def shop_name(db: AsyncSession) -> str:
    from app.core.context import require_tenant_id
    from app.tenants.models import Tenant

    tenant = await db.get(Tenant, require_tenant_id())
    return tenant.name if tenant is not None else ""


def compose(
    found: dict[str, Any], locale: str, values: dict[str, str], channel: str
) -> tuple[str, str, list[str] | None]:
    subject = render(found[f"subject_{locale}"], values) if channel == "EMAIL" else ""
    body = render(found[f"body_{locale}"], values)
    params = [values.get(name, "") for name in found.get("variables") or []]
    return subject, body, params if channel == "WHATSAPP" else None


async def queue_message(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    order_id: uuid.UUID,
    template_key: str,
    locale: str,
    idempotency_key: str,
) -> Message:
    """A transactional order update. Never marketing, whatever the template says."""
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
    if conversation.undeliverable_at is not None:
        raise ConflictError(
            "The provider reported this address cannot receive messages",
            details={"blocker": "UNDELIVERABLE"},
        )
    blocker = await channel_blocker(db, conversation.channel)
    if blocker:
        raise ConflictError("Channel is disabled", details={"blocker": blocker})
    found = await template(db, template_key)
    problem = usable(found, conversation.channel, TRANSACTIONAL)
    if problem:
        raise ConflictError("Template cannot be used here", details={"blocker": problem})
    values = {
        "customer_name": customer.name or ("গ্রাহক" if locale == "bn" else "customer"),
        "order_number": order.order_number,
        "shop_name": await shop_name(db),
    }
    subject, body, params = compose(found, locale, values, conversation.channel)
    message = Message(
        conversation_id=conversation.id,
        order_id=order.id,
        template_key=template_key,
        locale=locale,
        subject=subject,
        body=body,
        params=params,
        purpose=TRANSACTIONAL,
        channel=conversation.channel,
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
    row.failed_at = None
    await db.flush()
    return row
