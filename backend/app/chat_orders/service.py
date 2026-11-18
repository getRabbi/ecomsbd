"""From inbound customer messages to a draft the seller confirms.

The flow for one message, run by the worker inside the message's shop:

1.  find the sender's open session: an unfinished draft whose last message is
    within :data:`SESSION_WINDOW`. Otherwise start a new draft;
2.  rebuild the session's transcript from its messages in the order the
    customer sent them, bounded by :data:`MAX_SESSION_MESSAGES` and
    :data:`MAX_TRANSCRIPT_CHARS`;
3.  read it with the same deterministic parser as the Inbox's "paste a
    message" (:class:`DeterministicOrderParser`), never a second parser;
4.  match the items to the catalogue and the sender to a customer;
5.  for a draft that did not come from a Meta webhook (V4 WP1.3): when its
    phone first appears, attach it to an open draft or a recent order for the
    same phone instead of keeping a second candidate order;
6.  set the draft's state, and raise one notification (and one
    ``intake.draft_ready`` event) the first time it becomes ready for review.

Nothing here creates an order. :func:`confirm` does, through
``OrderService.create`` (the same path as ``POST /v1/orders``) with the draft's
id as the order's ``client_id``: a double tap or a retried request finds the
order it already made.

Message text is never logged, raised in an error, or sent to analytics; only
codes leave this module.
"""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hasher, get_vault
from app.chat_orders import capture, matching
from app.chat_orders.models import (
    COLLECTING,
    CONFIRMED,
    DEVICE,
    EXPIRED,
    FINAL_STATES,
    IGNORED,
    META_WEBHOOK,
    NEEDS_INFO,
    OPEN_STATES,
    READY_FOR_REVIEW,
    ChatAttention,
    ChatIdentity,
    ChatMessage,
    ChatOrderDraft,
)
from app.common.audit import AuditAction, record_audit
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.common.phone import try_normalize_bd_phone
from app.core.clock import utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.customers.models import Customer
from app.customers.service import CustomerService
from app.delivery_rules.service import DeliveryRulesService
from app.integrations.models import IntegrationConnection
from app.orders.cod import CodLine, calculate_cod, is_chat_mismatch
from app.orders.models import Order, OrderChannel, OrderStatus
from app.orders.parser import DeterministicOrderParser, ParsedOrder
from app.orders.parser.base import CONFIDENCE_CONFIRM_THRESHOLD
from app.orders.zones import resolve_zone

#: Messages further apart than this start a new order session.
SESSION_WINDOW = timedelta(minutes=25)
MAX_SESSION_MESSAGES = 30
MAX_TRANSCRIPT_CHARS = 6000
#: How long an unfinished draft waits before it is closed as expired.
EXPIRY = {
    COLLECTING: timedelta(hours=2),
    NEEDS_INFO: timedelta(days=3),
    READY_FOR_REVIEW: timedelta(days=7),
}
#: Raw messages (and attention excerpts) are deleted after this. Orders and
#: customers made from them follow the shop's normal retention.
MESSAGE_RETENTION = timedelta(days=30)
#: Closed drafts are deleted after this; a confirmed one lives on as its order.
CLOSED_DRAFT_RETENTION = timedelta(days=30)
#: A later message is linked to an order placed within this window.
FOLLOW_UP_WINDOW = timedelta(days=30)
MAX_ATTEMPTS = 5
REQUIRED = ("phone", "address", "items")
#: A draft whose phone has an order this recent is about that order.
DUPLICATE_ORDER_WINDOW = timedelta(hours=24)
#: Apps whose conversations are WhatsApp chats (for the order's channel).
_WHATSAPP_APPS = frozenset({"com.whatsapp", "com.whatsapp.w4b"})
_MESSENGER_APPS = frozenset({"com.facebook.orca"})

_parser = DeterministicOrderParser()

_INTENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "CANCEL_REQUEST",
        (
            "cancel",
            "ক্যানসেল",
            "ক্যান্সেল",
            "বাতিল",
            "লাগবে না",
            "নিবো না",
            "নেব না",
            "চাই না",
            "lagbe na",
            "nibo na",
            "chai na",
        ),
    ),
    (
        "ADDRESS_CHANGE",
        (
            "ঠিকানা পরিবর্তন",
            "ঠিকানা চেঞ্জ",
            "ঠিকানা বদল",
            "নতুন ঠিকানা",
            "address change",
            "change address",
            "change the address",
            "new address",
            "address chenge",
            "thikana change",
        ),
    ),
    (
        "STATUS_QUESTION",
        (
            "কবে পাবো",
            "কবে পাব",
            "কবে দিবেন",
            "কবে আসবে",
            "কোথায় আছে",
            "পার্সেল কোথায়",
            "এখনো পাইনি",
            "পাইনি",
            "ডেলিভারি কবে",
            "kobe pabo",
            "kobe dibe",
            "kobe diben",
            "kobe asbe",
            "parcel kothay",
            "delivery kobe",
            "pai ni",
            "pai nai",
            "order status",
            "tracking",
            "where is my order",
        ),
    ),
)


def detect_intent(text: str | None) -> str | None:
    """A follow-up the seller should look at, by keyword. Never acted on."""
    if not text:
        return None
    folded = " ".join(unicodedata.normalize("NFC", text).casefold().split())
    for intent, phrases in _INTENTS:
        if any(phrase in folded for phrase in phrases):
            return intent
    return None


def _customers(db: AsyncSession) -> CustomerService:
    return CustomerService(db, hasher=get_hasher(), vault=get_vault())


# ----------------------------------------------------------- processing ---


async def process_message(db: AsyncSession, message_id: uuid.UUID) -> str:
    """Fold one received message into its sender's draft. Returns a code.

    Runs inside the message's shop. Safe to call twice: a message is handled
    only while it is ``RECEIVED``.
    """
    message = await db.scalar(
        sa.select(ChatMessage).where(ChatMessage.id == message_id).with_for_update()
    )
    if message is None or message.processing_status != "RECEIVED":
        return "SKIPPED"
    message.attempts += 1
    conn = await db.get(IntegrationConnection, message.connection_id)
    if conn is None or conn.state != "CONNECTED":
        message.processing_status, message.processing_code = "IGNORED", "CONNECTION_NOT_ACTIVE"
        return "IGNORED"
    # The sender's row is the per-customer lock: two workers never build two
    # drafts from one burst of messages.
    identity = await db.scalar(
        sa.select(ChatIdentity).where(ChatIdentity.id == message.identity_id).with_for_update()
    )
    if identity is None:
        message.processing_status, message.processing_code = "FAILED", "IDENTITY_MISSING"
        return "FAILED"
    at = message.provider_timestamp
    if identity.last_message_at is None or identity.last_message_at < at:
        identity.last_message_at = at
    await _link_whatsapp_sender(db, identity)

    if message.message_type == "TEXT" and message.provider == "WHATSAPP":
        from app.messaging.receipts import stop_word

        if stop_word(message.text or ""):
            # An opt-out keyword, handled by messaging receipts; not order data.
            message.processing_status, message.processing_code = "IGNORED", "OPT_OUT_KEYWORD"
            return "IGNORED"

    draft = await _open_session(db, identity, at)
    if draft is None and message.message_type == "TEXT":
        attention = await _follow_up(db, identity, message)
        if attention is not None:
            message.processing_status = "PROCESSED"
            message.linked_attention_id = attention.id
            message.linked_customer_id = attention.customer_id
            return "ATTENTION"

    if draft is None:
        draft = ChatOrderDraft(
            connection_id=conn.id,
            identity_id=identity.id,
            provider=message.provider,
            capture_source=message.capture_source,
            status=COLLECTING,
            customer_match="NONE",
            customer_candidates=[],
            phones=[],
            items=[],
            warnings=[],
            uncertain_fields=[],
            missing_fields=list(REQUIRED),
            source_message_ids=[],
            first_message_at=at,
            last_message_at=at,
        )
        db.add(draft)
        await db.flush()
    elif draft.message_count >= MAX_SESSION_MESSAGES:
        message.processing_status, message.processing_code = "PROCESSED", "SESSION_FULL"
        message.linked_draft_id = draft.id
        draft.warnings = sorted({*draft.warnings, "SESSION_FULL"})
        return "SESSION_FULL"

    message.linked_draft_id = draft.id
    message.linked_customer_id = identity.customer_id
    draft.source_message_ids = [*draft.source_message_ids, str(message.id)]
    draft.message_count += 1
    draft.first_message_at = min(draft.first_message_at, at)
    draft.last_message_at = max(draft.last_message_at, at)
    if message.message_type != "TEXT":
        draft.attachment_count += max(1, message.attachment_count)
    await db.flush()
    # A draft another sender's messages were merged into is read as its own
    # sender's; for Meta the owner is always this message's sender.
    owner = identity
    if draft.identity_id != identity.id:
        owner = await db.get(ChatIdentity, draft.identity_id) or identity
    phone_before = draft.selected_phone
    await rebuild(db, draft, owner)
    message.processing_status = "PROCESSED"
    if (
        draft.capture_source != META_WEBHOOK
        and draft.selected_phone
        and draft.selected_phone != phone_before
    ):
        draft = await _attach_duplicate(db, draft)
        message.linked_draft_id = draft.id
    await _announce_if_ready(db, draft)
    return draft.status


async def _link_whatsapp_sender(db: AsyncSession, identity: ChatIdentity) -> None:
    """A WhatsApp sender's own number may already be a customer of the shop.

    Phone numbers are unique per shop, so a hit is the one customer. Messenger
    gives no number and is never linked this way.
    """
    if identity.provider != "WHATSAPP" or identity.customer_id is not None:
        return
    number = try_normalize_bd_phone("+" + capture.external_id(identity))
    if number is None:
        return
    customer = await _customers(db).find_by_phone(number.e164)
    if customer is not None:
        identity.customer_id = customer.id
        identity.customer_link_source = "PHONE_MATCH"
        identity.customer_linked_at = utc_now()


async def _open_session(
    db: AsyncSession, identity: ChatIdentity, at: datetime
) -> ChatOrderDraft | None:
    mine: Any = ChatOrderDraft.identity_id == identity.id
    if identity.provider == DEVICE:
        # A device conversation merged into another sender's draft keeps
        # adding to it (duplicate merge, V4 WP1.3). Meta drafts never move.
        held = sa.select(ChatMessage.linked_draft_id).where(
            ChatMessage.identity_id == identity.id, ChatMessage.linked_draft_id.is_not(None)
        )
        mine = sa.or_(mine, ChatOrderDraft.id.in_(held))
    drafts = (
        await db.scalars(
            sa.select(ChatOrderDraft)
            .where(mine, ChatOrderDraft.status.in_(OPEN_STATES))
            .order_by(ChatOrderDraft.last_message_at.desc())
            .with_for_update()
        )
    ).all()
    for draft in drafts:
        if draft.first_message_at - SESSION_WINDOW <= at <= draft.last_message_at + SESSION_WINDOW:
            return draft
    return None


async def _follow_up(
    db: AsyncSession, identity: ChatIdentity, message: ChatMessage
) -> ChatAttention | None:
    """A known customer writing about an order they already placed."""
    intent = detect_intent(message.text)
    if intent is None or identity.customer_id is None:
        return None
    since = utc_now() - FOLLOW_UP_WINDOW
    open_orders = (
        await db.scalars(
            sa.select(Order.id)
            .where(
                Order.customer_id == identity.customer_id,
                Order.deleted_at.is_(None),
                Order.created_at >= since,
                Order.status.not_in([str(OrderStatus.COMPLETED), str(OrderStatus.CANCELLED)]),
            )
            .order_by(Order.created_at.desc())
            .limit(2)
        )
    ).all()
    if not open_orders:
        return None
    attention = ChatAttention(
        connection_id=message.connection_id,
        identity_id=identity.id,
        provider=message.provider,
        intent=intent,
        status="OPEN",
        customer_id=identity.customer_id,
        # Linked only when it can only be one order; otherwise the seller sees
        # the customer and picks.
        order_id=open_orders[0] if len(open_orders) == 1 else None,
        excerpt=(message.text or "")[:300] or None,
        message_at=message.provider_timestamp,
    )
    db.add(attention)
    await db.flush()
    return attention


async def transcript(db: AsyncSession, draft: ChatOrderDraft) -> str:
    texts = (
        await db.scalars(
            sa.select(ChatMessage.text)
            .where(
                ChatMessage.linked_draft_id == draft.id,
                ChatMessage.message_type == "TEXT",
                ChatMessage.text.is_not(None),
            )
            .order_by(ChatMessage.provider_timestamp, ChatMessage.received_at)
            .limit(MAX_SESSION_MESSAGES)
        )
    ).all()
    joined = "\n".join(text for text in texts if text)
    return joined[:MAX_TRANSCRIPT_CHARS]


async def rebuild(db: AsyncSession, draft: ChatOrderDraft, identity: ChatIdentity) -> None:
    """Re-read the whole session and refresh every machine-filled field."""
    text = await transcript(db, draft)
    parsed = _parser.parse(text)
    warnings: set[str] = set()

    sender_phone: str | None = None
    if identity.provider == "WHATSAPP":
        number = try_normalize_bd_phone("+" + capture.external_id(identity))
        sender_phone = number.e164 if number else None
    phones = list(parsed.phones)
    selected = parsed.selected_phone
    if not phones and sender_phone:
        # The number the customer is writing from is a real delivery number.
        phones, selected = [sender_phone], sender_phone

    draft.customer_name = parsed.customer_name or (
        identity.display_name if identity.provider == "WHATSAPP" else None
    )
    draft.phones = phones
    draft.selected_phone = selected
    draft.address = parsed.address
    draft.notes = parsed.notes
    draft.items = await matching.match_items(db, parsed.items)

    if parsed.cod_amount_paisa is not None:
        draft.cod_amount_paisa, draft.cod_source = parsed.cod_amount_paisa, "PARSED"
    else:
        draft.cod_amount_paisa, draft.cod_source = _catalogue_total(draft.items), None
        if draft.cod_amount_paisa is not None:
            draft.cod_source = "CATALOG"

    uncertain = {
        field
        for field, key in (
            ("name", "name"),
            ("phone", "phone"),
            ("address", "address"),
            ("items", "items"),
            ("amount", "amount"),
        )
        if parsed.confidence.get(key, 0.0) < CONFIDENCE_CONFIRM_THRESHOLD
        and _present(parsed, field)
    }
    if parsed.needs_phone_selection:
        uncertain.add("phone")
        warnings.add("MULTIPLE_PHONES")
    if draft.customer_name and not parsed.customer_name:
        uncertain.add("name")
    await _apply_delivery_rules(db, draft, parsed, uncertain, warnings)
    if draft.cod_source in ("CATALOG", "CHAT_MISMATCH"):
        uncertain.add("amount")
    for item in draft.items:
        status = (item.get("match") or {}).get("status")
        if status == "AMBIGUOUS":
            warnings.add("PRODUCT_AMBIGUOUS")
            uncertain.add("product")
        elif status == "NOT_FOUND":
            warnings.add("PRODUCT_NOT_FOUND")
            uncertain.add("product")
        if (item.get("match") or {}).get("variant_status") == "AMBIGUOUS":
            warnings.add("VARIANT_AMBIGUOUS")
            uncertain.add("variant")
    if draft.attachment_count:
        warnings.add("ATTACHMENT")
    if draft.message_count >= MAX_SESSION_MESSAGES:
        warnings.add("SESSION_FULL")
    if draft.duplicate_of_order_id is not None:
        warnings.add("DUPLICATE_ORDER")

    await _match_customer(db, draft, identity, phones, sender_phone)
    if draft.customer_match == "AMBIGUOUS":
        warnings.add("CUSTOMER_AMBIGUOUS")
    if draft.customer_id is not None:
        customer = await db.get(Customer, draft.customer_id)
        if customer is not None and customer.is_blocked:
            warnings.add("CUSTOMER_BLOCKED")

    missing = [
        field
        for field, present in (
            ("phone", bool(phones)),
            ("address", bool(parsed.address)),
            ("items", bool(draft.items)),
        )
        if not present
    ]
    draft.missing_fields = missing
    draft.uncertain_fields = sorted(uncertain)
    draft.warnings = sorted(warnings)

    before = draft.status
    if before == READY_FOR_REVIEW:
        after = READY_FOR_REVIEW  # more messages never take a ready draft back
    elif not missing:
        after = READY_FOR_REVIEW
    elif parsed.is_empty and not draft.items:
        after = COLLECTING
    else:
        after = NEEDS_INFO
    draft.status = after
    if after == READY_FOR_REVIEW and draft.ready_at is None:
        draft.ready_at = utc_now()
    await db.flush()


def _present(parsed: ParsedOrder, field: str) -> bool:
    return {
        "name": parsed.customer_name is not None,
        "phone": bool(parsed.phones),
        "address": parsed.address is not None,
        "items": bool(parsed.items),
        "amount": parsed.cod_amount_paisa is not None,
    }[field]


async def _apply_delivery_rules(
    db: AsyncSession,
    draft: ChatOrderDraft,
    parsed: ParsedOrder,
    uncertain: set[str],
    warnings: set[str],
) -> None:
    """Zone-aware COD (V4 WP0.4), only with the flag on and rules saved.

    A catalogue COD gets the zone charge added. A COD stated in chat is never
    replaced: when it is further from the calculation than the zone charge, it
    is marked ``CHAT_MISMATCH`` for the seller to resolve.
    """
    if not await FeatureFlagService(db).is_enabled(
        FlagKey.DELIVERY_RULES_ENABLED, tenant_id=draft.tenant_id
    ):
        return
    rules = await DeliveryRulesService(db).get(draft.tenant_id)
    if not rules.configured:
        return
    zone = resolve_zone(parsed.address, parsed.district)
    if zone is None:
        if parsed.address:
            warnings.add("ZONE_UNKNOWN")
        return
    charge = rules.zone_charge(zone) or 0
    lines = []
    for item in draft.items:
        price = (item.get("match") or {}).get("unit_price_paisa")
        if not isinstance(price, int):
            return
        lines.append(CodLine(unit_price_paisa=price, quantity=int(item.get("quantity") or 1)))
    calculated = calculate_cod(lines, zone_charge_paisa=charge)
    if calculated is None:
        return
    if draft.cod_source == "PARSED" and draft.cod_amount_paisa is not None:
        if is_chat_mismatch(draft.cod_amount_paisa, calculated, zone_charge_paisa=charge):
            draft.cod_source = "CHAT_MISMATCH"
            warnings.add("COD_MISMATCH")
        return
    draft.cod_amount_paisa, draft.cod_source = calculated, "CATALOG"


def _catalogue_total(items: list[dict[str, Any]]) -> int | None:
    total = 0
    for item in items:
        price = (item.get("match") or {}).get("unit_price_paisa")
        if not isinstance(price, int) or price <= 0:
            return None
        total += price * int(item.get("quantity") or 1)
    return total or None


async def _match_customer(
    db: AsyncSession,
    draft: ChatOrderDraft,
    identity: ChatIdentity,
    phones: list[str],
    sender_phone: str | None,
) -> None:
    """Which existing customer this is, without ever merging on a name."""
    found: dict[uuid.UUID, str] = {}
    customers = _customers(db)
    for phone in phones[:5]:
        customer = await customers.find_by_phone(phone)
        if customer is not None:
            found.setdefault(customer.id, phone)
    linked = identity.customer_id
    candidates = set(found) | ({linked} if linked else set())
    if not candidates:
        draft.customer_match, draft.customer_id, draft.customer_candidates = "NONE", None, []
        return
    if len(candidates) > 1:
        draft.customer_match, draft.customer_id = "AMBIGUOUS", None
        draft.customer_candidates = sorted(str(c) for c in candidates)
        return
    (only,) = candidates
    draft.customer_id = only
    draft.customer_candidates = [str(only)]
    sender_owned = sender_phone is not None and found.get(only) == sender_phone
    draft.customer_match = "MATCHED" if only == linked or sender_owned else "SUGGESTED"


async def _recent_order(db: AsyncSession, phone: str) -> uuid.UUID | None:
    customer = await _customers(db).find_by_phone(phone)
    if customer is None:
        return None
    return await db.scalar(
        sa.select(Order.id)
        .where(
            Order.customer_id == customer.id,
            Order.deleted_at.is_(None),
            Order.created_at >= utc_now() - DUPLICATE_ORDER_WINDOW,
            Order.status != str(OrderStatus.CANCELLED),
        )
        .order_by(Order.created_at.desc())
        .limit(1)
    )


async def _attach_duplicate(db: AsyncSession, draft: ChatOrderDraft) -> ChatOrderDraft:
    """Duplicate merge (V4 WP1.3) for a draft whose phone just appeared.

    *   an open draft with the same phone: this one is merged into it (its
        messages move over) and the merged draft is returned;
    *   else an order for that phone made in the last 24 hours: the draft is
        linked to it (``DUPLICATE_ORDER``) and is never announced as new.

    A draft the seller was already told about is not merged away. Nothing is
    closed or confirmed here.
    """
    phone = draft.selected_phone
    if phone is None:
        return draft
    if draft.notified_at is None:
        other = await db.scalar(
            sa.select(ChatOrderDraft)
            .where(
                ChatOrderDraft.id != draft.id,
                ChatOrderDraft.status.in_(OPEN_STATES),
                ChatOrderDraft.selected_phone == phone,
            )
            .order_by(ChatOrderDraft.first_message_at, ChatOrderDraft.id)
            .limit(1)
            .with_for_update()
        )
        if other is not None:
            await _merge(db, draft, other)
            return other
    draft.duplicate_of_order_id = await _recent_order(db, phone)
    warnings = set(draft.warnings) - {"DUPLICATE_ORDER"}
    if draft.duplicate_of_order_id is not None:
        warnings.add("DUPLICATE_ORDER")
    draft.warnings = sorted(warnings)
    await db.flush()
    return draft


async def _merge(db: AsyncSession, draft: ChatOrderDraft, target: ChatOrderDraft) -> None:
    rows = (
        await db.scalars(sa.select(ChatMessage).where(ChatMessage.linked_draft_id == draft.id))
    ).all()
    for row in rows:
        row.linked_draft_id = target.id
    known = set(target.source_message_ids)
    target.source_message_ids = [
        *target.source_message_ids,
        *(mid for mid in draft.source_message_ids if mid not in known),
    ]
    target.message_count += draft.message_count
    target.attachment_count += draft.attachment_count
    target.first_message_at = min(target.first_message_at, draft.first_message_at)
    target.last_message_at = max(target.last_message_at, draft.last_message_at)
    await db.delete(draft)
    await db.flush()
    owner = await db.get(ChatIdentity, target.identity_id)
    if owner is not None:
        await rebuild(db, target, owner)


async def _announce_if_ready(db: AsyncSession, draft: ChatOrderDraft) -> None:
    """The first time a draft is ready: one push and one ``intake.draft_ready``.

    A draft about an order made in the last 24 hours is never announced.
    """
    if draft.status != READY_FOR_REVIEW or draft.notified_at is not None:
        return
    if draft.duplicate_of_order_id is not None:
        return
    from app.common.outbox import OutboxTopic, enqueue

    await _announce(db, draft)
    await enqueue(
        db,
        OutboxTopic.INTAKE_DRAFT_READY,
        {
            "draft_id": str(draft.id),
            "connection_id": str(draft.connection_id),
            "provider": draft.provider,
            "capture_source": draft.capture_source,
        },
        tenant_id=draft.tenant_id,
        dedupe_key=f"intake.draft_ready:{draft.id}",
    )


async def _announce(db: AsyncSession, draft: ChatOrderDraft) -> None:
    """One push per draft: the first time it is ready to review."""
    from app.notifications.models import NotificationCategory, NotificationKind, Severity
    from app.notifications.service import NotificationService
    from app.notifications.templates import render
    from app.tenants.roles import Permission

    params = {"provider": draft.provider}
    title, body = render(NotificationKind.CHAT_ORDER_READY, params, "en") or ("", "")
    await NotificationService(db).notify(
        kind=NotificationKind.CHAT_ORDER_READY,
        severity=Severity.ACTION,
        title=title,
        body=body,
        dedupe_key=f"chat-draft:{draft.id}",
        entity_type="chat_order",
        entity_id=draft.id,
        category=NotificationCategory.ORDERS,
        audience=Permission.ORDER_WRITE,
        payload={"params": params, "target": {"route": "inbox"}},
    )
    draft.notified_at = utc_now()


# --------------------------------------------------------- seller actions ---


async def get_draft(db: AsyncSession, draft_id: uuid.UUID, *, lock: bool = False) -> ChatOrderDraft:
    query = sa.select(ChatOrderDraft).where(ChatOrderDraft.id == draft_id)
    draft = await db.scalar(query.with_for_update() if lock else query)
    if draft is None:
        raise NotFoundError()
    return draft


async def ignore(db: AsyncSession, draft: ChatOrderDraft, actor_id: uuid.UUID) -> None:
    if draft.status == IGNORED:
        return
    if draft.status in FINAL_STATES:
        raise ConflictError("This chat order is already closed", details={"code": "DRAFT_CLOSED"})
    draft.status, draft.closed_at, draft.closed_by = IGNORED, utc_now(), actor_id
    await record_audit(
        db,
        AuditAction.CHAT_ORDER_IGNORED,
        entity_type="chat_order",
        entity_id=draft.id,
        context={"provider": draft.provider},
    )
    await db.flush()


def order_channel(draft: ChatOrderDraft, identity: ChatIdentity | None) -> OrderChannel:
    """The order's channel: the Meta provider, or the app a device read it in.

    A shared or pasted message, or an app that carries more than one kind of
    chat (Business Suite), is a parsed message: ``PASTE_PARSE``.
    """
    if draft.provider == "WHATSAPP":
        return OrderChannel.WHATSAPP
    if draft.provider != DEVICE:
        return OrderChannel.MESSENGER
    app = identity.account_ref if identity is not None else ""
    if app in _WHATSAPP_APPS:
        return OrderChannel.WHATSAPP
    if app in _MESSENGER_APPS:
        return OrderChannel.MESSENGER
    return OrderChannel.PASTE_PARSE


@dataclass
class Confirmed:
    order: Order
    duplicates: Any
    replayed: bool


def _check_items(draft: ChatOrderDraft, items: list[Any]) -> None:
    """An item the parser could not pin to one product needs the seller's pick.

    Kept as the customer wrote it, with no product chosen, it would be saved as
    a free-text line the seller never decided on.
    """
    unsure = {
        " ".join(str(line.get("name") or "").casefold().split())
        for line in draft.items
        if (line.get("match") or {}).get("status") == "AMBIGUOUS"
    }
    for index, item in enumerate(items):
        name = " ".join((item.name or "").casefold().split())
        if item.product_id is None and name in unsure:
            raise ValidationError(
                "Choose which product the customer meant",
                details={"code": "PRODUCT_AMBIGUOUS", "item": index},
            )


async def confirm(
    db: AsyncSession,
    draft: ChatOrderDraft,
    payload: Any,
    orders: Any,
    actor_id: uuid.UUID,
) -> Confirmed:
    """Create the order through the canonical order service, exactly once.

    ``draft`` must be locked by the caller. A confirmed draft returns its
    order; the draft id is the order's ``client_id``, so even two requests
    racing past the lock end at the same order.
    """
    from app.orders.service import OrderDraft, OrderItemDraft

    if draft.status == CONFIRMED and draft.confirmed_order_id is not None:
        order = await db.get(Order, draft.confirmed_order_id)
        if order is not None:
            return Confirmed(order=order, duplicates=None, replayed=True)
    if draft.status in (IGNORED, EXPIRED):
        raise ConflictError(
            "This chat order was closed. Open the chat to start a new one.",
            details={"code": "DRAFT_CLOSED"},
        )
    _check_items(draft, payload.items)
    owner = await db.get(ChatIdentity, draft.identity_id)
    order, duplicates = await orders.create(
        OrderDraft(
            phone=payload.phone,
            items=[
                OrderItemDraft(
                    product_id=item.product_id,
                    name=item.name,
                    quantity=item.quantity,
                    unit_price_paisa=item.unit_price_paisa,
                    discount_paisa=item.discount_paisa,
                    variant_label=item.variant_label,
                    note=item.note,
                    variant_id=item.variant_id,
                )
                for item in payload.items
            ],
            customer_name=payload.customer_name,
            address=payload.address,
            district=payload.district,
            area=payload.area,
            cod_amount_paisa=payload.cod_amount_paisa,
            discount_paisa=payload.discount_paisa,
            delivery_fee_paisa=payload.delivery_fee_paisa,
            note=payload.note,
            # The chat itself is not copied onto the order: raw messages have
            # their own, shorter retention.
            source_text=None,
            channel=order_channel(draft, owner),
            client_id=draft.id,
        )
    )
    draft.status, draft.confirmed_order_id = CONFIRMED, order.id
    draft.closed_at, draft.closed_by = utc_now(), actor_id
    draft.customer_id = order.customer_id
    identity = await db.scalar(
        sa.select(ChatIdentity).where(ChatIdentity.id == draft.identity_id).with_for_update()
    )
    if identity is not None and order.customer_id is not None:
        # Remembered from now on: the seller confirmed who this sender is.
        identity.customer_id = order.customer_id
        identity.customer_link_source = "CONFIRMED_ORDER"
        identity.customer_linked_at = utc_now()
    chat: dict[str, Any] = {"provider": draft.provider, "draft_id": str(draft.id)}
    if draft.capture_source != META_WEBHOOK:
        chat["capture_source"] = draft.capture_source
    order.metadata_json = {**(order.metadata_json or {}), "chat": chat}
    await record_audit(
        db,
        AuditAction.CHAT_ORDER_CONFIRMED,
        entity_type="chat_order",
        entity_id=draft.id,
        context={"provider": draft.provider, "order_id": str(order.id)},
    )
    await db.flush()
    return Confirmed(order=order, duplicates=duplicates, replayed=False)


async def resolve_attention(
    db: AsyncSession, attention_id: uuid.UUID, actor_id: uuid.UUID
) -> ChatAttention:
    row = await db.scalar(
        sa.select(ChatAttention).where(ChatAttention.id == attention_id).with_for_update()
    )
    if row is None:
        raise NotFoundError()
    if row.status != "DONE":
        row.status, row.resolved_at, row.resolved_by = "DONE", utc_now(), actor_id
    await db.flush()
    return row


# --------------------------------------------------------------- views ---


def draft_view(
    draft: ChatOrderDraft,
    identity: ChatIdentity | None,
    customer: Customer | None,
    connection: IntegrationConnection | None,
) -> dict[str, Any]:
    return {
        "id": draft.id,
        "provider": draft.provider,
        "capture_source": draft.capture_source,
        # The app a device read it in (None for Meta).
        "source_app": identity.account_ref if identity and draft.provider == DEVICE else None,
        "duplicate_of_order_id": draft.duplicate_of_order_id,
        "status": draft.status,
        "connection_id": draft.connection_id,
        "channel_name": connection.account_name if connection else None,
        "sender": {
            "display_name": identity.display_name if identity else None,
            "phone_masked": identity.phone_masked if identity else None,
            "known": bool(identity and identity.customer_id),
        },
        "customer_match": draft.customer_match,
        "customer": None
        if customer is None
        else {
            "id": customer.id,
            "name": customer.name,
            "phone_masked": customer.phone_masked,
            "flag": customer.flag,
            "order_count": customer.order_count,
            "is_blocked": customer.is_blocked,
        },
        "customer_name": draft.customer_name,
        "phones": draft.phones,
        "selected_phone": draft.selected_phone,
        "address": draft.address,
        "items": draft.items,
        "cod_amount_paisa": draft.cod_amount_paisa,
        "cod_source": draft.cod_source,
        "notes": draft.notes,
        "warnings": draft.warnings,
        "uncertain_fields": draft.uncertain_fields,
        "missing_fields": draft.missing_fields,
        "message_count": draft.message_count,
        "attachment_count": draft.attachment_count,
        "first_message_at": draft.first_message_at,
        "last_message_at": draft.last_message_at,
        "ready_at": draft.ready_at,
        "closed_at": draft.closed_at,
        "confirmed_order_id": draft.confirmed_order_id,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


def attention_view(row: ChatAttention, customer: Customer | None) -> dict[str, Any]:
    return {
        "id": row.id,
        "provider": row.provider,
        "intent": row.intent,
        "status": row.status,
        "order_id": row.order_id,
        "customer": None
        if customer is None
        else {"id": customer.id, "name": customer.name, "phone_masked": customer.phone_masked},
        "excerpt": row.excerpt,
        "message_at": row.message_at,
        "created_at": row.created_at,
    }


# ------------------------------------------------------------ housekeeping ---


async def expire_due(db: AsyncSession, now: datetime | None = None) -> int:
    """Close drafts nobody finished. Runs on a system session, all shops."""
    moment = now or utc_now()
    closed = 0
    for state, after in EXPIRY.items():
        result = await db.execute(
            sa.update(ChatOrderDraft)
            .where(ChatOrderDraft.status == state, ChatOrderDraft.last_message_at < moment - after)
            .values(status=EXPIRED, closed_at=moment)
            .execution_options(synchronize_session=False)
        )
        closed += int(getattr(result, "rowcount", 0) or 0)
    return closed


async def purge_expired_data(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Retention: raw messages and closed drafts go after their window."""
    moment = now or utc_now()
    messages = await db.execute(
        sa.delete(ChatMessage)
        .where(ChatMessage.received_at < moment - MESSAGE_RETENTION)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        sa.update(ChatAttention)
        .where(ChatAttention.message_at < moment - MESSAGE_RETENTION)
        .values(excerpt=None)
        .execution_options(synchronize_session=False)
    )
    attention = await db.execute(
        sa.delete(ChatAttention)
        .where(
            ChatAttention.status == "DONE",
            ChatAttention.resolved_at < moment - CLOSED_DRAFT_RETENTION,
        )
        .execution_options(synchronize_session=False)
    )
    drafts = await db.execute(
        sa.delete(ChatOrderDraft)
        .where(
            ChatOrderDraft.status.in_(FINAL_STATES),
            ChatOrderDraft.closed_at < moment - CLOSED_DRAFT_RETENTION,
        )
        .execution_options(synchronize_session=False)
    )
    # V4 WP1.3: a device "sender" is a conversation title the phone showed (or
    # a one-off share). Once nothing of it is left, it goes too.
    cutoff = moment - MESSAGE_RETENTION
    identities = await db.execute(
        sa.delete(ChatIdentity)
        .where(
            ChatIdentity.provider == DEVICE,
            ChatIdentity.created_at < cutoff,
            sa.or_(ChatIdentity.last_message_at.is_(None), ChatIdentity.last_message_at < cutoff),
            ~sa.exists().where(ChatMessage.identity_id == ChatIdentity.id),
            ~sa.exists().where(ChatOrderDraft.identity_id == ChatIdentity.id),
            ~sa.exists().where(ChatAttention.identity_id == ChatIdentity.id),
        )
        .execution_options(synchronize_session=False)
    )
    return {
        "messages": int(getattr(messages, "rowcount", 0) or 0),
        "attention": int(getattr(attention, "rowcount", 0) or 0),
        "drafts": int(getattr(drafts, "rowcount", 0) or 0),
        "identities": int(getattr(identities, "rowcount", 0) or 0),
    }


async def erase_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Account deletion: every message, draft, follow-up and sender of a shop."""
    removed = 0
    for model in (ChatMessage, ChatAttention, ChatOrderDraft, ChatIdentity):
        result = await db.execute(
            sa.delete(model)
            .where(model.tenant_id == tenant_id)
            .execution_options(synchronize_session=False)
        )
        removed += int(getattr(result, "rowcount", 0) or 0)
    return removed
