"""Shop referrals (V4 WP7.4).

A shop shares its code; another shop enters it before its first payment.
When that shop's first bKash / Nagad payment is approved (WP2.5), both shops
get :data:`CREDIT_DAYS` days of plan credit through the admin-manual grant —
the same path as support credit, so it is audited, never auto-renews and adds
after any time already paid.

Rules (conservative where the spec is silent, see the build ledger):

*   Off unless ``referrals_enabled`` is on **and** the shop could pay by TrxID
    (the reward only ever follows such a payment): never in Free Launch Mode,
    never in a Play build, never without a receiving number.
*   A shop is referred once, before its first payment, by an active shop that
    is not itself, does not share an owner with it and was not referred by it.
*   The reward is granted once: the referral row moves PENDING → REWARDED
    under a row lock, and each grant carries a unique billing
    ``provider_event_id`` (``referral:<id>:referred|referrer``).
*   A referrer paying through a billing provider (Play, bKash web) is held for
    support rather than having its provider's subscription replaced.

Ids, codes and states only: no shop name, phone or message is stored or logged.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.manual_payments import ManualPaymentService
from app.billing.manual_payments import Unavailable as PaymentUnavailable
from app.billing.models import (
    BillingPaymentSubmission,
    BillingProviderKind,
    BillingTransaction,
    DistributionChannel,
    ManualPaymentState,
    TransactionKind,
    TransactionState,
)
from app.common.audit import AuditAction, record_audit
from app.common.cache import RateLimiter
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.common.phone import normalize_digits
from app.core.clock import ensure_utc, utc_now
from app.core.config import Settings
from app.core.errors import AppError, ConflictError, ErrorCode, RateLimitedError, ValidationError
from app.core.logging import get_logger
from app.db.session import system_session
from app.entitlements.catalog import PlanCode
from app.entitlements.models import Subscription
from app.entitlements.service import EntitlementResolver
from app.referrals.models import (
    Referral,
    ReferralCode,
    ReferralCreditState,
    ReferralHoldReason,
    ReferralState,
    ReferralVoidReason,
)
from app.tenants.models import Tenant, TenantStatus, TenantUser
from app.tenants.roles import TenantRole

__all__ = [
    "APPLIES_PER_WINDOW",
    "CODE_LENGTH",
    "CREDIT_DAYS",
    "Availability",
    "ReferralOverview",
    "ReferralRewards",
    "ReferralService",
    "ReferredBy",
    "list_referrals",
    "new_code",
    "normalise_code",
]

log = get_logger("app.referrals")

#: Days of plan credit each shop gets.
CREDIT_DAYS = 30
CODE_LENGTH = 8
#: No 0/O, 1/I/L: read aloud or typed from a screenshot without mistakes.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_RE = re.compile(rf"^[{_ALPHABET}]{{{CODE_LENGTH}}}$")
_SEPARATORS = re.compile(r"[\s\-]+")

#: Per shop. Enough for typos; too few to walk the code space.
APPLIES_PER_WINDOW = 10
APPLY_WINDOW_SECONDS = 3600

#: A payment already made: a verified (or later refunded) purchase or renewal.
_PAID_KINDS = (str(TransactionKind.PURCHASE), str(TransactionKind.RENEWAL))
_PAID_STATES = (str(TransactionState.VERIFIED), str(TransactionState.REFUNDED))


def new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))


def normalise_code(raw: str | None) -> str:
    """Upper case, Bangla digits to ASCII, spaces and dashes dropped."""
    text = _SEPARATORS.sub("", normalize_digits(raw or "")).upper()
    if not _CODE_RE.match(text):
        raise ValidationError(
            "That is not a referral code",
            message_bn="এটি সঠিক রেফারেল কোড নয়",
            details={"code": "REFERRAL_CODE_INVALID"},
        )
    return text


@dataclass(frozen=True, slots=True)
class Availability:
    available: bool
    #: ``FEATURE_OFF`` (referrals), ``PAYMENTS_OFF``, ``FREE_LAUNCH``,
    #: ``CHANNEL`` or ``NO_ACCOUNTS`` (the TrxID payment the reward follows).
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReferredBy:
    """The referral that names this shop, as this shop may see it."""

    referral_id: uuid.UUID
    code: str
    state: str
    credit_state: str
    applied_at: datetime
    rewarded_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReferralOverview:
    code: str | None
    referred_by: ReferredBy | None
    referrals: list[Referral]


def _referred_by(row: Referral, code: str) -> ReferredBy:
    return ReferredBy(
        referral_id=row.id,
        code=code,
        state=row.state,
        credit_state=row.referred_credit_state,
        applied_at=row.created_at,
        rewarded_at=row.rewarded_at,
    )


def _refused(message: str, message_bn: str, code: str, *, conflict: bool = False) -> AppError:
    error = ConflictError if conflict else ValidationError
    return error(message, message_bn=message_bn, details={"code": code})


async def _has_paid(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    approved = await db.scalar(
        sa.select(BillingPaymentSubmission.id)
        .where(
            BillingPaymentSubmission.tenant_id == tenant_id,
            BillingPaymentSubmission.state == str(ManualPaymentState.APPROVED),
        )
        .limit(1)
    )
    if approved is not None:
        return True
    purchase = await db.scalar(
        sa.select(BillingTransaction.id)
        .where(
            BillingTransaction.tenant_id == tenant_id,
            BillingTransaction.kind.in_(_PAID_KINDS),
            BillingTransaction.state.in_(_PAID_STATES),
        )
        .limit(1)
    )
    return purchase is not None


async def _shop_active(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    tenant = await db.get(Tenant, tenant_id)
    return tenant is not None and tenant.status == str(TenantStatus.ACTIVE)


async def _current_subscription(db: AsyncSession, tenant_id: uuid.UUID) -> Subscription | None:
    return await db.scalar(
        sa.select(Subscription)
        .where(Subscription.tenant_id == tenant_id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )


class ReferralService:
    """The shop's side, in the request's tenant-scoped session.

    The shop's own code and its own referrals are ordinary tenant reads.
    Applying a code and reading who referred this shop cross shops, so those
    run in a system session in which every query names its tenant.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings,
        flags: FeatureFlagService | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._flags = flags or FeatureFlagService(db)
        self._limiter = limiter

    async def availability(
        self, tenant_id: uuid.UUID, channel: DistributionChannel
    ) -> Availability:
        if not await self._flags.is_enabled(FlagKey.REFERRALS_ENABLED, tenant_id=tenant_id):
            return Availability(False, "FEATURE_OFF")
        payments = await ManualPaymentService(
            self._db, settings=self._settings, flags=self._flags
        ).availability(tenant_id, channel)
        if not payments.available:
            if payments.reason is PaymentUnavailable.FEATURE_OFF:
                return Availability(False, "PAYMENTS_OFF")
            return Availability(False, str(payments.reason))
        return Availability(True)

    async def _require_available(self, tenant_id: uuid.UUID, channel: DistributionChannel) -> None:
        availability = await self.availability(tenant_id, channel)
        if not availability.available:
            raise AppError(
                "Referrals are not available",
                code=ErrorCode.FEATURE_DISABLED,
                message_bn="এখন রেফারেল চালু নেই",
                details={"code": "REFERRALS_UNAVAILABLE", "reason": availability.reason},
            )

    async def _own_code(self, tenant_id: uuid.UUID) -> ReferralCode | None:
        return await self._db.scalar(
            sa.select(ReferralCode).where(ReferralCode.tenant_id == tenant_id)
        )

    async def ensure_code(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        channel: DistributionChannel,
    ) -> tuple[ReferralCode, bool]:
        """The shop's code, made on first use. Returns ``(row, created)``."""
        await self._require_available(tenant_id, channel)
        existing = await self._own_code(tenant_id)
        if existing is not None:
            return existing, False
        for _ in range(5):
            row = ReferralCode(tenant_id=tenant_id, code=new_code(), created_by_user_id=actor_id)
            try:
                async with self._db.begin_nested():
                    self._db.add(row)
                    await self._db.flush()
            except IntegrityError:
                # This shop's other request won, or (rarely) the code is taken.
                existing = await self._own_code(tenant_id)
                if existing is not None:
                    return existing, False
                continue
            return row, True
        raise ConflictError(
            "Could not make a referral code",
            message_bn="রেফারেল কোড তৈরি করা যায়নি, আবার চেষ্টা করুন",
            retryable=True,
            details={"code": "REFERRAL_CODE_RETRY"},
        )

    async def overview(self, tenant_id: uuid.UUID) -> ReferralOverview:
        code = await self._own_code(tenant_id)
        referrals = list(
            (
                await self._db.scalars(
                    sa.select(Referral)
                    .where(Referral.tenant_id == tenant_id)
                    .order_by(Referral.created_at.desc())
                    .limit(100)
                )
            ).all()
        )
        return ReferralOverview(
            code=code.code if code is not None else None,
            referred_by=await self._referred_by(tenant_id),
            referrals=referrals,
        )

    async def _referred_by(self, tenant_id: uuid.UUID) -> ReferredBy | None:
        async with system_session("referrals: the referral naming this shop") as db:
            row = await db.scalar(
                sa.select(Referral).where(Referral.referred_tenant_id == tenant_id)
            )
            if row is None:
                return None
            code = await db.scalar(
                sa.select(ReferralCode.code).where(
                    ReferralCode.id == row.referral_code_id,
                    ReferralCode.tenant_id == row.tenant_id,
                )
            )
            return _referred_by(row, code or "")

    async def apply(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        channel: DistributionChannel,
        raw_code: str,
    ) -> tuple[ReferredBy, bool]:
        """Join with another shop's code. Returns ``(referral, created)``.

        The same code sent again returns the first answer (200); every refusal
        is a stable code in ``details.code``.
        """
        await self._require_available(tenant_id, channel)
        code = normalise_code(raw_code)
        limited = await (self._limiter or RateLimiter()).hit(
            "referral-apply",
            str(tenant_id),
            limit=APPLIES_PER_WINDOW,
            window_seconds=APPLY_WINDOW_SECONDS,
        )
        if not limited.allowed:
            raise RateLimitedError(retry_after_seconds=limited.retry_after_seconds)

        async with system_session("referrals: apply a code across two shops") as db:
            owner = await db.scalar(sa.select(ReferralCode).where(ReferralCode.code == code))
            existing = await db.scalar(
                sa.select(Referral).where(Referral.referred_tenant_id == tenant_id)
            )
            if existing is not None:
                if owner is not None and existing.referral_code_id == owner.id:
                    return _referred_by(existing, code), False
                raise _refused(
                    "This shop already joined with a referral code",
                    "এই দোকান আগেই একটি রেফারেল কোড ব্যবহার করেছে",
                    "REFERRAL_ALREADY_APPLIED",
                    conflict=True,
                )
            if owner is None or not await _shop_active(db, owner.tenant_id):
                raise _refused(
                    "No shop has this referral code",
                    "এই রেফারেল কোড কোনো দোকানের নয়",
                    "REFERRAL_CODE_UNKNOWN",
                )
            if owner.tenant_id == tenant_id:
                raise _refused(
                    "This is your own shop's code",
                    "এটি আপনার নিজের দোকানের কোড",
                    "REFERRAL_OWN_SHOP",
                )
            if await self._share_an_owner(db, owner.tenant_id, tenant_id):
                raise _refused(
                    "Both shops have the same owner",
                    "দুই দোকানের মালিক একই",
                    "REFERRAL_SAME_OWNER",
                )
            cycle = await db.scalar(
                sa.select(Referral.id).where(
                    Referral.tenant_id == tenant_id,
                    Referral.referred_tenant_id == owner.tenant_id,
                )
            )
            if cycle is not None:
                raise _refused(
                    "That shop joined with your code",
                    "ওই দোকান আপনার কোড দিয়ে যোগ দিয়েছে",
                    "REFERRAL_CYCLE",
                )
            if await _has_paid(db, tenant_id):
                raise _refused(
                    "A code can only be used before the first payment",
                    "প্রথম পেমেন্টের আগেই শুধু কোড ব্যবহার করা যায়",
                    "REFERRAL_AFTER_FIRST_PAYMENT",
                    conflict=True,
                )

            row = Referral(
                tenant_id=owner.tenant_id,
                referral_code_id=owner.id,
                referred_tenant_id=tenant_id,
                applied_by_user_id=actor_id,
                state=str(ReferralState.PENDING),
                referred_credit_state=str(ReferralCreditState.PENDING),
                referrer_credit_state=str(ReferralCreditState.PENDING),
            )
            try:
                async with db.begin_nested():
                    db.add(row)
                    await db.flush()
            except IntegrityError:
                # The same shop's other request got there first.
                raced = await db.scalar(
                    sa.select(Referral).where(Referral.referred_tenant_id == tenant_id)
                )
                if raced is not None and raced.referral_code_id == owner.id:
                    return _referred_by(raced, code), False
                raise _refused(
                    "This shop already joined with a referral code",
                    "এই দোকান আগেই একটি রেফারেল কোড ব্যবহার করেছে",
                    "REFERRAL_ALREADY_APPLIED",
                    conflict=True,
                ) from None
            await record_audit(
                db,
                AuditAction.REFERRAL_APPLIED,
                entity_type="referral",
                entity_id=row.id,
                context={
                    "referral_code_id": str(owner.id),
                    "referrer_tenant_id": str(owner.tenant_id),
                },
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
            log.info(
                "referral applied",
                extra={"referral_id": str(row.id), "tenant_id": str(tenant_id)},
            )
            return _referred_by(row, code), True

    @staticmethod
    async def _share_an_owner(db: AsyncSession, first: uuid.UUID, second: uuid.UUID) -> bool:
        owners = sa.select(TenantUser.user_id).where(
            TenantUser.tenant_id == first, TenantUser.role == str(TenantRole.OWNER)
        )
        shared = await db.scalar(
            sa.select(TenantUser.id)
            .where(
                TenantUser.tenant_id == second,
                TenantUser.role == str(TenantRole.OWNER),
                TenantUser.user_id.in_(owners),
            )
            .limit(1)
        )
        return shared is not None


class ReferralRewards:
    """Grants the two credits. Runs in a system session (the outbox handler),
    because it writes two shops; every query names its tenant."""

    def __init__(self, db: AsyncSession, *, settings: Settings, registry: Any = None) -> None:
        self._db = db
        self._settings = settings
        self._registry = registry

    async def payment_approved(self, submission_id: uuid.UUID) -> Referral | None:
        """Reward the referral of the paying shop, once. Safe to repeat."""
        submission = await self._db.get(BillingPaymentSubmission, submission_id)
        if submission is None or submission.state != ManualPaymentState.APPROVED:
            return None
        tenant_id = submission.tenant_id
        referral = (
            await self._db.execute(
                sa.select(Referral)
                .where(Referral.referred_tenant_id == tenant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if referral is None or referral.state != ReferralState.PENDING:
            return referral
        if not await FeatureFlagService(self._db).is_enabled(
            FlagKey.REFERRALS_ENABLED, tenant_id=tenant_id
        ):
            # Kill switch: nothing is granted while off. The referral waits;
            # the shop's next approved payment looks again.
            log.info("referral reward waits: feature off", extra={"referral_id": str(referral.id)})
            return referral

        first = await self._first_approved(tenant_id)
        if first is None:
            return referral
        if await self._paid_elsewhere_before(tenant_id, first) or (
            first.reviewed_at is not None
            and ensure_utc(first.reviewed_at) < ensure_utc(referral.created_at)
        ):
            referral.state = str(ReferralState.VOID)
            referral.void_reason = str(ReferralVoidReason.NOT_FIRST_PAYMENT)
            await self._db.flush()
            await self._audit(referral, tenant_id)
            log.info(
                "referral void: not the first payment", extra={"referral_id": str(referral.id)}
            )
            return referral

        await self._grant(referral, first)
        return referral

    async def _first_approved(self, tenant_id: uuid.UUID) -> BillingPaymentSubmission | None:
        return await self._db.scalar(
            sa.select(BillingPaymentSubmission)
            .where(
                BillingPaymentSubmission.tenant_id == tenant_id,
                BillingPaymentSubmission.state == str(ManualPaymentState.APPROVED),
            )
            .order_by(BillingPaymentSubmission.reviewed_at.asc(), BillingPaymentSubmission.id.asc())
            .limit(1)
        )

    async def _paid_elsewhere_before(
        self, tenant_id: uuid.UUID, first: BillingPaymentSubmission
    ) -> bool:
        """A store / provider purchase came before the first TrxID payment."""
        stmt = sa.select(BillingTransaction.id).where(
            BillingTransaction.tenant_id == tenant_id,
            BillingTransaction.provider != str(BillingProviderKind.MANUAL_ADMIN),
            BillingTransaction.kind.in_(_PAID_KINDS),
            BillingTransaction.state.in_(_PAID_STATES),
        )
        if first.reviewed_at is not None:
            stmt = stmt.where(BillingTransaction.occurred_at <= first.reviewed_at)
        return await self._db.scalar(stmt.limit(1)) is not None

    async def _grant(self, referral: Referral, first: BillingPaymentSubmission) -> None:
        from app.billing.providers.registry import build_registry
        from app.billing.service import BillingService

        billing = BillingService(
            self._db,
            settings=self._settings,
            registry=self._registry or build_registry(self._settings),
        )
        now = utc_now()
        reason = f"Referral credit: {CREDIT_DAYS} days (referral {referral.id})"
        paid_plan = PlanCode(first.plan_code)

        referred_plan = await self._credit_plan(first.tenant_id, fallback=paid_plan, at=now)
        await billing.grant_manual_subscription(
            tenant_id=first.tenant_id,
            plan=referred_plan,
            days=CREDIT_DAYS,
            reason=reason,
            actor_id=None,
            actor_label="referral",
            provider_event_id=f"referral:{referral.id}:referred",
        )
        referral.referred_credit_state = str(ReferralCreditState.GRANTED)

        hold = await self._referrer_hold(referral.tenant_id, at=now)
        if hold is not None:
            referral.referrer_credit_state = str(ReferralCreditState.HELD)
            referral.referrer_hold_reason = str(hold)
        else:
            referrer_plan = await self._credit_plan(referral.tenant_id, fallback=paid_plan, at=now)
            await billing.grant_manual_subscription(
                tenant_id=referral.tenant_id,
                plan=referrer_plan,
                days=CREDIT_DAYS,
                reason=reason,
                actor_id=None,
                actor_label="referral",
                provider_event_id=f"referral:{referral.id}:referrer",
            )
            referral.referrer_credit_state = str(ReferralCreditState.GRANTED)

        referral.state = str(ReferralState.REWARDED)
        referral.rewarded_at = now
        referral.qualifying_submission_id = first.id
        await self._db.flush()
        await self._audit(referral, first.tenant_id)
        await self._audit(referral, referral.tenant_id)
        log.info(
            "referral rewarded",
            extra={
                "referral_id": str(referral.id),
                "referrer_credit_state": referral.referrer_credit_state,
                "referrer_hold_reason": referral.referrer_hold_reason,
            },
        )

    async def _credit_plan(
        self, tenant_id: uuid.UUID, *, fallback: PlanCode, at: datetime
    ) -> PlanCode:
        """The shop's current paid plan; a Free shop gets the plan that was paid for."""
        subscription = await _current_subscription(self._db, tenant_id)
        plan = EntitlementResolver.resolve(subscription, at=at).plan.code
        return plan if plan is not PlanCode.FREE else fallback

    async def _referrer_hold(
        self, tenant_id: uuid.UUID, *, at: datetime
    ) -> ReferralHoldReason | None:
        if not await _shop_active(self._db, tenant_id):
            return ReferralHoldReason.SHOP_NOT_ACTIVE
        subscription = await _current_subscription(self._db, tenant_id)
        if (
            subscription is not None
            and subscription.source != str(BillingProviderKind.MANUAL_ADMIN)
            and subscription.plan_code != str(PlanCode.FREE)
            and subscription.is_current(at=at)
        ):
            return ReferralHoldReason.PROVIDER_SUBSCRIPTION
        return None

    async def _audit(self, referral: Referral, tenant_id: uuid.UUID) -> None:
        await record_audit(
            self._db,
            AuditAction.REFERRAL_REWARDED,
            entity_type="referral",
            entity_id=referral.id,
            reason=referral.void_reason or referral.referrer_hold_reason,
            context={
                "state": referral.state,
                "referred_credit_state": referral.referred_credit_state,
                "referrer_credit_state": referral.referrer_credit_state,
                "qualifying_submission_id": (
                    str(referral.qualifying_submission_id)
                    if referral.qualifying_submission_id
                    else None
                ),
            },
            tenant_id=tenant_id,
        )


async def list_referrals(
    db: AsyncSession,
    *,
    state: ReferralState | None = None,
    referrer_credit_state: ReferralCreditState | None = None,
    tenant_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[Referral]:
    """The admin console's view (an unscoped session): newest first."""
    stmt = sa.select(Referral)
    if state is not None:
        stmt = stmt.where(Referral.state == str(state))
    if referrer_credit_state is not None:
        stmt = stmt.where(Referral.referrer_credit_state == str(referrer_credit_state))
    if tenant_id is not None:
        stmt = stmt.where(
            sa.or_(Referral.tenant_id == tenant_id, Referral.referred_tenant_id == tenant_id)
        )
    return list((await db.scalars(stmt.order_by(Referral.created_at.desc()).limit(limit))).all())
