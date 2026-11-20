"""Voice call credits (V4 WP3.4).

Every IVR call is paid for before the provider is asked:

*   **What pays.** The plan's monthly allowance (``voice_calls_monthly``:
    Free 0, Starter 50, Pro 200) first, then the shop's oldest unexpired
    :class:`~app.voice.models.VoiceCreditPack`. In Free Launch Mode the plan is
    unlimited, so two Asia/Dhaka daily ceilings protect the operator's bill
    instead: ``VOICE_DAILY_SHOP_CAP`` per shop and ``VOICE_DAILY_GLOBAL_CAP``
    for every shop together; packs are not spent then.
*   **Atomic.** Each spend is one conditional ``UPDATE`` whose ``WHERE`` holds
    the check, so two workers dialling at the boundary cannot both take the
    last call. The spend happens in the dispatcher's first transaction, with
    the attempt, so no call can be placed without one.
*   **Metered per placed call.** A call that reached the provider (``PLACED``,
    ``DUPLICATE``) or may have (``UNKNOWN``, a stuck ``CALLING`` row) stays
    charged: the provider may bill it. Only a call the provider certainly never
    placed (``FAILED``, ``REJECTED``, ``NOT_CONFIGURED``) is given back.

Credits are a quota, not money: no ledger row is written here.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_date, utc_now
from app.core.config import Settings, get_settings
from app.core.errors import (
    AppError,
    ErrorCode,
    IdempotencyConflictError,
    NotFoundError,
    ValidationError,
)
from app.entitlements.catalog import UNLIMITED, Entitlement
from app.entitlements.service import EntitlementService
from app.entitlements.usage import (
    UsageCounter,
    UsagePeriod,
    decrement_atomically,
    increment_atomically,
    period_key_for,
)
from app.voice.models import (
    PACK_TTL_DAYS,
    ChargeSource,
    ChargeState,
    PackSource,
    VoiceCallCharge,
    VoiceCreditPack,
    VoiceDailyUsage,
)

__all__ = [
    "MAX_PACK_CALLS",
    "MAX_REPORT_DAYS",
    "SHOP_DAILY_KEY",
    "CreditBalance",
    "Reservation",
    "VoiceCredits",
    "VoiceReport",
    "call_cost",
    "voice_report",
]

#: The Free Launch per-shop daily counter, kept in ``usage_counters`` beside
#: the plan quotas. An operational ceiling, not a plan entitlement.
SHOP_DAILY_KEY = "voice_calls_daily_safety"
#: Largest single pack an operator can grant.
MAX_PACK_CALLS = 10_000
#: Widest admin report, in days.
MAX_REPORT_DAYS = 31

_PLAN = Entitlement.VOICE_CALLS_MONTHLY
#: Statuses of a call somebody picked up.
_ANSWERED = frozenset({"ANSWERED", "CONFIRMED", "CANCELLED", "CALLBACK", "NO_KEY"})


def _rowcount(result: Any) -> int:
    return int(cast("CursorResult[Any]", result).rowcount or 0)


def _day(now: datetime) -> str:
    return business_date(None, at=now).isoformat()


@dataclass(frozen=True, slots=True)
class Reservation:
    """One call's credit, spent and not yet tied to an attempt."""

    tenant_id: uuid.UUID
    source: ChargeSource
    day: str
    period_key: str | None = None
    pack_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CreditBalance:
    free_launch: bool
    has_credit: bool
    #: This month's plan allowance (``limit`` -1 = unlimited).
    allowance_limit: int
    allowance_used: int
    allowance_period: str
    #: Unexpired packs with calls left, oldest first.
    packs: tuple[VoiceCreditPack, ...]
    #: Free Launch only: today's counts against the two ceilings.
    shop_today: int = 0
    shop_cap: int = 0
    global_today: int = 0
    global_cap: int = 0

    @property
    def allowance_remaining(self) -> int | None:
        if self.allowance_limit == UNLIMITED:
            return None
        return max(0, self.allowance_limit - self.allowance_used)

    @property
    def pack_calls_remaining(self) -> int:
        return sum(pack.remaining for pack in self.packs)


class VoiceCredits:
    """One shop's voice credit. Works in a tenant session or an admin one."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._db = session
        self._settings = settings or get_settings()

    # ------------------------------------------------------------ spend ---

    async def reserve(self, tenant_id: uuid.UUID, *, now: datetime) -> Reservation | None:
        """Spend one call's credit, or ``None`` when the shop has none."""
        day = _day(now)
        if self._settings.free_launch_mode:
            return await self._reserve_free_launch(tenant_id, day)
        period = await EntitlementService(self._db, settings=self._settings).try_consume(
            tenant_id, _PLAN, at=now
        )
        if period is not None:
            return Reservation(tenant_id, ChargeSource.PLAN_ALLOWANCE, day, period_key=period)
        pack_id = await self._spend_pack(tenant_id, now)
        if pack_id is not None:
            return Reservation(tenant_id, ChargeSource.PACK, day, pack_id=pack_id)
        return None

    async def undo(self, reservation: Reservation) -> None:
        """Give a reserved credit back to where it came from."""
        tenant_id = reservation.tenant_id
        if reservation.source is ChargeSource.PLAN_ALLOWANCE and reservation.period_key:
            await decrement_atomically(
                self._db,
                tenant_id=tenant_id,
                entitlement=_PLAN,
                period_key=reservation.period_key,
                amount=1,
            )
        elif reservation.source is ChargeSource.PACK and reservation.pack_id is not None:
            await self._db.execute(
                sa.update(VoiceCreditPack)
                .where(
                    VoiceCreditPack.id == reservation.pack_id,
                    VoiceCreditPack.tenant_id == tenant_id,
                    VoiceCreditPack.remaining < VoiceCreditPack.calls,
                )
                .values(remaining=VoiceCreditPack.remaining + 1, updated_at=utc_now())
                .execution_options(synchronize_session=False)
            )
        elif reservation.source is ChargeSource.FREE_LAUNCH:
            await decrement_atomically(
                self._db,
                tenant_id=tenant_id,
                entitlement=SHOP_DAILY_KEY,
                period_key=reservation.day,
                amount=1,
            )
            await self._db.execute(
                sa.update(VoiceDailyUsage)
                .where(VoiceDailyUsage.day == reservation.day, VoiceDailyUsage.used >= 1)
                .values(used=VoiceDailyUsage.used - 1, updated_at=utc_now())
                .execution_options(synchronize_session=False)
            )

    async def charge(
        self, reservation: Reservation, *, attempt_id: uuid.UUID, provider: str
    ) -> VoiceCallCharge:
        """Tie a reservation to the attempt it pays for (same transaction)."""
        row = VoiceCallCharge(
            tenant_id=reservation.tenant_id,
            attempt_id=attempt_id,
            provider=provider[:40],
            source=str(reservation.source),
            pack_id=reservation.pack_id,
            period_key=reservation.period_key,
            day=reservation.day,
            state=str(ChargeState.CHARGED),
        )
        self._db.add(row)
        await self._db.flush()
        return row

    async def release(self, attempt_id: uuid.UUID, *, now: datetime) -> bool:
        """The provider certainly never placed this call: give its credit back.
        Once only; returns whether anything was given back."""
        row = await self._db.scalar(
            sa.select(VoiceCallCharge)
            .where(
                VoiceCallCharge.attempt_id == attempt_id,
                VoiceCallCharge.state == str(ChargeState.CHARGED),
            )
            .with_for_update()
        )
        if row is None:
            return False
        await self.undo(
            Reservation(
                row.tenant_id,
                ChargeSource(row.source),
                row.day,
                period_key=row.period_key,
                pack_id=row.pack_id,
            )
        )
        row.state = str(ChargeState.RELEASED)
        row.released_at = now
        await self._db.flush()
        return True

    async def _reserve_free_launch(self, tenant_id: uuid.UUID, day: str) -> Reservation | None:
        shop_cap = self._settings.voice_daily_shop_cap
        global_cap = self._settings.voice_daily_global_cap
        if shop_cap <= 0 or global_cap <= 0:
            return None
        await self._ensure_shop_counter(tenant_id, day, shop_cap)
        if not await increment_atomically(
            self._db,
            tenant_id=tenant_id,
            entitlement=SHOP_DAILY_KEY,
            period_key=day,
            amount=1,
            limit_value=shop_cap,
        ):
            return None
        await self._ensure_global_row(day)
        spent = await self._db.execute(
            sa.update(VoiceDailyUsage)
            .where(VoiceDailyUsage.day == day, VoiceDailyUsage.used + 1 <= global_cap)
            .values(used=VoiceDailyUsage.used + 1, updated_at=utc_now())
            .execution_options(synchronize_session=False)
        )
        if not _rowcount(spent):
            await decrement_atomically(
                self._db, tenant_id=tenant_id, entitlement=SHOP_DAILY_KEY, period_key=day, amount=1
            )
            return None
        return Reservation(tenant_id, ChargeSource.FREE_LAUNCH, day, period_key=day)

    async def _spend_pack(self, tenant_id: uuid.UUID, now: datetime) -> uuid.UUID | None:
        """One call off the oldest unexpired pack that has one."""
        candidates = (
            await self._db.scalars(
                sa.select(VoiceCreditPack.id)
                .where(
                    VoiceCreditPack.tenant_id == tenant_id,
                    VoiceCreditPack.remaining > 0,
                    VoiceCreditPack.expires_at > now,
                )
                .order_by(VoiceCreditPack.created_at, VoiceCreditPack.id)
                .limit(5)
            )
        ).all()
        for pack_id in candidates:
            spent = await self._db.execute(
                sa.update(VoiceCreditPack)
                .where(
                    VoiceCreditPack.id == pack_id,
                    VoiceCreditPack.tenant_id == tenant_id,
                    VoiceCreditPack.remaining >= 1,
                    VoiceCreditPack.expires_at > now,
                )
                .values(remaining=VoiceCreditPack.remaining - 1, updated_at=utc_now())
                .execution_options(synchronize_session=False)
            )
            if _rowcount(spent):
                return pack_id
        return None

    async def _ensure_shop_counter(self, tenant_id: uuid.UUID, day: str, cap: int) -> None:
        existing = await self._shop_counter(tenant_id, day)
        if existing is not None:
            if existing.limit_value != cap:
                existing.limit_value = cap
                await self._db.flush()
            return
        savepoint = await self._db.begin_nested()
        try:
            self._db.add(
                UsageCounter(
                    tenant_id=tenant_id,
                    entitlement_key=SHOP_DAILY_KEY,
                    period_key=day,
                    period_type=str(UsagePeriod.DAILY),
                    used=0,
                    limit_value=cap,
                    plan_code="free_launch",
                )
            )
            await savepoint.commit()
        except IntegrityError:
            await savepoint.rollback()

    async def _ensure_global_row(self, day: str) -> None:
        if await self._db.scalar(sa.select(VoiceDailyUsage.id).where(VoiceDailyUsage.day == day)):
            return
        savepoint = await self._db.begin_nested()
        try:
            self._db.add(VoiceDailyUsage(day=day, used=0))
            await savepoint.commit()
        except IntegrityError:
            await savepoint.rollback()

    async def _shop_counter(self, tenant_id: uuid.UUID, day: str) -> UsageCounter | None:
        return await self._db.scalar(
            sa.select(UsageCounter)
            .where(
                UsageCounter.tenant_id == tenant_id,
                UsageCounter.entitlement_key == SHOP_DAILY_KEY,
                UsageCounter.period_key == day,
            )
            .execution_options(populate_existing=True)
        )

    # ----------------------------------------------------------- read ---

    async def balance(self, tenant_id: uuid.UUID, *, now: datetime) -> CreditBalance:
        """What the shop can still call with (read-only)."""
        entitlements = EntitlementService(self._db, settings=self._settings)
        limit = await entitlements.limit(tenant_id, _PLAN)
        period = period_key_for(_PLAN, at=now)
        used = await self._db.scalar(
            sa.select(UsageCounter.used)
            .where(
                UsageCounter.tenant_id == tenant_id,
                UsageCounter.entitlement_key == str(_PLAN),
                UsageCounter.period_key == period,
            )
            .execution_options(populate_existing=True)
        )
        packs = tuple(
            (
                await self._db.scalars(
                    sa.select(VoiceCreditPack)
                    .where(
                        VoiceCreditPack.tenant_id == tenant_id,
                        VoiceCreditPack.remaining > 0,
                        VoiceCreditPack.expires_at > now,
                    )
                    .order_by(VoiceCreditPack.created_at, VoiceCreditPack.id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if self._settings.free_launch_mode:
            day = _day(now)
            shop = await self._shop_counter(tenant_id, day)
            shop_today = shop.used if shop is not None else 0
            global_today = int(
                await self._db.scalar(
                    sa.select(VoiceDailyUsage.used)
                    .where(VoiceDailyUsage.day == day)
                    .execution_options(populate_existing=True)
                )
                or 0
            )
            shop_cap = self._settings.voice_daily_shop_cap
            global_cap = self._settings.voice_daily_global_cap
            return CreditBalance(
                free_launch=True,
                has_credit=shop_today < shop_cap and global_today < global_cap,
                allowance_limit=limit,
                allowance_used=int(used or 0),
                allowance_period=period,
                packs=packs,
                shop_today=shop_today,
                shop_cap=shop_cap,
                global_today=global_today,
                global_cap=global_cap,
            )
        allowance_left = limit == UNLIMITED or (limit > 0 and int(used or 0) < limit)
        return CreditBalance(
            free_launch=False,
            has_credit=allowance_left or bool(packs),
            allowance_limit=limit,
            allowance_used=int(used or 0),
            allowance_period=period,
            packs=packs,
        )

    # --------------------------------------------------------- grant ---

    async def grant_pack(
        self,
        tenant_id: uuid.UUID,
        *,
        calls: int,
        grant_key: str,
        reason: str,
        granted_by: str,
        payment_submission_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> tuple[VoiceCreditPack, bool]:
        """Grant a pack (admin). Returns the pack and whether it is new: the
        same ``grant_key`` with the same terms returns the first pack."""
        from app.billing.models import BillingPaymentSubmission, ManualPaymentState

        if not 1 <= calls <= MAX_PACK_CALLS:
            raise ValidationError(
                f"A pack holds 1 to {MAX_PACK_CALLS} calls",
                message_bn=f"একটি প্যাকে ১ থেকে {MAX_PACK_CALLS}টি কল দেওয়া যায়।",
            )
        reason = reason.strip()
        grant_key = grant_key.strip()
        if not reason or not grant_key:
            raise ValidationError("A reason and an idempotency key are required")
        source = PackSource.PAYMENT_SUBMISSION if payment_submission_id else PackSource.ADMIN_GRANT
        existing = await self._db.scalar(
            sa.select(VoiceCreditPack).where(
                VoiceCreditPack.tenant_id == tenant_id, VoiceCreditPack.grant_key == grant_key
            )
        )
        if existing is not None:
            return self._same_grant(existing, calls, source, payment_submission_id), False
        if payment_submission_id is not None:
            submission = await self._db.get(BillingPaymentSubmission, payment_submission_id)
            if submission is None or submission.tenant_id != tenant_id:
                raise NotFoundError("Payment not found")
            if submission.state != ManualPaymentState.APPROVED:
                raise AppError(
                    "Only an approved payment can carry a pack",
                    code=ErrorCode.VOICE_PACK_PAYMENT_INVALID,
                    details={"reason": "NOT_APPROVED"},
                )
            used = await self._db.scalar(
                sa.select(VoiceCreditPack.id).where(
                    VoiceCreditPack.payment_submission_id == payment_submission_id
                )
            )
            if used is not None:
                raise AppError(
                    "This payment already carries a pack",
                    code=ErrorCode.VOICE_PACK_PAYMENT_INVALID,
                    details={"reason": "ALREADY_USED"},
                )
        at = now or utc_now()
        pack = VoiceCreditPack(
            tenant_id=tenant_id,
            calls=calls,
            remaining=calls,
            expires_at=at + timedelta(days=PACK_TTL_DAYS),
            source=str(source),
            grant_key=grant_key[:80],
            payment_submission_id=payment_submission_id,
            granted_by=granted_by[:120],
            reason=reason[:400],
        )
        savepoint = await self._db.begin_nested()
        try:
            self._db.add(pack)
            await savepoint.commit()
        except IntegrityError:
            # A racing grant with the same key or payment won.
            await savepoint.rollback()
            winner = await self._db.scalar(
                sa.select(VoiceCreditPack).where(
                    VoiceCreditPack.tenant_id == tenant_id, VoiceCreditPack.grant_key == grant_key
                )
            )
            if winner is None:
                raise AppError(
                    "This payment already carries a pack",
                    code=ErrorCode.VOICE_PACK_PAYMENT_INVALID,
                    details={"reason": "ALREADY_USED"},
                ) from None
            return self._same_grant(winner, calls, source, payment_submission_id), False
        return pack, True

    @staticmethod
    def _same_grant(
        pack: VoiceCreditPack,
        calls: int,
        source: PackSource,
        payment_submission_id: uuid.UUID | None,
    ) -> VoiceCreditPack:
        if (
            pack.calls != calls
            or pack.source != source
            or pack.payment_submission_id != payment_submission_id
        ):
            raise IdempotencyConflictError("This idempotency key granted a different pack")
        return pack


# --------------------------------------------------------------------------- #
# The admin report
# --------------------------------------------------------------------------- #


def call_cost(
    *,
    reported_paisa: int | None,
    duration_seconds: int | None,
    price_per_call_paisa: int | None,
    price_per_minute_paisa: int | None,
) -> tuple[int | None, bool]:
    """One call's cost in paisa and whether it is an estimate.

    The provider's own figure when it reported one; else the manifest price
    (per call, or per started minute of a known length), marked estimated;
    else unknown (``None``)."""
    if reported_paisa is not None:
        return reported_paisa, False
    if price_per_call_paisa is not None:
        return price_per_call_paisa, True
    if price_per_minute_paisa is not None and duration_seconds is not None:
        minutes = max(1, math.ceil(duration_seconds / 60))
        return price_per_minute_paisa * minutes, True
    return None, True


@dataclass(slots=True)
class VoiceReport:
    """One shop's calls on one Asia/Dhaka day."""

    tenant_id: uuid.UUID
    day: str
    calls: int = 0
    answered: int = 0
    confirmed: int = 0
    #: Sum over the calls whose cost is known.
    cost_paisa: int = 0
    #: Any part of ``cost_paisa`` came from the manifest price, not the provider.
    cost_estimated: bool = False
    calls_without_cost: int = 0
    shop_name: str = ""
    providers: set[str] = field(default_factory=set)

    @staticmethod
    def _bps(part: int, whole: int) -> int:
        return part * 10_000 // whole if whole else 0

    def as_dict(self) -> dict[str, Any]:
        known = self.calls - self.calls_without_cost
        return {
            "tenant_id": str(self.tenant_id),
            "shop_name": self.shop_name,
            "day": self.day,
            "calls": self.calls,
            "answered": self.answered,
            "confirmed": self.confirmed,
            "answer_rate_bps": self._bps(self.answered, self.calls),
            "confirm_rate_bps": self._bps(self.confirmed, self.calls),
            "cost_paisa": self.cost_paisa if known else None,
            "cost_per_call_paisa": self.cost_paisa // known if known else None,
            "cost_estimated": self.cost_estimated,
            "calls_without_cost": self.calls_without_cost,
            "providers": sorted(self.providers),
        }


async def voice_report(
    db: AsyncSession,
    *,
    day_from: date,
    day_to: date,
    tenant_id: uuid.UUID | None = None,
    prices: Mapping[str, tuple[int | None, int | None]] | None = None,
) -> list[VoiceReport]:
    """Charged calls per shop per day, from an admin (cross-shop) session.

    ``prices`` maps a provider to its manifest's (per call, per minute) price.
    """
    from app.confirmations.models import ConfirmationAttempt
    from app.tenants.models import Tenant

    if day_to < day_from:
        raise ValidationError("The report ends before it starts")
    if (day_to - day_from).days + 1 > MAX_REPORT_DAYS:
        raise ValidationError(f"A report covers at most {MAX_REPORT_DAYS} days")
    query = (
        sa.select(
            VoiceCallCharge.tenant_id,
            VoiceCallCharge.day,
            VoiceCallCharge.provider,
            ConfirmationAttempt.status,
            ConfirmationAttempt.duration_seconds,
            ConfirmationAttempt.cost_paisa,
            Tenant.name,
        )
        .join(ConfirmationAttempt, ConfirmationAttempt.id == VoiceCallCharge.attempt_id)
        .join(Tenant, Tenant.id == VoiceCallCharge.tenant_id)
        .where(
            VoiceCallCharge.state == str(ChargeState.CHARGED),
            VoiceCallCharge.day >= day_from.isoformat(),
            VoiceCallCharge.day <= day_to.isoformat(),
        )
    )
    if tenant_id is not None:
        query = query.where(VoiceCallCharge.tenant_id == tenant_id)
    rows: dict[tuple[uuid.UUID, str], VoiceReport] = {}
    for shop, day, provider, status, duration, reported, name in (await db.execute(query)).all():
        item = rows.get((shop, day))
        if item is None:
            item = rows[(shop, day)] = VoiceReport(shop, day, shop_name=name or "")
        item.calls += 1
        item.providers.add(provider)
        if status in _ANSWERED:
            item.answered += 1
        if status == "CONFIRMED":
            item.confirmed += 1
        per_call, per_minute = (prices or {}).get(provider, (None, None))
        cost, estimated = call_cost(
            reported_paisa=reported,
            duration_seconds=duration,
            price_per_call_paisa=per_call,
            price_per_minute_paisa=per_minute,
        )
        if cost is None:
            item.calls_without_cost += 1
        else:
            item.cost_paisa += cost
            item.cost_estimated = item.cost_estimated or estimated
    return sorted(rows.values(), key=lambda item: (item.day, item.shop_name, str(item.tenant_id)))
