"""Seller payment by bKash / Nagad TrxID (V4 WP2.5).

A seller who cannot buy a plan in the app sends the plan price to the
operator's own bKash or Nagad number and types the TrxID their wallet showed.
The submission waits in ``SUBMITTED`` until an operator finds that TrxID in the
receiving wallet and approves it; approval grants the plan through the manual
billing provider (:class:`~app.billing.providers.manual.AdminManualBillingProvider`,
via :meth:`~app.billing.service.BillingService.grant_manual_subscription`) with
an absolute period end that is fixed once and stored on the row.

What never happens:

*   nothing is granted on submission. A TrxID is a claim, not evidence;
*   no bKash or Nagad API is called. None is contracted, so the check is a
    person reading the receiving wallet (runbook ``MANUAL_PAYMENTS.md``);
*   the payer's number is stored masked only, and neither it nor the TrxID is
    written to the application log;
*   one TrxID pays twice: ``(method, trx_id)`` is unique across every shop and
    state, enforced by the database rather than a tenant-filtered read.

The form is offered only when ``manual_payments_enabled`` is on, Free Launch
Mode is off, the calling build may show an external payment option (never a
Play build, master spec section 27.1) and at least one receiving account is
configured in ``MANUAL_PAYMENT_ACCOUNTS``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import (
    BillingPaymentSubmission,
    DistributionChannel,
    ManualPaymentMethod,
    ManualPaymentState,
)
from app.common.audit import AuditAction, record_audit
from app.common.cache import RateLimiter
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.common.outbox import OutboxTopic, enqueue
from app.common.phone import normalize_digits, try_normalize_bd_phone
from app.core.clock import ensure_utc, utc_now
from app.core.config import Settings
from app.core.errors import (
    AppError,
    ConflictError,
    ErrorCode,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from app.entitlements.catalog import PlanCode, plan_catalog

__all__ = [
    "DAYS_PER_MONTH",
    "MAX_MONTHS",
    "MIN_MONTHS",
    "MONTH_CHOICES",
    "PAID_PLANS",
    "Availability",
    "ManualPaymentReview",
    "ManualPaymentService",
    "ReceivingAccount",
    "Unavailable",
    "amount_for",
    "normalise_trx_id",
    "period_end_for",
    "receiving_accounts",
]

MIN_MONTHS = 1
MAX_MONTHS = 12
#: A paid month is 30 days, whatever the calendar month.
DAYS_PER_MONTH = 30
PAID_PLANS: tuple[PlanCode, ...] = (PlanCode.STARTER, PlanCode.PRO)
#: What the screens offer (the API accepts any 1–12). Each comes with its exact
#: amount from the server: the client never computes money.
MONTH_CHOICES: tuple[int, ...] = (1, 3, 6, 12)

#: Per shop. Enough for typos; too few to probe which TrxIDs exist.
SUBMITS_PER_WINDOW = 10
SUBMIT_WINDOW_SECONDS = 3600

#: Letters and digits only. No exact wallet format is assumed (none is
#: documented here), just a sane length.
_TRX_RE = re.compile(r"^[A-Z0-9]{6,20}$")
_SEPARATORS = re.compile(r"[\s\-]+")


class Unavailable(StrEnum):
    FEATURE_OFF = "FEATURE_OFF"
    FREE_LAUNCH = "FREE_LAUNCH"
    CHANNEL = "CHANNEL"
    NO_ACCOUNTS = "NO_ACCOUNTS"


@dataclass(frozen=True, slots=True)
class ReceivingAccount:
    """One of the operator's own wallets a seller sends money to."""

    method: ManualPaymentMethod
    #: 11-digit national form, shown to the seller on purpose.
    number: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"method": str(self.method), "number": self.number, "label": self.label}


def receiving_accounts(settings: Settings) -> list[ReceivingAccount]:
    """``MANUAL_PAYMENT_ACCOUNTS``, keeping only entries that make sense."""
    accounts: list[ReceivingAccount] = []
    for entry in settings.manual_payment_accounts or []:
        if not isinstance(entry, dict):
            continue
        try:
            method = ManualPaymentMethod(str(entry.get("method") or "").strip().upper())
        except ValueError:
            continue
        phone = try_normalize_bd_phone(str(entry.get("number") or ""))
        if phone is None:
            continue
        label = str(entry.get("label") or "").strip()[:40]
        accounts.append(ReceivingAccount(method=method, number=phone.national, label=label))
    return accounts


def normalise_trx_id(raw: str | None) -> str:
    """Upper-case, Bangla digits folded, spaces and dashes dropped."""
    text = _SEPARATORS.sub("", normalize_digits(raw or "")).upper()
    if not _TRX_RE.match(text):
        raise ValidationError(
            "A TrxID is 6 to 20 letters and digits",
            message_bn="TrxID ৬ থেকে ২০টি ইংরেজি অক্ষর ও সংখ্যা হয়",
            details={"code": "TRX_ID_INVALID", "field": "trx_id"},
        )
    return text


def amount_for(plan: PlanCode, months: int) -> int:
    """Catalogue price × months, integer paisa."""
    return plan_catalog()[plan].price_paisa * months


def period_end_for(current_end: datetime | None, months: int, now: datetime) -> datetime:
    """From the later of now and the current paid end: paid time is never lost."""
    base = max(now, ensure_utc(current_end)) if current_end is not None else now
    return base + timedelta(days=DAYS_PER_MONTH * months)


def _paid_plan(raw: str) -> PlanCode:
    for plan in PAID_PLANS:
        if str(plan) == str(raw).strip().lower():
            return plan
    raise ValidationError(
        "Choose a paid plan",
        message_bn="একটি পেইড প্ল্যান বেছে নিন",
        details={"code": "PLAN_NOT_PAYABLE", "field": "plan_code"},
    )


def _months(value: int) -> int:
    if not MIN_MONTHS <= value <= MAX_MONTHS:
        raise ValidationError(
            f"Months must be between {MIN_MONTHS} and {MAX_MONTHS}",
            message_bn="মাস ১ থেকে ১২ এর মধ্যে হতে হবে",
            details={"code": "MONTHS_OUT_OF_RANGE", "field": "months"},
        )
    return value


@dataclass(frozen=True, slots=True)
class Availability:
    available: bool
    reason: Unavailable | None
    accounts: tuple[ReceivingAccount, ...] = ()


def _already_used() -> ConflictError:
    return ConflictError(
        "This TrxID has already been submitted",
        message_bn="এই TrxID আগেই জমা দেওয়া হয়েছে",
        details={"code": "TRX_ID_ALREADY_USED", "field": "trx_id"},
    )


class ManualPaymentService:
    """The seller's side. Runs in the shop's tenant-scoped session."""

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
        if not await self._flags.is_enabled(FlagKey.MANUAL_PAYMENTS_ENABLED, tenant_id=tenant_id):
            return Availability(False, Unavailable.FEATURE_OFF)
        if self._settings.free_launch_mode:
            return Availability(False, Unavailable.FREE_LAUNCH)
        if not channel.allows_external_payment_cta:
            return Availability(False, Unavailable.CHANNEL)
        accounts = tuple(receiving_accounts(self._settings))
        if not accounts:
            return Availability(False, Unavailable.NO_ACCOUNTS)
        return Availability(True, None, accounts)

    async def submit(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        channel: DistributionChannel,
        plan_code: str,
        months: int,
        method: str,
        payer_number: str,
        trx_id: str,
    ) -> tuple[BillingPaymentSubmission, bool]:
        """Record the claim. Returns ``(row, created)``.

        The same shop sending the same TrxID for the same plan again (a retry
        after a dropped connection) gets its first row back; anything else
        with a used TrxID is refused.
        """
        availability = await self.availability(tenant_id, channel)
        if not availability.available:
            raise AppError(
                "Paying by bKash or Nagad is not available",
                code=ErrorCode.FEATURE_DISABLED,
                message_bn="এখন বিকাশ বা নগদে পেমেন্ট নেওয়া যাচ্ছে না",
                details={
                    "code": "MANUAL_PAYMENT_UNAVAILABLE",
                    "reason": str(availability.reason),
                },
            )
        plan = _paid_plan(plan_code)
        months = _months(months)
        try:
            chosen = ManualPaymentMethod(str(method).strip().upper())
        except ValueError:
            chosen = None
        if chosen is None or all(a.method is not chosen for a in availability.accounts):
            raise ValidationError(
                "Choose bKash or Nagad",
                message_bn="বিকাশ বা নগদ বেছে নিন",
                details={"code": "METHOD_NOT_OFFERED", "field": "method"},
            )
        payer = try_normalize_bd_phone(payer_number)
        if payer is None:
            raise ValidationError(
                "Not a valid Bangladeshi mobile number",
                code=ErrorCode.INVALID_PHONE_NUMBER,
                details={"field": "payer_number"},
            )
        trx = normalise_trx_id(trx_id)
        amount = amount_for(plan, months)

        mine = (
            await self._db.execute(
                sa.select(BillingPaymentSubmission).where(
                    BillingPaymentSubmission.tenant_id == tenant_id,
                    BillingPaymentSubmission.method == str(chosen),
                    BillingPaymentSubmission.trx_id == trx,
                )
            )
        ).scalar_one_or_none()
        if mine is not None:
            if (mine.plan_code, mine.months, mine.amount_paisa) == (str(plan), months, amount):
                return mine, False
            raise _already_used()

        limited = await (self._limiter or RateLimiter()).hit(
            "manual-payment-submit",
            str(tenant_id),
            limit=SUBMITS_PER_WINDOW,
            window_seconds=SUBMIT_WINDOW_SECONDS,
        )
        if not limited.allowed:
            raise RateLimitedError(retry_after_seconds=limited.retry_after_seconds)

        # Created inside the savepoint, so a refused insert leaves nothing
        # pending for the next flush (see BillingService._record_transaction).
        savepoint = await self._db.begin_nested()
        try:
            row = BillingPaymentSubmission(
                tenant_id=tenant_id,
                plan_code=str(plan),
                months=months,
                amount_paisa=amount,
                method=str(chosen),
                payer_number_masked=payer.masked,
                trx_id=trx,
                state=str(ManualPaymentState.SUBMITTED),
                submitted_by=actor_id,
            )
            self._db.add(row)
            await self._db.flush()
            await savepoint.commit()
        except IntegrityError:
            await savepoint.rollback()
            # Another shop's row: this read cannot see it, the constraint can.
            raise _already_used() from None

        await record_audit(
            self._db,
            AuditAction.BILLING_MANUAL_PAYMENT_SUBMITTED,
            entity_type="billing_payment_submission",
            entity_id=row.id,
            context={
                "plan": str(plan),
                "months": months,
                "amount_paisa": amount,
                "method": str(chosen),
            },
            tenant_id=tenant_id,
        )
        return row, True

    async def list_mine(
        self, tenant_id: uuid.UUID, *, limit: int = 20
    ) -> list[BillingPaymentSubmission]:
        rows = await self._db.scalars(
            sa.select(BillingPaymentSubmission)
            .where(BillingPaymentSubmission.tenant_id == tenant_id)
            .order_by(BillingPaymentSubmission.created_at.desc())
            .limit(limit)
        )
        return list(rows.all())


class ManualPaymentReview:
    """The operator's side. Runs in the admin console's unscoped session, so
    every query names its row or tenant."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings,
        registry: Any,
        admin_id: uuid.UUID | None,
        admin_label: str,
    ) -> None:
        self._db = db
        self._settings = settings
        self._registry = registry
        self._admin_id = admin_id
        self._admin_label = admin_label

    async def list(
        self,
        *,
        state: ManualPaymentState | None = None,
        tenant_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[BillingPaymentSubmission]:
        stmt = sa.select(BillingPaymentSubmission)
        if state is not None:
            stmt = stmt.where(BillingPaymentSubmission.state == str(state))
        if tenant_id is not None:
            stmt = stmt.where(BillingPaymentSubmission.tenant_id == tenant_id)
        # Oldest waiting first: a seller who paid yesterday is served before today.
        order = (
            BillingPaymentSubmission.created_at.asc()
            if state is ManualPaymentState.SUBMITTED
            else BillingPaymentSubmission.created_at.desc()
        )
        return list((await self._db.scalars(stmt.order_by(order).limit(limit))).all())

    async def _locked(self, submission_id: uuid.UUID) -> BillingPaymentSubmission:
        row = (
            await self._db.execute(
                sa.select(BillingPaymentSubmission)
                .where(BillingPaymentSubmission.id == submission_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("No such payment submission")
        return row

    @staticmethod
    def _already_reviewed(row: BillingPaymentSubmission) -> ConflictError:
        return ConflictError(
            f"This payment was already {row.state.lower()}",
            message_bn="এই পেমেন্ট আগেই যাচাই করা হয়েছে",
            details={"code": "PAYMENT_ALREADY_REVIEWED", "state": row.state},
        )

    async def approve(
        self, submission_id: uuid.UUID, *, note: str | None = None
    ) -> tuple[BillingPaymentSubmission, bool]:
        """Grant the plan once. A repeat returns the stored result unchanged."""
        from app.billing.service import BillingService

        row = await self._locked(submission_id)
        if row.state == ManualPaymentState.APPROVED:
            return row, False
        if row.state != ManualPaymentState.SUBMITTED:
            raise self._already_reviewed(row)

        plan = PlanCode(row.plan_code)
        billing = BillingService(self._db, settings=self._settings, registry=self._registry)
        current = await billing.current_subscription(row.tenant_id)
        now = utc_now()
        period_end = period_end_for(
            current.current_period_end if current is not None else None, row.months, now
        )
        note = (note or "").strip()[:300] or None
        reason = f"{row.method} TrxID {row.trx_id}: {plan} × {row.months} month(s)"
        subscription = await billing.grant_manual_subscription(
            tenant_id=row.tenant_id,
            plan=plan,
            until=period_end,
            reason=reason + (f" ({note})" if note else ""),
            actor_id=self._admin_id,
            actor_label=self._admin_label,
            amount_paisa=row.amount_paisa,
            provider_reference=f"{row.method}:{row.trx_id}",
            provider_event_id=f"manual_payment:{row.id}",
        )
        row.state = str(ManualPaymentState.APPROVED)
        row.reviewed_by = self._admin_label[:120]
        row.reviewed_at = now
        row.reason = note
        row.period_end = period_end
        row.subscription_id = subscription.id
        await self._reviewed(row, AuditAction.BILLING_MANUAL_PAYMENT_APPROVED)
        return row, True

    async def reject(
        self, submission_id: uuid.UUID, *, reason: str
    ) -> tuple[BillingPaymentSubmission, bool]:
        """Refuse it with a reason the seller sees. A repeat changes nothing."""
        text = (reason or "").strip()
        if len(text) < 3:
            raise ValidationError(
                "Say why the payment is rejected", details={"code": "REASON_REQUIRED"}
            )
        row = await self._locked(submission_id)
        if row.state == ManualPaymentState.REJECTED:
            return row, False
        if row.state != ManualPaymentState.SUBMITTED:
            raise self._already_reviewed(row)
        row.state = str(ManualPaymentState.REJECTED)
        row.reviewed_by = self._admin_label[:120]
        row.reviewed_at = utc_now()
        row.reason = text[:400]
        await self._reviewed(row, AuditAction.BILLING_MANUAL_PAYMENT_REJECTED)
        return row, True

    async def _reviewed(self, row: BillingPaymentSubmission, action: AuditAction) -> None:
        await self._db.flush()
        await record_audit(
            self._db,
            action,
            entity_type="billing_payment_submission",
            entity_id=row.id,
            reason=row.reason,
            context={
                "plan": row.plan_code,
                "months": row.months,
                "amount_paisa": row.amount_paisa,
                "method": row.method,
                "admin": self._admin_label,
                "period_end": row.period_end.isoformat() if row.period_end else None,
            },
            tenant_id=row.tenant_id,
        )
        # The seller's notification, in their own tenant scope (app.billing.jobs).
        await enqueue(
            self._db,
            OutboxTopic.BILLING_MANUAL_PAYMENT_REVIEWED,
            {"submission_id": str(row.id), "state": row.state},
            tenant_id=row.tenant_id,
            dedupe_key=f"manual_payment:{row.id}:{row.state}",
        )
