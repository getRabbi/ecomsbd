"""Marketing campaigns and flows on top of the V2 messaging engine.

A campaign never sends anything itself. It snapshots an audience from the CRM
into recipient rows, then turns due recipients into ordinary ``Message`` rows
(purpose MARKETING) that the existing delivery job sends, retries and records.
Every recipient is checked twice for marketing consent, opt-out and bounces:
when it is queued and again at the moment of sending.

Anti-spam rules are fixed, not settings: marketing is never queued during
Bangladeshi night hours, one customer receives at most one marketing message
per frequency window across all campaigns (a queued message counts even if it
is later suppressed, so two campaigns can never race past the cap), and a
campaign reaches at most ``MAX_RECIPIENTS`` people.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.customer_segments import INACTIVE_DAYS, Segment
from app.analytics.rto import ParcelOutcome, statuses_for
from app.api.deps import get_hasher, get_vault
from app.common.idempotency import request_hash
from app.consignments.models import Consignment
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.context import require_tenant_id
from app.core.errors import ConflictError
from app.customers.crm import CrmService
from app.customers.models import Customer, CustomerFlag
from app.customers.service import CustomerService
from app.messaging import service
from app.messaging.models import (
    MARKETING,
    Campaign,
    CampaignRecipient,
    ConsentEvent,
    Conversation,
    Message,
)
from app.messaging.providers import reports as provider_reports
from app.orders.models import Order, OrderStatus

MAX_RECIPIENTS = 5000
#: Flows enrol at most this many new people a day, so a first activation on a
#: large shop drains over days instead of in one burst.
FLOW_DAILY_LIMIT = 500
QUIET_START, QUIET_END = 21, 9
FLOW_HOUR = 10
#: (default days, minimum, maximum). INACTIVE uses the CRM definition as is.
FLOWS: dict[str, tuple[int, int, int]] = {
    "WIN_BACK": (60, 30, 365),
    "REPEAT_NUDGE": (21, 7, 180),
    "INACTIVE": (INACTIVE_DAYS, INACTIVE_DAYS, INACTIVE_DAYS),
}
ONE_OFF_FINAL = {"COMPLETED", "CANCELLED"}
EDITABLE = {"DRAFT", "SCHEDULED", "PAUSED"}
SKIP_REASONS = (
    "NO_CONTACT",
    "NO_MARKETING_CONSENT",
    "OPTED_OUT",
    "UNDELIVERABLE",
    "FREQUENCY_CAP",
    "CUSTOMER_BLOCKED",
    "CANCELLED",
)


def zone() -> ZoneInfo:
    return ZoneInfo(get_settings().default_timezone)


def quiet(now: datetime) -> bool:
    hour = now.astimezone(zone()).hour
    return hour >= QUIET_START or hour < QUIET_END


def next_morning(now: datetime) -> datetime:
    local = now.astimezone(zone())
    morning = local.replace(hour=QUIET_END, minute=0, second=0, microsecond=0)
    if local.hour >= QUIET_END:
        morning += timedelta(days=1)
    return morning


# -------------------------------------------------------------- audience ---


def crm_filters(audience: dict[str, Any], now: datetime) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if audience.get("segment"):
        filters["segment"] = Segment(audience["segment"])
    if audience.get("tag_id"):
        filters["tag_id"] = uuid.UUID(str(audience["tag_id"]))
    for key in ("min_orders", "max_orders"):
        if audience.get(key) is not None:
            filters[key] = int(audience[key])
    if audience.get("last_order_before_days") is not None:
        filters["last_until"] = now - timedelta(days=int(audience["last_order_before_days"]))
    if audience.get("last_order_within_days") is not None:
        filters["last_from"] = now - timedelta(days=int(audience["last_order_within_days"]))
    filters["money_allowed"] = bool(audience.get("money_allowed"))
    return filters


def _crm(db: AsyncSession) -> CrmService:
    return CrmService(db, CustomerService(db, hasher=get_hasher(), vault=get_vault()))


async def flow_candidates(
    db: AsyncSession, flow: str, days: int, now: datetime, limit: int
) -> list[tuple[uuid.UUID, str]]:
    """(customer, cycle key) pairs. The key is the customer's latest order, so a
    customer re-enters a flow only after ordering again and lapsing again."""
    tenant = require_tenant_id()
    ranked = (
        sa.select(
            Order.customer_id.label("customer_id"),
            Order.id.label("order_id"),
            Order.created_at.label("created_at"),
            sa.func.row_number()
            .over(
                partition_by=Order.customer_id,
                order_by=(Order.created_at.desc(), Order.id.desc()),
            )
            .label("rank"),
        )
        .where(
            Order.tenant_id == tenant, Order.customer_id.is_not(None), Order.deleted_at.is_(None)
        )
        .subquery()
    )
    delivered = statuses_for(ParcelOutcome.DELIVERED, ParcelOutcome.PARTIAL)
    query = (
        sa.select(ranked.c.customer_id, ranked.c.order_id)
        .join(Customer, Customer.id == ranked.c.customer_id)
        .where(
            ranked.c.rank == 1,
            Customer.tenant_id == tenant,
            Customer.deleted_at.is_(None),
            Customer.flag != CustomerFlag.BLOCKED,
        )
    )
    cutoff = now - timedelta(days=days)
    if flow == "REPEAT_NUDGE":
        # The latest order was delivered and is `days` old (a one-week window,
        # so a daily run that was missed still catches it).
        query = query.where(
            ranked.c.created_at <= cutoff,
            ranked.c.created_at > cutoff - timedelta(days=7),
            sa.exists().where(
                Consignment.tenant_id == tenant,
                Consignment.order_id == ranked.c.order_id,
                Consignment.status.in_(delivered),
            ),
        )
    else:
        query = query.where(ranked.c.created_at <= cutoff)
        if flow == "WIN_BACK":
            # Win back people who once received a parcel, not ones who never did.
            query = query.where(
                sa.exists().where(
                    Consignment.tenant_id == tenant,
                    Order.tenant_id == tenant,
                    Consignment.order_id == Order.id,
                    Order.customer_id == ranked.c.customer_id,
                    Consignment.status.in_(delivered),
                )
            )
    rows = (await db.execute(query.order_by(ranked.c.created_at).limit(limit))).all()
    return [(customer_id, str(order_id)) for customer_id, order_id in rows]


async def contacts(
    db: AsyncSession, channel: str, customer_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Conversation]:
    found: dict[uuid.UUID, Conversation] = {}
    for start in range(0, len(customer_ids), 500):
        chunk = customer_ids[start : start + 500]
        rows = await db.scalars(
            sa.select(Conversation).where(
                Conversation.channel == channel, Conversation.customer_id.in_(chunk)
            )
        )
        found.update({row.customer_id: row for row in rows.all()})
    return found


def skip_reason(conversation: Conversation | None) -> str | None:
    if conversation is None:
        return "NO_CONTACT"
    return service.marketing_block(conversation)


async def estimate(
    db: AsyncSession,
    *,
    channel: str,
    audience: dict[str, Any],
    flow: str | None = None,
    flow_days: int | None = None,
) -> dict[str, Any]:
    now = utc_now()
    if flow:
        pairs = await flow_candidates(
            db, flow, flow_days or FLOWS[flow][0], now, MAX_RECIPIENTS + 1
        )
        ids = [customer_id for customer_id, _ in pairs]
        if _has_filters(audience):
            allowed = set(await _crm(db).audience_ids(limit=50_000, **crm_filters(audience, now)))
            ids = [customer_id for customer_id in ids if customer_id in allowed]
    else:
        ids = await _crm(db).audience_ids(limit=MAX_RECIPIENTS + 1, **crm_filters(audience, now))
    found = await contacts(db, channel, ids)
    counts = dict.fromkeys(("NO_CONTACT", "NO_MARKETING_CONSENT", "OPTED_OUT", "UNDELIVERABLE"), 0)
    reachable = 0
    for customer_id in ids:
        reason = skip_reason(found.get(customer_id))
        if reason:
            counts[reason] += 1
        else:
            reachable += 1
    return {
        "matched": min(len(ids), MAX_RECIPIENTS),
        "over_limit": len(ids) > MAX_RECIPIENTS,
        "limit": MAX_RECIPIENTS,
        "reachable": reachable,
        "excluded": counts,
    }


def _has_filters(audience: dict[str, Any]) -> bool:
    return any(
        audience.get(key) is not None and audience.get(key) != ""
        for key in (
            "segment",
            "tag_id",
            "min_orders",
            "max_orders",
            "last_order_before_days",
            "last_order_within_days",
        )
    )


# ------------------------------------------------------------ validation ---


async def ready(db: AsyncSession, campaign: Campaign) -> str | None:
    """Why this campaign cannot send right now, or None."""
    blocker = await service.channel_blocker(db, campaign.channel)
    if blocker:
        return blocker
    try:
        found = await service.template(db, campaign.template_key)
    except Exception:
        return "TEMPLATE_NOT_FOUND"
    problem = service.usable(found, campaign.channel, MARKETING)
    if problem:
        return problem
    if campaign.channel == "WHATSAPP" and not service.template_language(found, campaign.locale):
        return "WHATSAPP_TEMPLATE_LANGUAGE"
    return None


async def require_ready(db: AsyncSession, campaign: Campaign) -> None:
    blocker = await ready(db, campaign)
    if blocker:
        raise ConflictError("This campaign cannot send yet", details={"blocker": blocker})


# ------------------------------------------------------------- lifecycle ---


async def materialize(db: AsyncSession, campaign: Campaign, now: datetime) -> int:
    """Snapshot a one-off audience into recipients. Runs once per campaign."""
    ids = await _crm(db).audience_ids(
        limit=MAX_RECIPIENTS, **crm_filters(campaign.audience or {}, now)
    )
    return await enrol(db, campaign, [(customer_id, "once") for customer_id in ids], now)


async def enrol(
    db: AsyncSession, campaign: Campaign, pairs: list[tuple[uuid.UUID, str]], now: datetime
) -> int:
    if not pairs:
        return 0
    existing: set[tuple[uuid.UUID, str]] = set()
    customers = [customer for customer, _ in pairs]
    for start in range(0, len(customers), 500):
        rows = await db.execute(
            sa.select(CampaignRecipient.customer_id, CampaignRecipient.cycle_key).where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.customer_id.in_(customers[start : start + 500]),
            )
        )
        existing.update((customer, cycle) for customer, cycle in rows.all())
    found = await contacts(db, campaign.channel, [customer for customer, _ in pairs])
    added = 0
    for customer_id, cycle in pairs:
        if (customer_id, cycle) in existing:
            continue
        conversation = found.get(customer_id)
        reason = skip_reason(conversation)
        db.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                customer_id=customer_id,
                conversation_id=conversation.id if conversation else None,
                cycle_key=cycle,
                status="SKIPPED" if reason else "PENDING",
                skip_reason=reason,
                due_at=now,
            )
        )
        added += 1
    campaign.total_recipients += added
    await db.flush()
    return added


async def queue_recipient(
    db: AsyncSession,
    campaign: Campaign,
    recipient: CampaignRecipient,
    found: dict[str, Any],
    shop: str,
    now: datetime,
) -> None:
    """Turn one due recipient into a MARKETING message, or skip it with a reason."""
    customer = await db.get(Customer, recipient.customer_id)
    conversation = (
        await db.scalar(
            sa.select(Conversation)
            .where(Conversation.id == recipient.conversation_id)
            .with_for_update()
        )
        if recipient.conversation_id
        else None
    )
    if customer is None or customer.deleted_at is not None or customer.flag == CustomerFlag.BLOCKED:
        recipient.status, recipient.skip_reason = "SKIPPED", "CUSTOMER_BLOCKED"
        return
    reason = skip_reason(conversation)
    if (
        reason is None
        and conversation is not None
        and conversation.last_marketing_at is not None
        and now - conversation.last_marketing_at < timedelta(hours=campaign.frequency_cap_hours)
    ):
        reason = "FREQUENCY_CAP"
    if reason or conversation is None:
        recipient.status, recipient.skip_reason = "SKIPPED", reason or "NO_CONTACT"
        return
    locale = campaign.locale
    values = {
        "customer_name": customer.name or ("গ্রাহক" if locale == "bn" else "customer"),
        "shop_name": shop,
    }
    subject, body, params = service.compose(found, locale, values, campaign.channel)
    if campaign.channel == "EMAIL":
        body += service.render(
            service.MARKETING_FOOTER[locale],
            {"shop_name": shop, "unsubscribe_url": service.unsubscribe_url(conversation)},
        )
    message = Message(
        conversation_id=conversation.id,
        order_id=None,
        template_key=campaign.template_key,
        locale=locale,
        subject=subject[:200],
        body=body,
        params=params,
        purpose=MARKETING,
        channel=campaign.channel,
        campaign_id=campaign.id,
        consent_version=conversation.consent_version,
        idempotency_key=f"campaign:{recipient.id}",
        request_hash=request_hash([str(campaign.id), str(recipient.id)]),
        next_attempt_at=now,
    )
    db.add(message)
    await db.flush()
    recipient.status, recipient.message_id = "QUEUED", message.id
    conversation.last_marketing_at = now


async def advance(db: AsyncSession, campaign: Campaign, now: datetime) -> int:
    """Queue up to one minute's worth of due recipients."""
    if quiet(now):
        return 0
    blocker = await ready(db, campaign)
    if blocker:
        # The channel or template stopped being usable mid-send: stop, loudly.
        campaign.status, campaign.last_error = "PAUSED", blocker
        return 0
    due = (
        await db.scalars(
            sa.select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status == "PENDING",
                CampaignRecipient.due_at <= now,
            )
            .order_by(CampaignRecipient.due_at, CampaignRecipient.id)
            .limit(campaign.rate_per_minute)
            .with_for_update()
        )
    ).all()
    found = await service.template(db, campaign.template_key)
    shop = await service.shop_name(db)
    for recipient in due:
        await queue_recipient(db, campaign, recipient, found, shop, now)
    await db.flush()
    if campaign.kind == "ONE_OFF":
        pending = await db.scalar(
            sa.select(sa.func.count())
            .select_from(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status == "PENDING",
            )
        )
        # Done only once every message has left our hands, so a pause or a
        # cancel still reaches messages that are queued but not yet sent.
        sending = await db.scalar(
            sa.select(sa.func.count())
            .select_from(Message)
            .where(
                Message.campaign_id == campaign.id,
                Message.status.in_(["QUEUED", "RETRY", "SENDING"]),
            )
        )
        if not pending and not sending:
            campaign.status, campaign.completed_at = "COMPLETED", now
    return len(due)


async def run_flow(db: AsyncSession, campaign: Campaign, now: datetime) -> int:
    """Enrol today's newly-eligible customers, once a day after FLOW_HOUR."""
    local = now.astimezone(zone())
    if local.hour < FLOW_HOUR or campaign.flow is None:
        return 0
    if campaign.last_run_at and campaign.last_run_at.astimezone(zone()).date() >= local.date():
        return 0
    campaign.last_run_at = now
    pairs = await flow_candidates(
        db,
        campaign.flow,
        campaign.flow_days or FLOWS[campaign.flow][0],
        now,
        FLOW_DAILY_LIMIT * 4,
    )
    audience = campaign.audience or {}
    if _has_filters(audience):
        allowed = set(await _crm(db).audience_ids(limit=50_000, **crm_filters(audience, now)))
        pairs = [pair for pair in pairs if pair[0] in allowed]
    return await enrol(db, campaign, pairs[:FLOW_DAILY_LIMIT], now)


async def cancel(db: AsyncSession, campaign: Campaign, now: datetime) -> None:
    campaign.status, campaign.cancelled_at = "CANCELLED", now
    await db.execute(
        sa.update(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status == "PENDING",
        )
        .values(status="SKIPPED", skip_reason="CANCELLED")
    )
    # Queued messages not yet handed to a provider are cancelled by the
    # delivery job when it next picks them up.


# ------------------------------------------------------------- analytics ---


async def analytics(db: AsyncSession, campaign: Campaign, *, money_allowed: bool) -> dict[str, Any]:
    recipients = dict.fromkeys(("PENDING", "QUEUED", "SKIPPED"), 0)
    skipped: dict[str, int] = {}
    for status, reason, count in (
        await db.execute(
            sa.select(CampaignRecipient.status, CampaignRecipient.skip_reason, sa.func.count())
            .where(CampaignRecipient.campaign_id == campaign.id)
            .group_by(CampaignRecipient.status, CampaignRecipient.skip_reason)
        )
    ).all():
        recipients[status] = recipients.get(status, 0) + count
        if status == "SKIPPED" and reason:
            skipped[reason] = skipped.get(reason, 0) + count
    messages: dict[str, int] = {}
    for status, count in (
        await db.execute(
            sa.select(Message.status, sa.func.count())
            .where(Message.campaign_id == campaign.id)
            .group_by(Message.status)
        )
    ).all():
        messages[status] = count
    sent = sum(messages.get(key, 0) for key in ("SENT", "DELIVERED", "READ"))
    delivered = messages.get("DELIVERED", 0) + messages.get("READ", 0)
    errors = [
        {"code": code, "count": count}
        for code, count in (
            await db.execute(
                sa.select(Message.last_error, sa.func.count())
                .where(
                    Message.campaign_id == campaign.id,
                    Message.last_error.is_not(None),
                    Message.status.in_(["FAILED", "UNKNOWN", "SUPPRESSED", "CANCELLED"]),
                )
                .group_by(Message.last_error)
                .order_by(sa.func.count().desc())
                .limit(5)
            )
        ).all()
    ]
    opt_outs = await db.scalar(
        sa.select(sa.func.count())
        .select_from(ConsentEvent)
        .where(ConsentEvent.campaign_id == campaign.id, ConsentEvent.consent.is_(False))
    )
    reports = provider_reports(campaign.channel)
    return {
        "recipients": {"total": campaign.total_recipients, **recipients, "skipped_by": skipped},
        "messages": messages,
        "sent": sent,
        "delivered": delivered if "DELIVERED" in reports else None,
        "read": messages.get("READ", 0) if "READ" in reports else None,
        "failed": messages.get("FAILED", 0),
        "reports": list(reports),
        "errors": errors,
        "opt_outs": opt_outs or 0,
        "orders_after": await orders_after(db, campaign, money_allowed=money_allowed),
    }


async def orders_after(
    db: AsyncSession, campaign: Campaign, *, money_allowed: bool
) -> dict[str, Any]:
    """Orders recipients placed within the window after their message was sent.

    A correlation, labelled as such: it counts what happened after a message,
    not what the message caused.
    """
    window = timedelta(days=campaign.attribution_days)
    first_sent: dict[uuid.UUID, datetime] = {}
    for customer_id, sent_at in (
        await db.execute(
            sa.select(Conversation.customer_id, sa.func.min(Message.sent_at))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.campaign_id == campaign.id, Message.sent_at.is_not(None))
            .group_by(Conversation.customer_id)
        )
    ).all():
        first_sent[customer_id] = sent_at
    buyers: set[uuid.UUID] = set()
    orders = total = 0
    ids = list(first_sent)
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        rows = await db.execute(
            sa.select(
                Order.customer_id,
                Order.created_at,
                Order.subtotal_paisa - Order.discount_paisa + Order.delivery_fee_paisa,
            ).where(
                Order.customer_id.in_(chunk),
                Order.deleted_at.is_(None),
                Order.status != OrderStatus.CANCELLED,
                Order.created_at >= min(first_sent[customer] for customer in chunk),
            )
        )
        for customer_id, created_at, value in rows.all():
            sent_at = first_sent[customer_id]
            if sent_at <= created_at < sent_at + window:
                buyers.add(customer_id)
                orders += 1
                total += int(value or 0)
    return {
        "window_days": campaign.attribution_days,
        "customers": len(buyers),
        "orders": orders,
        "order_value_paisa": total if money_allowed else None,
    }
