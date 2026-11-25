"""The SMS confirm link (V4 WP2.3).

After the last unanswered call, and only with ``confirmation_sms_link_enabled``
on, the shop's own switch on (Settings → SMS, default off; launch run 14), an
SMS transport configured and ``PUBLIC_WEB_URL`` set, the customer gets
one Banglish SMS with a link to a page where they confirm or cancel:

    ``<shop>: order Tk 1,250. Confirm/cancel: <PUBLIC_WEB_URL>/c/<token>``

The token is 22 url-safe characters derived from the link row's random id
(:meth:`SecretHasher.confirmation_link_token`); only its SHA-256 is stored.
The row moves ``NO_ANSWER → SMS_LINK_SENT`` in the sweep's transaction (or
``SELLER_REVIEW → SMS_LINK_SENT`` when the seller's own call went unanswered,
once per confirmation, operator decision 2026-11-20) and the
SMS itself goes out from the ``confirmation.link_requested`` outbox handler,
so a retried send carries the same link. A link is good for six hours
(``LINK_TTL``); then the sweep moves the row to ``SELLER_REVIEW``.

"Tk", not "৳": the taka sign is outside the GSM 03.38 alphabet and would make
the whole message UCS-2 (70 characters a segment), which breaks the single
GSM segment the spec targets. The segment count is recorded on the attempt.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import OutboxTopic, enqueue
from app.confirmations.manual import QuietHours
from app.confirmations.models import (
    AttemptChannel,
    ConfirmationAttempt,
    ConfirmationLink,
    ConfirmationState,
    OrderConfirmation,
)
from app.confirmations.service import LINK_TTL, MAX_CALL_ATTEMPTS, ConfirmationService
from app.confirmations.state_machine import Cause
from app.core.config import get_settings
from app.notifications.transport import (
    DeliveryOutcome,
    SmsMessage,
    SmsTransport,
    build_sms_transport,
    count_sms_segments,
)

if TYPE_CHECKING:
    from app.core.security import SecretHasher
    from app.customers.service import CustomerService
    from app.orders.service import OrderService

__all__ = [
    "SHOP_LINK_SWITCH",
    "SHOP_NAME_MAX",
    "FollowUp",
    "LinkService",
    "LinkStatus",
    "current_sms_transport",
    "format_taka",
    "hash_token",
    "link_text",
    "link_url",
    "links_available",
    "links_on",
    "public_base",
    "shop_link_switch",
    "sms_transport",
]

S = ConfirmationState

#: The shop name is cut to this, so the text stays one GSM segment.
SHOP_NAME_MAX = 24
#: Longest token the public routes look up (22 in practice).
TOKEN_MAX = 64


class LinkStatus(StrEnum):
    """``confirmation_attempts.status`` for an SMS link."""

    STARTED = "STARTED"
    SENT = "SENT"
    ANSWERED = "ANSWERED"


class FollowUp(StrEnum):
    """What the sweep did with a due row."""

    WAITING = "WAITING"
    RETRY = "RETRY"
    LINK = "LINK"
    QUIET = "QUIET"
    REVIEW = "REVIEW"
    EXPIRED = "EXPIRED"


def sms_transport() -> SmsTransport:
    """The deployment's SMS transport (tests replace this)."""
    return build_sms_transport(get_settings())


async def current_sms_transport(db: AsyncSession) -> SmsTransport:
    """The Alpha SMS gateway when its flag and admin-console account are on
    (V4 WP7.1), else the deployment's settings transport."""
    from app.sms_providers.service import active_sms_transport

    return await active_sms_transport(db) or sms_transport()


def public_base() -> str | None:
    base = (get_settings().public_web_url or "").strip().rstrip("/")
    return base or None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def link_url(base: str, token: str) -> str:
    return f"{base}/c/{token}"


def format_taka(paisa: int) -> str:
    """``125000`` → ``"1,250"``; ``125050`` → ``"1,250.50"``. Integers only."""
    taka, rest = divmod(paisa, 100)
    return f"{taka:,}" if rest == 0 else f"{taka:,}.{rest:02d}"


def link_text(shop_name: str, cod_paisa: int, url: str) -> str:
    shop = " ".join(shop_name.split())[:SHOP_NAME_MAX].strip() or "ecomsbd"
    return f"{shop}: order Tk {format_taka(cod_paisa)}. Confirm/cancel: {url}"


#: ``Tenant.settings`` key: the shop's own switch for the SMS confirm link
#: (launch run 14). Off until the seller turns it on in Settings → SMS, because
#: every link is an SMS that costs from the SMS balance.
SHOP_LINK_SWITCH = "confirmation_sms_link"


async def shop_link_switch(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    from app.tenants.models import Tenant

    settings = await db.scalar(sa.select(Tenant.settings).where(Tenant.id == tenant_id))
    return (settings or {}).get(SHOP_LINK_SWITCH) is True


async def links_available(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """The platform side: flag, a sending SMS transport and ``PUBLIC_WEB_URL``."""
    from app.common.feature_flags import FeatureFlagService, FlagKey

    return (
        public_base() is not None
        and await FeatureFlagService(db).is_enabled(
            FlagKey.CONFIRMATION_SMS_LINK_ENABLED, tenant_id=tenant_id
        )
        and (await current_sms_transport(db)).is_configured
    )


async def links_on(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """A link may go out: the shop switched it on and the platform can send."""
    return await shop_link_switch(db, tenant_id) and await links_available(db, tenant_id)


@dataclass(frozen=True, slots=True)
class Sent:
    outcome: DeliveryOutcome
    segments: int | None


class LinkService:
    """Follow-ups after an unanswered call, and the link itself."""

    def __init__(self, session: AsyncSession, *, orders: OrderService, hasher: SecretHasher):
        self._db = session
        self._hasher = hasher
        self._confirmations = ConfirmationService(session, orders=orders)

    # ------------------------------------------------------------- sweep ---

    async def follow_up(self, row: OrderConfirmation, *, now: datetime) -> FollowUp:
        """A due ``NO_ANSWER`` row (locked): call again, send the link, or ask
        the seller. A due ``SMS_LINK_SENT`` row: the link expired."""
        from app.delivery_rules.service import DeliveryRulesService

        state = row.confirmation_state
        if state is S.SMS_LINK_SENT:
            if row.next_action_at is not None and now < row.next_action_at:
                return FollowUp.WAITING
            await self._expire_links(row, now)
            await self._confirmations.transition(row, S.SELLER_REVIEW, Cause.LINK_EXPIRED, now=now)
            return FollowUp.EXPIRED
        if state is not S.NO_ANSWER:
            return FollowUp.WAITING
        if row.attempt_count < MAX_CALL_ATTEMPTS:
            if row.next_action_at is not None and now < row.next_action_at:
                return FollowUp.WAITING
            await self._confirmations.transition(row, S.CALL_QUEUED, Cause.RETRY, now=now)
            return FollowUp.RETRY
        order_has_customer = await self._has_customer(row)
        if order_has_customer and await links_on(self._db, row.tenant_id):
            rules = await DeliveryRulesService(self._db).get(row.tenant_id)
            if QuietHours.from_rules(rules).active(now):
                return FollowUp.QUIET  # no SMS at night; the next sweep after
            await self._create_link(row, now)
            return FollowUp.LINK
        await self._confirmations.transition(
            row, S.SELLER_REVIEW, Cause.ATTEMPTS_EXHAUSTED, now=now
        )
        return FollowUp.REVIEW

    async def _has_customer(self, row: OrderConfirmation) -> bool:
        from app.orders.models import Order

        order = await self._db.get(Order, row.order_id)
        return order is not None and order.customer_id is not None

    async def after_seller_no_answer(self, row: OrderConfirmation, *, now: datetime) -> bool:
        """The seller's own call went unanswered ("Dhoreni", operator decision
        2026-11-20): send the link once per confirmation, under the same rules
        as after the IVR's last call (flag, transport, ``PUBLIC_WEB_URL``, a
        customer, outside quiet hours). ``False`` leaves the row with the
        seller."""
        from app.delivery_rules.service import DeliveryRulesService

        if row.confirmation_state is not S.SELLER_REVIEW:
            return False
        sent_before = await self._db.scalar(
            sa.select(ConfirmationLink.id)
            .where(ConfirmationLink.confirmation_id == row.id)
            .limit(1)
        )
        if sent_before is not None or not await self._has_customer(row):
            return False
        if not await links_on(self._db, row.tenant_id):
            return False
        rules = await DeliveryRulesService(self._db).get(row.tenant_id)
        if QuietHours.from_rules(rules).active(now):
            return False  # no SMS at night; the seller can call again later
        await self._create_link(row, now, cause=Cause.SELLER_NO_ANSWER)
        return True

    async def _create_link(
        self, row: OrderConfirmation, now: datetime, *, cause: Cause = Cause.LINK_SENT
    ) -> ConfirmationLink:
        transport = await current_sms_transport(self._db)
        attempt = await self._confirmations.add_attempt(
            row, AttemptChannel.SMS_LINK, provider=transport.name, now=now
        )
        link_id = uuid.uuid4()
        link = ConfirmationLink(
            id=link_id,
            confirmation_id=row.id,
            token_hash=hash_token(self._hasher.confirmation_link_token(link_id)),
            expires_at=now + LINK_TTL,
        )
        self._db.add(link)
        await self._db.flush()
        await self._confirmations.transition(row, S.SMS_LINK_SENT, cause, now=now)
        await enqueue(
            self._db,
            OutboxTopic.CONFIRMATION_LINK_REQUESTED,
            {
                "confirmation_id": str(row.id),
                "order_id": str(row.order_id),
                "link_id": str(link.id),
                "attempt_no": attempt.attempt_no,
            },
            dedupe_key=f"{OutboxTopic.CONFIRMATION_LINK_REQUESTED}:{link.id}",
        )
        return link

    async def _expire_links(self, row: OrderConfirmation, now: datetime) -> None:
        await self._db.execute(
            sa.update(ConfirmationLink)
            .where(
                ConfirmationLink.confirmation_id == row.id,
                ConfirmationLink.used_at.is_(None),
                ConfirmationLink.expires_at > now,
            )
            .values(expires_at=now)
        )

    # -------------------------------------------------------------- send ---

    async def deliver(
        self, payload: dict[str, Any], *, now: datetime, customers: CustomerService
    ) -> Sent | None:
        """Send the link's SMS (the outbox handler). ``None`` when there is
        nothing to send any more: already sent, answered, expired or closed."""
        from app.customers.models import Customer
        from app.orders.models import Order
        from app.tenants.models import Tenant

        row = await self._db.scalar(
            sa.select(OrderConfirmation)
            .where(OrderConfirmation.id == uuid.UUID(str(payload["confirmation_id"])))
            .with_for_update()
        )
        link = await self._db.get(ConfirmationLink, uuid.UUID(str(payload["link_id"])))
        attempt = await self._attempt(row, int(payload["attempt_no"])) if row else None
        base = public_base()
        if (
            row is None
            or link is None
            or attempt is None
            or base is None
            or row.confirmation_state is not S.SMS_LINK_SENT
            or attempt.status != LinkStatus.STARTED
            or link.used_at is not None
            or link.expires_at <= now
        ):
            return None
        order = await self._db.get(Order, row.order_id)
        customer = (
            await self._db.get(Customer, order.customer_id)
            if order is not None and order.customer_id is not None
            else None
        )
        if customer is None:
            await self._undelivered(row, link, attempt, str(DeliveryOutcome.REJECTED), now)
            return Sent(DeliveryOutcome.REJECTED, None)
        tenant = await self._db.get(Tenant, row.tenant_id)
        token = self._hasher.confirmation_link_token(link.id)
        text = link_text(
            tenant.name if tenant else "", row.cod_paisa_snapshot, link_url(base, token)
        )
        result = await (await current_sms_transport(self._db)).send(
            SmsMessage(
                phone_e164=customers.phone_number(customer).e164,
                body=text,
                idempotency_key=f"conflink:{link.id}",
            )
        )
        if result.retryable:
            raise RuntimeError(f"SMS link not sent yet: {result.outcome}")
        attempt.provider = result.provider
        attempt.provider_ref = result.provider_reference
        attempt.segments = result.provider_segments or count_sms_segments(text)
        if result.outcome in (DeliveryOutcome.SENT, DeliveryOutcome.DUPLICATE):
            attempt.status = str(LinkStatus.SENT)
            await self._db.flush()
        elif result.outcome is DeliveryOutcome.UNKNOWN:
            # It may have reached the customer: the link stays good until the
            # sweep ends it (LINK_TTL), and it is never sent again.
            attempt.status = str(DeliveryOutcome.UNKNOWN)
            await self._db.flush()
        else:
            await self._undelivered(row, link, attempt, str(result.outcome), now)
        return Sent(result.outcome, attempt.segments)

    async def _undelivered(
        self,
        row: OrderConfirmation,
        link: ConfirmationLink,
        attempt: ConfirmationAttempt,
        status: str,
        now: datetime,
    ) -> None:
        """Nothing reached the customer: the link is dead, the seller decides."""
        attempt.status = status
        attempt.ended_at = now
        link.expires_at = now
        await self._db.flush()
        await self._confirmations.transition(row, S.SELLER_REVIEW, Cause.LINK_EXPIRED, now=now)

    async def _attempt(self, row: OrderConfirmation, attempt_no: int) -> ConfirmationAttempt | None:
        return await self._db.scalar(
            sa.select(ConfirmationAttempt).where(
                ConfirmationAttempt.confirmation_id == row.id,
                ConfirmationAttempt.attempt_no == attempt_no,
            )
        )

    # ------------------------------------------------------------ answer ---

    async def answer(
        self, row: OrderConfirmation, link: ConfirmationLink, *, confirm: bool, now: datetime
    ) -> OrderConfirmation:
        """The customer pressed a button on an open link (both rows locked)."""
        link.used_at = now
        link.result = "CONFIRM" if confirm else "CANCEL"
        attempt = await self._db.scalar(
            sa.select(ConfirmationAttempt)
            .where(
                ConfirmationAttempt.confirmation_id == row.id,
                ConfirmationAttempt.channel == str(AttemptChannel.SMS_LINK),
            )
            .order_by(ConfirmationAttempt.attempt_no.desc())
            .limit(1)
        )
        if attempt is not None:
            attempt.status = str(LinkStatus.ANSWERED)
            attempt.ended_at = now
        await self._db.flush()
        return await self._confirmations.transition(
            row,
            S.CONFIRMED if confirm else S.CANCELLED,
            Cause.LINK_CONFIRM if confirm else Cause.LINK_CANCEL,
            now=now,
        )
