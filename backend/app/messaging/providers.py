"""Official channel providers behind one send contract.

EMAIL goes through the platform's Resend transport. WHATSAPP goes through the
shop's own WhatsApp Business Account on Meta's Cloud API, linked in the
Integrations Hub, and only ever sends templates Meta approved. MESSENGER and
SMS are listed so sellers see why they are unavailable: a Messenger marketing
send needs Meta's per-person marketing opt-in (and V3.1 keeps no Page-scoped
IDs), and customer SMS needs an approved Bangladeshi sender. Nothing here
scrapes, automates a personal account or guesses a provider API.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations import meta, whatsapp
from app.integrations.http import ProviderError
from app.integrations.models import IntegrationConnection

KINDS = ("EMAIL", "WHATSAPP", "MESSENGER", "SMS")
#: Channels a customer contact can be recorded on.
CONTACT_KINDS = ("EMAIL", "WHATSAPP")


def reports(kind: str) -> tuple[str, ...]:
    """Receipts this deployment actually receives for a channel.

    WhatsApp reports delivered and read. Resend reports delivered and opened
    (shown as read) only once its webhook secret is configured; an email
    open depends on the recipient's client and is a floor, not a count.
    """
    if kind == "WHATSAPP":
        return ("DELIVERED", "READ")
    if kind == "EMAIL" and get_settings().email_webhook_secret is not None:
        return ("DELIVERED", "READ")
    return ()


def capability(kind: str) -> tuple[bool, str | None]:
    """Platform-level availability: is the official provider set up at all."""
    settings = get_settings()
    if kind == "EMAIL":
        if (
            settings.email_transport_can_deliver
            and (settings.email_api_base_url or "").rstrip("/") == "https://api.resend.com"
        ):
            return True, None
        return False, "EMAIL_OFFICIAL_PROVIDER_REQUIRED"
    if kind == "WHATSAPP":
        if not meta.configured():
            return False, "META_APP_SETUP_REQUIRED"
        return True, None
    if kind == "MESSENGER":
        return False, "MESSENGER_MARKETING_OPT_IN_REQUIRED"
    return False, f"{kind}_OFFICIAL_PROVIDER_REQUIRED"


async def whatsapp_connection(db: AsyncSession) -> IntegrationConnection | None:
    return await db.scalar(
        sa.select(IntegrationConnection)
        .where(
            IntegrationConnection.provider == "WHATSAPP",
            IntegrationConnection.state == "CONNECTED",
        )
        .order_by(IntegrationConnection.created_at)
        .limit(1)
    )


async def shop_blocker(db: AsyncSession, kind: str) -> str | None:
    """What this shop still has to do, once the platform provider exists."""
    if kind == "WHATSAPP" and await whatsapp_connection(db) is None:
        return "WHATSAPP_CONNECTION_REQUIRED"
    return None


@dataclass(frozen=True)
class Outbound:
    message_id: uuid.UUID
    channel: str
    purpose: str
    to: str
    subject: str
    body: str
    template_name: str | None = None
    language: str | None = None
    params: list[str] = field(default_factory=list)
    unsubscribe_url: str | None = None


@dataclass(frozen=True)
class Sent:
    #: SENT, RETRY (provably not sent), FAILED, or UNKNOWN (the request may
    #: have reached the provider; WhatsApp has no idempotency key to resend under).
    outcome: str
    provider: str
    reference: str | None = None
    error: str | None = None


async def send(db: AsyncSession, message: Outbound, *, email_transport: Any = None) -> Sent:
    if message.channel == "EMAIL":
        return await _email(message, email_transport)
    if message.channel == "WHATSAPP":
        return await _whatsapp(db, message)
    return Sent("FAILED", message.channel.lower(), error="CHANNEL_NOT_SUPPORTED")


async def _email(message: Outbound, transport: Any) -> Sent:
    from app.api.deps import get_email_transport
    from app.notifications.transport import EmailMessage

    headers: dict[str, str] | None = None
    if message.unsubscribe_url:
        headers = {
            "List-Unsubscribe": f"<{message.unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
    result = await (transport or get_email_transport()).send(
        EmailMessage(
            to_address=message.to,
            subject=message.subject,
            text_body=message.body,
            idempotency_key=f"customer-message/{message.message_id}",
            headers=headers,
        )
    )
    provider = "resend" if result.provider == "provider_api" else result.provider
    if result.delivered:
        return Sent("SENT", provider, result.provider_reference)
    outcome = "RETRY" if result.retryable else "FAILED"
    return Sent(outcome, provider, result.provider_reference, str(result.outcome))


async def _whatsapp(db: AsyncSession, message: Outbound) -> Sent:
    from app.integrations.service import mark_error, unseal

    conn = await whatsapp_connection(db)
    if conn is None or not message.template_name or not message.language:
        return Sent("FAILED", "whatsapp", error="WHATSAPP_CONNECTION_REQUIRED")
    credentials: dict[str, Any] = unseal(conn)
    try:
        wamid = await whatsapp.send_template(
            credentials.get("phone_number_id", ""),
            credentials.get("access_token", ""),
            to_e164=message.to,
            name=message.template_name,
            language=message.language,
            params=message.params,
        )
    except ProviderError as exc:
        mark_error(conn, exc.code)
        if exc.code in {"PROVIDER_RATE_LIMITED", "STORE_UNREACHABLE"}:
            return Sent("RETRY", "whatsapp", error=exc.code)
        if exc.code in {"PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"}:
            return Sent("UNKNOWN", "whatsapp", error=exc.code)
        return Sent("FAILED", "whatsapp", error=exc.code)
    return Sent("SENT", "whatsapp", wamid)
