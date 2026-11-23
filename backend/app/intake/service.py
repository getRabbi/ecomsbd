"""The Intake service: the one way a customer message enters ecomsbd (V4 WP1.3).

Rule L1: every order source — a Meta webhook, a linked phone's notification,
the Chrome extension, a shared or pasted message — calls :func:`capture` and
ends up as ``chat_messages`` rows of one sender identity. The chat-order
worker (:mod:`app.chat_orders.jobs`) then folds them into a
``chat_order_drafts`` row exactly as it always has; there is no second draft
system.

What a capture records:

*   the sender's identity on the connection, created on first contact. Its
    lookup key is a keyed hash of ``provider | account | conversation``: for
    Meta the Page/number and the PSID/WhatsApp id (unchanged from V3), for a
    device the app package and the conversation key the device saw;
*   one ``RECEIVED`` message per new message. Meta messages are keyed by the
    provider's own id; a device message by a keyed hash of its identity, sent
    time and text, so a re-upload of the same notification is a no-op.

Nothing is parsed here and nothing is logged about the content: message text,
sender names and conversation keys never reach a log, an exception or Sentry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hasher, get_vault
from app.chat_orders.models import (
    CAPTURE_SOURCES,
    DEVICE,
    ChatIdentity,
    ChatMessage,
)
from app.common.phone import try_normalize_bd_phone
from app.core.clock import utc_now
from app.core.errors import NotFoundError, ValidationError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.integrations.models import IntegrationConnection

__all__ = [
    "MANUAL_PLATFORM",
    "CaptureResult",
    "CapturedMessage",
    "capture",
    "identity_key",
    "is_manual",
    "manual_connection",
    "message_hash",
]

log = get_logger(__name__)

#: A device's clock may run ahead; a message is never dated in the future.
_CLOCK_SKEW = timedelta(minutes=5)
#: ``config.platform`` of the shop's share/paste connection.
MANUAL_PLATFORM = "manual"


@dataclass(frozen=True, slots=True)
class CapturedMessage:
    """One customer message, provider-neutral, before it is stored."""

    text: str | None
    #: When it was sent, as the source reported it (``None`` = not known).
    at: datetime | None
    #: The provider's own id (Meta). ``None`` = derive it from the content.
    message_id: str | None = None
    #: TEXT, IMAGE, AUDIO, VIDEO, DOCUMENT, STICKER or OTHER.
    message_type: str = "TEXT"
    attachments: int = 0


@dataclass(frozen=True, slots=True)
class CaptureResult:
    #: ``None`` when every message was already stored.
    identity_id: uuid.UUID | None
    #: The new ``chat_messages`` rows, in the order they were given.
    stored: list[uuid.UUID]
    duplicates: int
    #: Rows that were already stored (a re-upload), in the order given.
    existing: list[uuid.UUID]


def identity_key(provider: str, account_ref: str, conversation_key: str) -> str:
    return get_hasher().chat_identity_hash(provider, account_ref, conversation_key)


def message_hash(key: str, account_ref: str, at: datetime | None, text: str) -> str:
    sent = str(int(at.timestamp() * 1000)) if at is not None else "-"
    return "h1:" + get_hasher().chat_message_hash(account_ref, key, sent, text)


async def _identity(
    db: AsyncSession,
    conn: IntegrationConnection,
    *,
    provider: str,
    account_ref: str,
    conversation_key: str,
    key: str,
    display_name: str | None,
) -> ChatIdentity:
    """The sender's identity on this connection, created on first contact."""
    query = sa.select(ChatIdentity).where(
        ChatIdentity.connection_id == conn.id, ChatIdentity.external_key == key
    )
    found = await db.scalar(query)
    if found is not None:
        if display_name and not found.display_name:
            found.display_name = display_name
        return found
    row_id = new_id()
    # Only a WhatsApp id is a phone number. A device's conversation key is
    # whatever title the app showed, never read as a number.
    phone = try_normalize_bd_phone("+" + conversation_key) if provider == "WHATSAPP" else None
    identity = ChatIdentity(
        id=row_id,
        connection_id=conn.id,
        provider=provider,
        external_key=key,
        external_id_enc=get_vault().encrypt(conversation_key, context=f"chat-identity:{row_id}"),
        account_ref=account_ref,
        display_name=display_name,
        phone_masked=phone.masked if phone else None,
    )
    try:
        async with db.begin_nested():
            db.add(identity)
            await db.flush()
    except IntegrityError:
        # A concurrent capture from the same conversation created it first.
        found = await db.scalar(query)
        if found is None:
            raise
        return found
    return identity


async def manual_connection(
    db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> IntegrationConnection:
    """The shop's own connection for shared and pasted messages.

    Chat rows always belong to a connection; a share or a paste from the web
    has no linked device, so the shop gets one hidden DEVICE connection with
    platform ``manual``. It has no ingest key, never heartbeats, and is not
    listed in the Integrations Hub.
    """
    account_key = f"manual:{tenant_id}"
    query = sa.select(IntegrationConnection).where(
        IntegrationConnection.provider == DEVICE,
        IntegrationConnection.account_key == account_key,
    )
    found = await db.scalar(query)
    if found is not None:
        return found
    conn = IntegrationConnection(
        tenant_id=tenant_id,
        provider=DEVICE,
        name="Shared and pasted messages",
        state="CONNECTED",
        account_key=account_key,
        config={"platform": MANUAL_PLATFORM},
        created_by=user_id,
    )
    try:
        async with db.begin_nested():
            db.add(conn)
            await db.flush()
    except IntegrityError:
        found = await db.scalar(query)
        if found is None:
            raise
        return found
    return conn


def is_manual(conn: IntegrationConnection) -> bool:
    return conn.provider == DEVICE and (conn.config or {}).get("platform") == MANUAL_PLATFORM


async def capture(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection: IntegrationConnection,
    source: str,
    provider: str,
    account_ref: str,
    conversation_key: str,
    sender_display: str | None,
    messages: list[CapturedMessage],
) -> CaptureResult:
    """Record one conversation's messages for the chat-order worker.

    ``provider`` is MESSENGER or WHATSAPP for a Meta webhook (``account_ref``
    the Page or number, ``conversation_key`` the sender id) and DEVICE for
    everything else (``account_ref`` the app package or source).
    """
    if connection.tenant_id != tenant_id:
        # The connection was resolved for another shop: never record across.
        log.error(
            "intake capture across tenants refused",
            extra={"connection_id": str(connection.id), "tenant_id": str(tenant_id)},
        )
        raise NotFoundError()
    if source not in CAPTURE_SOURCES:
        raise ValidationError("Unknown capture source", details={"field": "source"})
    if (provider == DEVICE) != (connection.provider == DEVICE):
        raise ValidationError("Provider does not match the connection")
    key = identity_key(provider, account_ref, conversation_key)
    now = utc_now()
    pending: list[tuple[str, CapturedMessage]] = []
    seen: set[str] = set()
    existing: list[uuid.UUID] = []
    duplicates = 0
    for message in messages:
        message_id = message.message_id or message_hash(
            key, account_ref, message.at, message.text or ""
        )
        if message_id in seen:
            duplicates += 1
            continue
        seen.add(message_id)
        exists = await db.scalar(
            sa.select(ChatMessage.id).where(
                ChatMessage.connection_id == connection.id,
                ChatMessage.provider == provider,
                ChatMessage.provider_message_id == message_id,
            )
        )
        if exists is not None:
            duplicates += 1
            existing.append(exists)
            continue
        pending.append((message_id, message))
    if not pending:
        return CaptureResult(identity_id=None, stored=[], duplicates=duplicates, existing=existing)

    identity = await _identity(
        db,
        connection,
        provider=provider,
        account_ref=account_ref,
        conversation_key=conversation_key,
        key=key,
        display_name=sender_display,
    )
    stored: list[uuid.UUID] = []
    for index, (message_id, message) in enumerate(pending):
        at = message.at or now
        if provider == DEVICE and at > now + _CLOCK_SKEW:
            at = now
        row = ChatMessage(
            connection_id=connection.id,
            identity_id=identity.id,
            provider=provider,
            provider_message_id=message_id,
            account_ref=account_ref,
            capture_source=source,
            message_type=message.message_type,
            text=message.text,
            attachment_count=message.attachments,
            provider_timestamp=at,
            # A batch keeps its order even when the device gave one time to all.
            received_at=now + timedelta(microseconds=index) if provider == DEVICE else utc_now(),
            processing_status="RECEIVED",
        )
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except IntegrityError:
            duplicates += 1  # the same message, captured concurrently
            continue
        stored.append(row.id)
    return CaptureResult(
        identity_id=identity.id, stored=stored, duplicates=duplicates, existing=existing
    )
