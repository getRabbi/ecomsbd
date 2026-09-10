"""Phase F scheduled work.

Master spec section 42's job list, extended with what Phase F added:

*   :func:`reconcile_billing` — section 90's missed-notification safety net.
    Asks the provider what a subscription's real state is, for subscriptions we
    have not heard about recently.
*   :func:`expire_subscriptions` — closes out paid periods whose grace window
    has also ended. Deliberately *second* to the provider: local expiry alone
    never revokes a subscription the provider still calls active.
*   :func:`dispatch_notifications` — turns unread notifications into delivery
    attempts, and retries the ones whose backoff has elapsed.
*   :func:`expire_exports` — drops the content of exports whose links have
    expired, keeping the audit row.
*   :func:`run_account_deletions` — executes deletions whose cooling-off period
    has ended.

Every job runs on a **system session** and is idempotent: a job that runs twice
in the same minute — two workers, a restart, a manual trigger — must not double
anything. Where that is not free, the underlying operation carries the
idempotency (an absolute period end rather than an increment; a unique delivery
row rather than a counter).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from app.billing.providers.registry import build_registry
from app.billing.service import BillingService
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger
from app.db.session import system_session
from app.entitlements.models import Subscription, SubscriptionStatus
from app.exports.service import ExportService
from app.notifications.delivery import DeliveryState, NotificationDelivery, NotificationDispatcher
from app.notifications.models import Notification
from app.notifications.transport import build_push_transport, build_sms_transport
from app.privacy.service import PrivacyService

log = get_logger("app.worker.maintenance")

__all__ = [
    "dispatch_notifications",
    "expire_exports",
    "expire_subscriptions",
    "reconcile_billing",
    "run_account_deletions",
]

#: A subscription we have not confirmed within this window is stale enough to
#: re-ask about. Long enough that a healthy provider notification stream keeps
#: the job idle; short enough that a missed notification is caught within a day.
STALE_SYNC_AFTER = timedelta(hours=18)


def _job_context(job_name: str, tenant_id: uuid.UUID | None = None) -> RequestContext:
    return RequestContext(
        trace_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        actor_type=ActorType.SYSTEM,
        job_name=job_name,
    )


async def reconcile_billing(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Re-check subscriptions the provider has gone quiet about.

    Master spec section 90's *"periodic reconciliation job for missed
    notifications"*. Nothing here revokes access on its own: a provider that
    cannot be reached, or one that has nothing to say — a manual grant, a Play
    token we deliberately did not retain — leaves the subscription untouched
    and its ``last_synced_at`` stale, which is itself the signal.
    """
    settings = get_settings()
    token = set_context(_job_context("reconcile_billing"))
    counts = {"checked": 0, "activated": 0, "expired": 0, "unknown": 0, "unavailable": 0}

    try:
        async with system_session("worker: billing reconciliation") as session:
            service = BillingService(session, settings=settings, registry=build_registry(settings))
            cutoff = utc_now() - STALE_SYNC_AFTER
            rows = (
                (
                    await session.execute(
                        sa.select(Subscription)
                        .where(
                            Subscription.status.in_(
                                [
                                    str(SubscriptionStatus.ACTIVE),
                                    str(SubscriptionStatus.GRACE),
                                    str(SubscriptionStatus.PAST_DUE),
                                    str(SubscriptionStatus.CANCEL_AT_PERIOD_END),
                                ]
                            ),
                            sa.or_(
                                Subscription.last_synced_at.is_(None),
                                Subscription.last_synced_at <= cutoff,
                            ),
                        )
                        .limit(200)
                    )
                )
                .scalars()
                .all()
            )

            for subscription in rows:
                set_context(
                    replace(_job_context("reconcile_billing"), tenant_id=subscription.tenant_id)
                )
                outcome = await service.reconcile_subscription(subscription)
                counts["checked"] += 1
                if outcome in counts:
                    counts[outcome] += 1
    finally:
        clear_context(token)

    log.info("billing reconciliation finished", extra=counts)
    return counts


async def expire_subscriptions(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Close out subscriptions whose paid period and grace have both ended.

    The safety net, not the primary path: a provider notification is faster and
    more authoritative, and this only acts on periods that have genuinely
    lapsed with no grace window left open.
    """
    settings = get_settings()
    token = set_context(_job_context("expire_subscriptions"))
    try:
        async with system_session("worker: subscription expiry") as session:
            service = BillingService(session, settings=settings, registry=build_registry(settings))
            expired = await service.expire_lapsed()
    finally:
        clear_context(token)

    log.info("subscription expiry finished", extra={"expired": expired})
    return {"expired": expired}


async def dispatch_notifications(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Deliver unread notifications, and retry the ones that are due.

    No transport is configured in this build, so every attempt records
    ``NOT_CONFIGURED`` and nothing is sent. That is the honest state: the
    notification centre already holds everything, and section 94 makes push a
    second copy rather than the product.
    """
    settings = get_settings()
    token = set_context(_job_context("dispatch_notifications"))
    counts = {"dispatched": 0, "retried": 0}

    try:
        async with system_session("worker: notification dispatch") as session:
            dispatcher = NotificationDispatcher(
                session,
                settings=settings,
                push=build_push_transport(settings),
                sms=build_sms_transport(settings),
            )

            # Only notifications with no delivery row at all. A dispatcher that
            # re-read every unread notification would try the same one every
            # minute for as long as the seller left it unread.
            pending = (
                (
                    await session.execute(
                        sa.select(Notification)
                        .where(
                            Notification.read_at.is_(None),
                            Notification.created_at >= utc_now() - timedelta(days=2),
                            sa.not_(
                                sa.exists().where(
                                    NotificationDelivery.notification_id == Notification.id
                                )
                            ),
                        )
                        .order_by(Notification.created_at)
                        .limit(100)
                    )
                )
                .scalars()
                .all()
            )

            for notification in pending:
                set_context(
                    replace(
                        _job_context("dispatch_notifications"),
                        tenant_id=notification.tenant_id,
                    )
                )
                await dispatcher.dispatch(notification)
                counts["dispatched"] += 1

            set_context(_job_context("dispatch_notifications"))
            counts["retried"] = await dispatcher.retry_due()
    finally:
        clear_context(token)

    log.info("notification dispatch finished", extra=counts)
    return counts


async def expire_exports(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Drop the content of exports whose download links have expired.

    The row survives with its row count and requester. Section 99 wants the
    link time-limited; the *record* of who exported what is what makes a later
    privacy question answerable.
    """
    settings = get_settings()
    token = set_context(_job_context("expire_exports"))
    try:
        from app.api.deps import get_hasher

        async with system_session("worker: export expiry") as session:
            service = ExportService(session, settings=settings, hasher=get_hasher(settings))
            expired = await service.expire_stale()
    finally:
        clear_context(token)

    log.info("export expiry finished", extra={"expired": expired})
    return {"expired": expired}


async def run_account_deletions(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Execute deletions whose cooling-off period has ended (section 100)."""
    token = set_context(_job_context("run_account_deletions"))
    try:
        async with system_session("worker: account deletion") as session:
            executed = await PrivacyService(session).run_due()
    finally:
        clear_context(token)

    if executed:
        log.warning("account deletions executed", extra={"count": executed})
    return {"executed": executed}


def delivery_states() -> list[str]:
    """Exposed for the ops console and tests."""
    return [str(state) for state in DeliveryState]
