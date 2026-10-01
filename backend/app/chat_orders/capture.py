"""Turn verified Meta webhook deliveries into durable inbound message rows.

Runs inside the webhook request, after the signature check and after the
receiver has become the connection's shop. It only records: one row per
provider message id (a retry is a no-op) and the sender's identity. Reading the
messages as an order happens later, in :mod:`app.chat_orders.jobs`, so a slow
parse never makes Meta retry a delivery.

Never logs message content, and never stores an attachment's URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hasher, get_vault
from app.chat_orders.models import ChatIdentity, ChatMessage
from app.common.phone import try_normalize_bd_phone
from app.core.clock import utc_now
from app.core.ids import new_id
from app.integrations.models import IntegrationConnection
from app.messaging.receipts import stop_word

#: Longest text kept from one message; a pasted essay is cut, not refused.
MAX_TEXT = 2000
#: Messages read from one webhook delivery.
MAX_PER_DELIVERY = 50

_MESSENGER_ATTACHMENTS = {
    "image": "IMAGE",
    "audio": "AUDIO",
    "video": "VIDEO",
    "file": "DOCUMENT",
}
_WHATSAPP_TYPES = {
    "image": "IMAGE",
    "audio": "AUDIO",
    "video": "VIDEO",
    "document": "DOCUMENT",
    "sticker": "STICKER",
}


@dataclass(frozen=True)
class Inbound:
    """One customer message, provider-neutral, before it is stored."""

    provider: str
    message_id: str
    sender_id: str
    account_ref: str
    message_type: str
    text: str | None
    attachments: int
    at: datetime
    display_name: str | None = None


def _when(value: Any, *, millis: bool) -> datetime:
    try:
        number = int(value)
        return datetime.fromtimestamp(number / 1000 if millis else number, tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return utc_now()


def _clip(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text[:MAX_TEXT] if text else None


# ---------------------------------------------------------------- parsing ---


def messenger_events(page_id: str, entry: dict[str, Any]) -> list[Inbound]:
    """Customer messages to ``page_id`` from one ``entry``.

    Echoes (the Page's own replies), delivery and read receipts, postbacks and
    anything not addressed to this Page are skipped.
    """
    found: list[Inbound] = []
    for event in (entry.get("messaging") or [])[:MAX_PER_DELIVERY]:
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        sender = str((event.get("sender") or {}).get("id") or "")
        recipient = str((event.get("recipient") or {}).get("id") or "")
        if not isinstance(message, dict) or message.get("is_echo"):
            continue
        if not sender or sender == page_id or recipient != page_id:
            continue
        mid = message.get("mid")
        if not isinstance(mid, str) or not mid or len(mid) > 200:
            continue
        attachments = [a for a in message.get("attachments") or [] if isinstance(a, dict)]
        text = _clip(message.get("text"))
        if text is not None:
            kind = "TEXT"
        elif attachments:
            first = attachments[0]
            sticker = (
                (first.get("payload") or {}).get("sticker_id")
                if isinstance(first.get("payload"), dict)
                else None
            )
            kind = (
                "STICKER"
                if sticker
                else _MESSENGER_ATTACHMENTS.get(str(first.get("type")), "OTHER")
            )
        else:
            continue
        found.append(
            Inbound(
                provider="MESSENGER",
                message_id=mid,
                sender_id=sender[:64],
                account_ref=page_id,
                message_type=kind,
                text=text,
                attachments=len(attachments),
                at=_when(event.get("timestamp"), millis=True),
            )
        )
    return found


def whatsapp_events(phone_number_id: str, value: dict[str, Any]) -> list[Inbound]:
    """Customer messages in one WhatsApp ``changes[].value``.

    Status receipts live in ``value.statuses`` and are not messages; reactions
    and system notices are skipped.
    """
    names = {
        str(contact.get("wa_id")): str((contact.get("profile") or {}).get("name") or "")[:120]
        for contact in value.get("contacts") or []
        if isinstance(contact, dict) and contact.get("wa_id")
    }
    found: list[Inbound] = []
    for message in (value.get("messages") or [])[:MAX_PER_DELIVERY]:
        if not isinstance(message, dict):
            continue
        sender = str(message.get("from") or "")
        message_id = message.get("id")
        if not sender or not sender.isdigit() or len(sender) > 20:
            continue
        if not isinstance(message_id, str) or not message_id or len(message_id) > 200:
            continue
        kind_raw = str(message.get("type") or "")
        text: str | None = None
        if kind_raw == "text":
            text = _clip((message.get("text") or {}).get("body"))
            kind = "TEXT"
        elif kind_raw == "button":
            text = _clip((message.get("button") or {}).get("text"))
            kind = "TEXT"
        elif kind_raw == "interactive":
            reply = message.get("interactive") or {}
            chosen = reply.get("button_reply") or reply.get("list_reply") or {}
            text = _clip(chosen.get("title"))
            kind = "TEXT"
        elif kind_raw in _WHATSAPP_TYPES:
            kind = _WHATSAPP_TYPES[kind_raw]
        elif kind_raw in {"reaction", "system", "unsupported", "ephemeral"}:
            continue
        else:
            kind = "OTHER"
        if kind == "TEXT" and text is None:
            continue
        if text is not None and stop_word(text):
            continue  # an opt-out, handled by messaging receipts; not order data
        found.append(
            Inbound(
                provider="WHATSAPP",
                message_id=message_id,
                sender_id=sender,
                account_ref=phone_number_id,
                message_type=kind,
                text=text,
                attachments=0 if kind == "TEXT" else 1,
                at=_when(message.get("timestamp"), millis=False),
                display_name=names.get(sender) or None,
            )
        )
    return found


# ---------------------------------------------------------------- storing ---


def identity_key(provider: str, account_ref: str, sender_id: str) -> str:
    return get_hasher().chat_identity_hash(provider, account_ref, sender_id)


async def identity_for(
    db: AsyncSession, conn: IntegrationConnection, inbound: Inbound
) -> ChatIdentity:
    """The sender's identity on this connection, created on first contact."""
    key = identity_key(inbound.provider, inbound.account_ref, inbound.sender_id)
    query = sa.select(ChatIdentity).where(
        ChatIdentity.connection_id == conn.id, ChatIdentity.external_key == key
    )
    found = await db.scalar(query)
    if found is not None:
        if inbound.display_name and not found.display_name:
            found.display_name = inbound.display_name
        return found
    row_id = new_id()
    phone = (
        try_normalize_bd_phone("+" + inbound.sender_id) if inbound.provider == "WHATSAPP" else None
    )
    identity = ChatIdentity(
        id=row_id,
        connection_id=conn.id,
        provider=inbound.provider,
        external_key=key,
        external_id_enc=get_vault().encrypt(inbound.sender_id, context=f"chat-identity:{row_id}"),
        account_ref=inbound.account_ref,
        display_name=inbound.display_name,
        phone_masked=phone.masked if phone else None,
    )
    try:
        async with db.begin_nested():
            db.add(identity)
            await db.flush()
    except IntegrityError:
        # A concurrent delivery from the same customer created it first.
        found = await db.scalar(query)
        if found is None:
            raise
        return found
    return identity


def external_id(identity: ChatIdentity) -> str:
    """The PSID or WhatsApp id. Never logged; used only to match a phone."""
    return get_vault().decrypt(identity.external_id_enc, context=f"chat-identity:{identity.id}")


async def record(db: AsyncSession, conn: IntegrationConnection, messages: list[Inbound]) -> int:
    """Store each new message once. Returns how many were new."""
    stored = 0
    for inbound in messages:
        exists = await db.scalar(
            sa.select(ChatMessage.id).where(
                ChatMessage.connection_id == conn.id,
                ChatMessage.provider == inbound.provider,
                ChatMessage.provider_message_id == inbound.message_id,
            )
        )
        if exists is not None:
            continue
        identity = await identity_for(db, conn, inbound)
        row = ChatMessage(
            connection_id=conn.id,
            identity_id=identity.id,
            provider=inbound.provider,
            provider_message_id=inbound.message_id,
            account_ref=inbound.account_ref,
            message_type=inbound.message_type,
            text=inbound.text,
            attachment_count=inbound.attachments,
            provider_timestamp=inbound.at,
            received_at=utc_now(),
            processing_status="RECEIVED",
        )
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except IntegrityError:
            continue  # the same delivery, retried concurrently
        stored += 1
    return stored
