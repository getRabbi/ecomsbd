"""Notification delivery: attempts, retries, preferences and rate limits.

Master spec sections 94 and 95, and the Phase F brief's section 33.

The delivery record exists because "we sent a push" is a claim that has to be
checkable. A row here says which transport was used, what it answered, how many
times we tried, and whether the seller had opted out — which is what makes the
failure metrics in section 49 mean something.

Section 95's alert-fatigue rules are enforced here rather than at each alert:

*   ``INFO`` is never pushed. It belongs in the centre, waiting to be read.
*   A seller who turned a category off does not receive it, and the attempt is
    recorded as ``SUPPRESSED`` rather than silently dropped.
*   A per-tenant hourly cap stops a runaway job from sending forty pushes; the
    forty notifications still land in the centre, which is the point of having
    one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.models import Device
from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.base import Base, PrimaryKeyMixin, TenantOwned
from app.db.types import GUID, JSONColumn, TZDateTime
from app.notifications.models import Notification, NotificationKind, Severity
from app.notifications.service import localized
from app.notifications.transport import (
    DeliveryOutcome,
    PushMessage,
    PushTransport,
    SmsMessage,
    SmsTransport,
    count_sms_segments,
)
from app.tenants.models import TenantUser
from app.tenants.roles import Permission, has_permission
from app.users.models import User

__all__ = [
    "Channel",
    "DeliveryState",
    "NotificationDelivery",
    "NotificationDispatcher",
    "NotificationPreference",
    "effective_preference",
]

log = get_logger(__name__)

#: Section 95, made concrete. More pushes than this in an hour is a bug in a
#: job, not a busy shop, and the centre already holds everything.
MAX_PUSHES_PER_TENANT_HOUR = 6

#: Backoff between delivery attempts. Short, because a push that arrives an
#: hour late about money is worse than useless.
_RETRY_BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30))


class Channel(StrEnum):
    PUSH = "PUSH"
    SMS = "SMS"


class DeliveryState(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    #: Temporary failure; a retry is scheduled.
    RETRYING = "RETRYING"
    #: Permanently refused by the provider, or retries exhausted.
    FAILED = "FAILED"
    #: Opted out, severity too low, or rate-limited.
    SUPPRESSED = "SUPPRESSED"
    #: No transport is configured in this deployment.
    NOT_CONFIGURED = "NOT_CONFIGURED"


class NotificationPreference(Base, TenantOwned, PrimaryKeyMixin):
    """What a shop, or one member of it, wants to be interrupted about.

    The row with ``user_id`` NULL is the shop's default; a member's own row,
    created the first time they change a setting, overrides it for them alone.
    Nobody can edit another member's row: the API only ever writes the caller's.

    Defaults are on for everything except routine tracking, which section 95
    singles out as the thing sellers must opt *into* rather than out of: one
    push per parcel event is how a product teaches people to ignore it.
    """

    __tablename__ = "notification_preferences"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "user_id", name="uq_notification_preferences_tenant_id_user_id"
        ),
    )

    #: NULL for the shop-wide default; a member's own row otherwise.
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    push_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    sms_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    #: Notification kinds the seller has switched off, by name. Push only.
    muted_kinds: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    #: :class:`NotificationCategory` names switched off. Neither pushed nor
    #: shown in this member's centre.
    muted_categories: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    #: Section 95's opt-in. Off by default and deliberately so.
    routine_tracking_push: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    #: Local hours during which a push may interrupt. Outside them a
    #: notification still lands in the centre; only the interruption waits.
    quiet_hours_start: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    quiet_hours_end: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    def allows(self, kind: NotificationKind | str, channel: Channel) -> bool:
        if str(kind) in (self.muted_kinds or []):
            return False
        return self.push_enabled if channel is Channel.PUSH else self.sms_enabled

    def push_block(self, notification: Notification) -> str | None:
        """Why this preference refuses a push of ``notification``, if it does."""
        if not self.push_enabled:
            return "push notifications are turned off"
        if str(notification.kind) in (self.muted_kinds or []):
            return f"{notification.kind} is muted"
        if notification.category and notification.category in (self.muted_categories or []):
            return f"the {notification.category} category is muted"
        return None


@dataclass(frozen=True, slots=True)
class Recipient:
    """A member the backend decided should receive one notification."""

    user_id: uuid.UUID
    locale: str
    preference: NotificationPreference


class NotificationDelivery(Base, TenantOwned, PrimaryKeyMixin):
    """One attempt to deliver one notification over one channel."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        # Idempotency: one delivery row per (notification, channel, target).
        # A dispatcher that runs twice finds the row and does not send again.
        sa.UniqueConstraint(
            "notification_id",
            "channel",
            "target_hash",
            name="uq_notification_deliveries_notification_channel_target",
        ),
        sa.Index("ix_notification_deliveries_state_next", "state", "next_attempt_at"),
        sa.Index("ix_notification_deliveries_tenant_created", "tenant_id", "created_at"),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    #: Hash of the push token or phone number. Never the value: a push token is
    #: a credential and a phone number is PII.
    target_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    target_suffix: Mapped[str | None] = mapped_column(sa.String(8), nullable=True)

    state: Mapped[str] = mapped_column(sa.String(20), nullable=False, default=DeliveryState.PENDING)
    provider: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)
    #: Why it was suppressed, in words a support engineer can quote.
    suppression_reason: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    #: Our estimate, and the provider's own count when it reports one. Both are
    #: kept: section 22 says the provider's number wins, and comparing them is
    #: how a mis-estimating template gets found.
    estimated_segments: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    provider_segments: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class NotificationDispatcher:
    """Turns notifications into delivery attempts.

    Never raises into a business path. A notification that could not be pushed
    is still in the centre, and section 94's whole point is that the centre is
    the thing the product depends on.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        push: PushTransport,
        sms: SmsTransport,
    ) -> None:
        self._db = session
        self._settings = settings
        self._push = push
        self._sms = sms

    # ------------------------------------------------------------ preferences ---

    async def preferences(
        self, tenant_id: uuid.UUID, *, user_id: uuid.UUID | None = None
    ) -> NotificationPreference:
        """The row for ``user_id`` (or the shop default), created if missing.

        A member's own row starts as a copy of the shop default, so the first
        change a person makes does not silently reset everything else.
        """
        existing = (
            await self._db.execute(
                sa.select(NotificationPreference).where(
                    NotificationPreference.tenant_id == tenant_id,
                    NotificationPreference.user_id == user_id
                    if user_id is not None
                    else NotificationPreference.user_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        preference = NotificationPreference(tenant_id=tenant_id, user_id=user_id)
        if user_id is not None:
            shop = await self.preferences(tenant_id)
            preference.push_enabled = shop.push_enabled
            preference.sms_enabled = shop.sms_enabled
            preference.muted_kinds = list(shop.muted_kinds or [])
            preference.muted_categories = list(shop.muted_categories or [])
            preference.routine_tracking_push = shop.routine_tracking_push
            preference.quiet_hours_start = shop.quiet_hours_start
            preference.quiet_hours_end = shop.quiet_hours_end
        self._db.add(preference)
        try:
            await self._db.flush()
        except IntegrityError:
            # Two requests created the shop's first preference row at once.
            await self._db.rollback()
            return await self.preferences(tenant_id, user_id=user_id)
        return preference

    # -------------------------------------------------------------- dispatch ---

    async def dispatch(self, notification: Notification) -> list[NotificationDelivery]:
        """Attempt delivery of one notification to every eligible member.

        The in-app row already exists and stays whatever happens here: push is
        a delivery channel, not the source of truth. Recipients are decided by
        the backend — active members whose role carries the notification's
        audience (or the one member it is addressed to) and whose own
        preferences allow it — and each device is pushed at most once.
        """
        tenant_id = notification.tenant_id
        if not Severity(notification.severity).deserves_push:
            return [
                await self._record_suppressed(
                    notification, "INFO notifications live in the centre and never interrupt"
                )
            ]

        recipients = await self._recipients(notification)
        if not recipients:
            return [
                await self._record_suppressed(
                    notification, "no active member of the shop receives this notification"
                )
            ]
        allowed = [r for r in recipients if r.preference.push_block(notification) is None]
        if not allowed:
            reason = recipients[0].preference.push_block(notification) or "suppressed"
            return [await self._record_suppressed(notification, f"every recipient: {reason}")]

        suppression = await self._rate_limited(notification)
        if suppression is not None:
            return [await self._record_suppressed(notification, suppression)]

        locales = {r.user_id: r.locale for r in allowed}
        devices = await self._push_targets(tenant_id, user_ids=list(locales))
        if not devices:
            return [
                await self._record_suppressed(notification, "no device has a push token registered")
            ]

        deliveries: list[NotificationDelivery] = []
        for device in devices:
            delivery = await self._claim(notification, Channel.PUSH, target=device.push_token or "")
            if delivery is None:
                continue  # already attempted; the unique constraint says so
            await self._attempt_push(
                notification, device, delivery, locale=locales.get(device.user_id)
            )
            deliveries.append(delivery)
        return deliveries

    async def _recipients(self, notification: Notification) -> list[Recipient]:
        """Who should get this, decided here and nowhere else.

        One query for the members, one for their preference rows; a member
        without their own row falls back to the shop default.
        """
        tenant_id = notification.tenant_id
        stmt = (
            sa.select(TenantUser.user_id, TenantUser.role, User.locale)
            .join(User, User.id == TenantUser.user_id)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.is_active.is_(True))
        )
        if notification.user_id is not None:
            stmt = stmt.where(TenantUser.user_id == notification.user_id)
        members = list(await self._db.execute(stmt))

        audience = Permission(notification.audience) if notification.audience else None
        members = [
            (user_id, locale)
            for user_id, role, locale in members
            if audience is None or has_permission(role, audience)
        ]
        if not members:
            return []

        rows = (
            (
                await self._db.execute(
                    sa.select(NotificationPreference).where(
                        NotificationPreference.tenant_id == tenant_id,
                        sa.or_(
                            NotificationPreference.user_id.is_(None),
                            NotificationPreference.user_id.in_([uid for uid, _ in members]),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        own = {row.user_id: row for row in rows if row.user_id is not None}
        shop = next((row for row in rows if row.user_id is None), None)
        if shop is None:
            shop = await self.preferences(tenant_id)
        return [
            Recipient(user_id=user_id, locale=locale or "bn", preference=own.get(user_id, shop))
            for user_id, locale in members
        ]

    async def _rate_limited(self, notification: Notification) -> str | None:
        """Section 95's cap: a runaway job must not become forty pushes."""
        recent = int(
            (
                await self._db.execute(
                    sa.select(sa.func.count())
                    .select_from(NotificationDelivery)
                    .where(
                        NotificationDelivery.tenant_id == notification.tenant_id,
                        NotificationDelivery.channel == str(Channel.PUSH),
                        NotificationDelivery.state == str(DeliveryState.SENT),
                        NotificationDelivery.created_at >= utc_now() - timedelta(hours=1),
                    )
                )
            ).scalar_one()
        )
        if recent >= MAX_PUSHES_PER_TENANT_HOUR:
            return (
                f"{recent} pushes already sent this hour; the rest are waiting "
                "in the notification centre"
            )
        return None

    async def _push_targets(
        self, tenant_id: uuid.UUID, *, user_ids: list[uuid.UUID] | None = None
    ) -> list[Device]:
        if user_ids is None:
            user_ids = list(
                (
                    await self._db.execute(
                        sa.select(TenantUser.user_id).where(
                            TenantUser.tenant_id == tenant_id,
                            TenantUser.is_active.is_(True),
                        )
                    )
                ).scalars()
            )
        if not user_ids:
            return []
        return list(
            (
                await self._db.execute(
                    sa.select(Device).where(
                        Device.user_id.in_(user_ids),
                        Device.revoked_at.is_(None),
                        Device.push_token.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    async def _claim(
        self, notification: Notification, channel: Channel, *, target: str
    ) -> NotificationDelivery | None:
        """Create the delivery row, or ``None`` if one already exists.

        The unique constraint is the idempotency mechanism: a dispatcher that
        runs twice for the same notification loses the race on the second row
        and sends nothing.
        """
        from app.billing.models import hash_provider_token

        target_hash = hash_provider_token(target)
        existing = (
            await self._db.execute(
                sa.select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == notification.id,
                    NotificationDelivery.channel == str(channel),
                    NotificationDelivery.target_hash == target_hash,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return None

        delivery = NotificationDelivery(
            tenant_id=notification.tenant_id,
            notification_id=notification.id,
            channel=str(channel),
            target_hash=target_hash,
            target_suffix=target[-4:] if target else None,
            state=str(DeliveryState.PENDING),
        )
        self._db.add(delivery)
        try:
            await self._db.flush()
        except IntegrityError:
            await self._db.rollback()
            return None
        return delivery

    async def _attempt_push(
        self,
        notification: Notification,
        device: Device,
        delivery: NotificationDelivery,
        *,
        locale: str | None = None,
    ) -> None:
        # Worded in the recipient's own language from the stored facts.
        title, body = localized(notification, locale)
        target = (notification.payload or {}).get("target") or {}
        message = PushMessage(
            token=device.push_token or "",
            title=title,
            body=body,
            deep_link=_deep_link(notification),
            data={
                "kind": str(notification.kind),
                "notification_id": str(notification.id),
                **({"category": notification.category} if notification.category else {}),
                **({"route": str(target["route"])} if target.get("route") else {}),
                **({"entity_id": str(notification.entity_id)} if notification.entity_id else {}),
            },
            idempotency_key=f"{notification.id}:{delivery.target_hash}",
        )

        delivery.attempts += 1
        try:
            result = await self._push.send(message)
        except Exception as exc:
            log.warning("push transport raised", extra={"error": type(exc).__name__})
            delivery.state = str(DeliveryState.RETRYING)
            delivery.last_error = f"{type(exc).__name__}: {exc}"[:400]
            delivery.next_attempt_at = self._next_attempt(delivery.attempts)
            return

        delivery.provider = result.provider
        delivery.provider_reference = result.provider_reference

        match result.outcome:
            case DeliveryOutcome.SENT | DeliveryOutcome.DUPLICATE:
                delivery.state = str(DeliveryState.SENT)
                delivery.sent_at = utc_now()
                delivery.next_attempt_at = None
            case DeliveryOutcome.NOT_CONFIGURED:
                delivery.state = str(DeliveryState.NOT_CONFIGURED)
                delivery.last_error = result.detail
                delivery.next_attempt_at = None
            case DeliveryOutcome.REJECTED:
                # A dead token never comes back. Drop it so the shop stops
                # accumulating deliveries that cannot succeed.
                delivery.state = str(DeliveryState.FAILED)
                delivery.last_error = result.detail
                device.push_token = None
                await self._audit_failure(delivery, result.detail)
            case DeliveryOutcome.SUPPRESSED:
                delivery.state = str(DeliveryState.SUPPRESSED)
                delivery.suppression_reason = result.detail
            case _:
                if delivery.attempts >= len(_RETRY_BACKOFF):
                    delivery.state = str(DeliveryState.FAILED)
                    delivery.next_attempt_at = None
                    await self._audit_failure(delivery, result.detail)
                else:
                    delivery.state = str(DeliveryState.RETRYING)
                    delivery.next_attempt_at = self._next_attempt(delivery.attempts)
                delivery.last_error = result.detail

    async def send_sms(
        self,
        *,
        tenant_id: uuid.UUID,
        notification_id: uuid.UUID,
        phone_e164: str,
        body: str,
        sender_id: str | None = None,
    ) -> NotificationDelivery | None:
        """Send one SMS, metering it against the shop's segment quota.

        Metered *before* sending: a quota checked after the message has gone is
        not a quota. The estimate is what is charged against the plan until the
        provider reports its own count, which is then kept alongside.
        """
        from app.entitlements.catalog import Entitlement
        from app.entitlements.service import EntitlementService

        segments = count_sms_segments(body)
        await EntitlementService(self._db).consume(
            tenant_id, Entitlement.SMS_SEGMENTS_MONTHLY, amount=max(1, segments)
        )

        from app.billing.models import hash_provider_token

        delivery = NotificationDelivery(
            tenant_id=tenant_id,
            notification_id=notification_id,
            channel=str(Channel.SMS),
            target_hash=hash_provider_token(phone_e164),
            target_suffix=phone_e164[-4:],
            state=str(DeliveryState.PENDING),
            estimated_segments=segments,
        )
        self._db.add(delivery)
        try:
            await self._db.flush()
        except IntegrityError:
            await self._db.rollback()
            return None

        delivery.attempts = 1
        result = await self._sms.send(
            SmsMessage(
                phone_e164=phone_e164,
                body=body,
                sender_id=sender_id,
                idempotency_key=f"{notification_id}:{delivery.target_hash}",
            )
        )
        delivery.provider = result.provider
        delivery.provider_reference = result.provider_reference
        delivery.provider_segments = result.provider_segments
        delivery.last_error = result.detail

        if result.delivered:
            delivery.state = str(DeliveryState.SENT)
            delivery.sent_at = utc_now()
        elif result.outcome is DeliveryOutcome.NOT_CONFIGURED:
            delivery.state = str(DeliveryState.NOT_CONFIGURED)
        else:
            delivery.state = str(DeliveryState.FAILED)
        return delivery

    async def retry_due(self, *, now: datetime | None = None, limit: int = 100) -> int:
        """Re-attempt deliveries whose backoff has elapsed."""
        moment = now or utc_now()
        due = (
            (
                await self._db.execute(
                    sa.select(NotificationDelivery)
                    .where(
                        NotificationDelivery.state == str(DeliveryState.RETRYING),
                        NotificationDelivery.next_attempt_at.is_not(None),
                        NotificationDelivery.next_attempt_at <= moment,
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

        from app.billing.models import hash_provider_token

        attempted = 0
        for delivery in due:
            notification = await self._db.get(Notification, delivery.notification_id)
            if notification is None:
                delivery.state = str(DeliveryState.FAILED)
                delivery.last_error = "the notification no longer exists"
                continue
            # The same device the first attempt targeted, found by its token
            # hash among this shop's members — never "any device", which on a
            # system session would be any shop's.
            device = next(
                (
                    candidate
                    for candidate in await self._push_targets(notification.tenant_id)
                    if hash_provider_token(candidate.push_token or "") == delivery.target_hash
                ),
                None,
            )
            if device is None:
                delivery.state = str(DeliveryState.FAILED)
                delivery.last_error = "no device is registered for push any more"
                continue
            locale = await self._db.scalar(sa.select(User.locale).where(User.id == device.user_id))
            await self._attempt_push(notification, device, delivery, locale=locale)
            attempted += 1
        return attempted

    async def _record_suppressed(
        self, notification: Notification, reason: str
    ) -> NotificationDelivery:
        delivery = NotificationDelivery(
            tenant_id=notification.tenant_id,
            notification_id=notification.id,
            channel=str(Channel.PUSH),
            target_hash=f"suppressed:{notification.id.hex}"[:64],
            state=str(DeliveryState.SUPPRESSED),
            suppression_reason=reason[:200],
        )
        self._db.add(delivery)
        try:
            await self._db.flush()
        except IntegrityError:
            await self._db.rollback()
        return delivery

    async def _audit_failure(self, delivery: NotificationDelivery, detail: str | None) -> None:
        await record_audit(
            self._db,
            AuditAction.NOTIFICATION_DISPATCH_FAILED,
            entity_type="notification_delivery",
            entity_id=delivery.id,
            context={
                "channel": delivery.channel,
                "provider": delivery.provider,
                "attempts": delivery.attempts,
                "error": detail,
            },
            tenant_id=delivery.tenant_id,
        )

    @staticmethod
    def _next_attempt(attempts: int) -> datetime:
        index = min(attempts - 1, len(_RETRY_BACKOFF) - 1)
        return utc_now() + _RETRY_BACKOFF[max(0, index)]


def _deep_link(notification: Notification) -> str | None:
    """Section 94: opening a push lands on the exact record it is about."""
    if notification.entity_type and notification.entity_id:
        return f"ecomsbd://{notification.entity_type}/{notification.entity_id}"
    return f"ecomsbd://notifications/{notification.id}"


async def effective_preference(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> NotificationPreference | None:
    """The member's own preference, else the shop default, without creating
    either — reading the centre must not write a row."""
    rows = (
        (
            await session.execute(
                sa.select(NotificationPreference).where(
                    NotificationPreference.tenant_id == tenant_id,
                    sa.or_(
                        NotificationPreference.user_id == user_id,
                        NotificationPreference.user_id.is_(None),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    return next((r for r in rows if r.user_id == user_id), None) or next(iter(rows), None)
