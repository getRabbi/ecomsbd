"""Subscription lifecycle and provider verification.

Master spec sections 90–93. This is the only module that writes
``subscriptions``, and it is the only place a paid plan can be granted.

The invariant it exists to hold:

    **A client claim is never evidence.**

``POST /v1/billing/play/verify`` does not grant anything. It records a claim,
asks the provider, and grants only if the provider's answer says the purchase is
real, belongs to this build, maps to a plan we sell, and has not already been
spent by someone else. Every one of those four checks can refuse, and each
refusal is written down with its reason.

Replay protection is worth stating precisely, because it is where a billing bug
becomes free money:

*   a purchase token is stored **hashed and globally unique** — presenting it
    twice for the same tenant is idempotent, presenting it for a different
    tenant is refused and audited;
*   a webhook is deduplicated on provider event id, then provider reference,
    then payload fingerprint (section 78), and a replay updates a delivery
    counter rather than applying its events a second time;
*   a period extension is computed from the **provider's** expiry, never by
    adding a month to whatever is currently stored, so two deliveries of one
    renewal cannot buy two months.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import (
    BillingAttempt,
    BillingAttemptState,
    BillingEventType,
    BillingProviderCustomer,
    BillingProviderKind,
    BillingTransaction,
    BillingWebhookEvent,
    DistributionChannel,
    PlayPurchaseToken,
    SubscriptionEvent,
    TransactionKind,
    TransactionState,
    WebhookProcessingState,
    hash_provider_token,
)
from app.billing.providers.base import (
    BillingEvent,
    CheckoutSession,
    VerificationResult,
    VerifiedPurchase,
)
from app.billing.providers.registry import BillingProviderRegistry
from app.common.audit import AuditAction, record_audit
from app.core.clock import ensure_utc, utc_now
from app.core.config import Settings
from app.core.errors import BillingVerificationError, ConflictError, NotFoundError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.redaction import redact_value
from app.db.tenancy import allow_cross_tenant
from app.entitlements.catalog import PlanCode
from app.entitlements.models import Subscription, SubscriptionStatus

__all__ = ["BillingService", "PurchaseOutcome"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PurchaseOutcome:
    """What a verification attempt did."""

    granted: bool
    result: VerificationResult
    subscription: Subscription | None
    transaction: BillingTransaction | None
    detail: str | None = None
    #: True when the same token was already spent by this tenant. The API
    #: returns success: a retried request must not look like a failure.
    replayed: bool = False


class BillingService:
    """Everything that changes a subscription."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        registry: BillingProviderRegistry,
    ) -> None:
        self._db = session
        self._settings = settings
        self._registry = registry

    # ===================================================================== #
    # Subscription access
    # ===================================================================== #

    async def current_subscription(self, tenant_id: uuid.UUID) -> Subscription | None:
        return (
            await self._db.execute(
                sa.select(Subscription)
                .where(Subscription.tenant_id == tenant_id)
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _subscription_or_new(
        self, tenant_id: uuid.UUID, *, provider: BillingProviderKind
    ) -> Subscription:
        """The tenant's subscription row, created on the Free plan if absent.

        A shop always has a subscription row once billing touches it, so support
        can see "Free, never purchased" rather than nothing at all.
        """
        existing = await self.current_subscription(tenant_id)
        if existing is not None:
            return existing

        subscription = Subscription(
            tenant_id=tenant_id,
            plan_code=str(PlanCode.FREE),
            status=str(SubscriptionStatus.ACTIVE),
            source=str(provider),
            distribution_channel=self._settings.distribution_channel,
        )
        self._db.add(subscription)
        await self._db.flush()
        return subscription

    # ===================================================================== #
    # Google Play
    # ===================================================================== #

    async def verify_play_purchase(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        purchase_token: str,
        product_id: str,
        package_name: str | None = None,
        channel: DistributionChannel = DistributionChannel.PLAY,
    ) -> PurchaseOutcome:
        """Turn a Play purchase token into an entitlement, or refuse.

        Order of operations is deliberate:

        1.  **Replay first, before touching the provider.** A token already
            bound to another tenant is refused without a network call, so a
            replay attempt cannot be used to probe Play.
        2.  Ask the provider.
        3.  Bind the token, write the transaction, move the subscription — all
            in the caller's transaction, so a crash cannot leave a granted
            entitlement with no record of why.
        """
        token_hash = hash_provider_token(purchase_token)
        existing = await self._find_play_token(token_hash)

        if existing is not None and existing.tenant_id != tenant_id:
            # The single most important refusal in this module.
            await record_audit(
                self._db,
                AuditAction.BILLING_REPLAY_BLOCKED,
                entity_type="play_purchase_token",
                entity_id=existing.id,
                context={
                    "reason": "token_bound_to_other_tenant",
                    "product_id": product_id,
                },
                tenant_id=tenant_id,
            )
            transaction = await self._record_transaction(
                tenant_id=tenant_id,
                subscription=None,
                provider=BillingProviderKind.PLAY,
                plan=PlanCode.FREE,
                kind=TransactionKind.PURCHASE,
                state=TransactionState.FAILED,
                result=VerificationResult.WRONG_TENANT,
                detail="This purchase is already attached to another shop",
                provider_product_id=product_id,
                provider_reference=token_hash,
            )
            await self._commit_refusal()
            raise BillingVerificationError(
                str(VerificationResult.WRONG_TENANT),
                "This purchase is already attached to another shop",
                details={"transaction_id": str(transaction.id)},
            )

        provider = self._registry.get(BillingProviderKind.PLAY)
        verified = await provider.verify_purchase(
            {
                "purchase_token": purchase_token,
                "product_id": product_id,
                "package_name": package_name or self._settings.play_package_name or "",
            }
        )

        if not verified.verified:
            await self._record_refusal(tenant_id, verified, product_id, token_hash)
            if existing is not None and verified.result is VerificationResult.UNAVAILABLE:
                # We already know this token; a provider outage must not revoke
                # what a previous successful verification granted.
                return PurchaseOutcome(
                    granted=False,
                    result=verified.result,
                    subscription=await self.current_subscription(tenant_id),
                    transaction=None,
                    detail="The provider is unreachable; existing access is unchanged.",
                )
            raise BillingVerificationError(
                str(verified.result),
                verified.detail or "The purchase could not be verified",
            )

        assert verified.plan is not None  # noqa: S101 - VERIFIED implies a plan

        replayed = existing is not None
        token_row = await self._bind_play_token(
            existing,
            token_hash=token_hash,
            tenant_id=tenant_id,
            user_id=user_id,
            product_id=product_id,
            package_name=package_name or self._settings.play_package_name or "",
            verified=verified,
        )

        subscription = await self._apply_verified_purchase(
            tenant_id=tenant_id,
            verified=verified,
            provider=BillingProviderKind.PLAY,
            channel=channel,
        )
        transaction = await self._record_transaction(
            tenant_id=tenant_id,
            subscription=subscription,
            provider=BillingProviderKind.PLAY,
            plan=verified.plan,
            kind=TransactionKind.RENEWAL if replayed else TransactionKind.PURCHASE,
            state=TransactionState.VERIFIED,
            result=VerificationResult.VERIFIED,
            detail=verified.detail,
            provider_product_id=product_id,
            provider_reference=token_hash,
            provider_event_id=verified.provider_event_id,
            amount_paisa=verified.amount_paisa,
            currency=verified.currency,
            metadata=verified.metadata,
        )

        if verified.acknowledgement_required:
            await self._acknowledge_play(purchase_token, token_row)

        await record_audit(
            self._db,
            AuditAction.BILLING_PURCHASE_VERIFIED,
            entity_type="subscription",
            entity_id=subscription.id,
            context={
                "provider": str(BillingProviderKind.PLAY),
                "plan": str(verified.plan),
                "product_id": product_id,
                "replayed": replayed,
            },
            tenant_id=tenant_id,
        )
        return PurchaseOutcome(
            granted=True,
            result=VerificationResult.VERIFIED,
            subscription=subscription,
            transaction=transaction,
            replayed=replayed,
        )

    async def _find_play_token(self, token_hash: str) -> PlayPurchaseToken | None:
        """Look up a purchase token across every tenant.

        ``play_purchase_tokens`` is not tenant-owned, but the ORM read filter
        keys off the model, so this needs no bypass. The explicit context
        manager is here because the *intent* is cross-tenant and a future reader
        must not "fix" it by adding a tenant filter — that would reintroduce the
        double-grant bug.
        """
        with allow_cross_tenant("billing: purchase token replay check is global by design"):
            return (
                await self._db.execute(
                    sa.select(PlayPurchaseToken).where(PlayPurchaseToken.token_hash == token_hash)
                )
            ).scalar_one_or_none()

    async def _bind_play_token(
        self,
        existing: PlayPurchaseToken | None,
        *,
        token_hash: str,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        product_id: str,
        package_name: str,
        verified: VerifiedPurchase,
    ) -> PlayPurchaseToken:
        now = utc_now()
        if existing is not None:
            existing.presentation_count += 1
            existing.last_verified_at = now
            existing.purchase_state = verified.purchase_state
            existing.expiry_at = verified.expiry_at
            existing.auto_renewing = verified.auto_renewing
            if verified.provider_event_id:
                existing.provider_order_id = verified.provider_event_id
            return existing

        row = PlayPurchaseToken(
            token_hash=token_hash,
            tenant_id=tenant_id,
            user_id=user_id,
            package_name=package_name,
            product_id=product_id,
            plan_code=str(verified.plan or PlanCode.FREE),
            purchase_state=verified.purchase_state,
            provider_order_id=verified.provider_event_id,
            expiry_at=verified.expiry_at,
            auto_renewing=verified.auto_renewing,
            first_verified_at=now,
            last_verified_at=now,
        )
        savepoint = await self._db.begin_nested()
        try:
            self._db.add(row)
            await self._db.flush()
            await savepoint.commit()
        except IntegrityError as exc:
            # Two concurrent verifications of the same token. The unique
            # constraint decides; the loser rolls back only its savepoint —
            # rolling back the request would discard the audit trail with it.
            await savepoint.rollback()
            raise ConflictError("This purchase is already being processed") from exc
        return row

    async def _acknowledge_play(self, token: str, row: PlayPurchaseToken) -> None:
        """Acknowledge a purchase where the provider requires it.

        Failure is logged, not raised. The seller has paid and the entitlement
        is granted; an acknowledgement problem is an operations issue to chase,
        not a reason to fail the request in front of them.
        """
        provider = self._registry.get(BillingProviderKind.PLAY)
        acknowledge = getattr(provider, "acknowledge", None)
        if acknowledge is None:
            return
        try:
            await acknowledge(token)
        except Exception as exc:
            log.warning("play acknowledgement failed", extra={"error": type(exc).__name__})
            return
        row.acknowledged = True
        row.acknowledged_at = utc_now()

    async def restore_purchases(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        purchases: list[dict[str, str]],
        channel: DistributionChannel = DistributionChannel.PLAY,
    ) -> list[PurchaseOutcome]:
        """Re-verify purchases the client still holds (master spec section 90).

        Used after a reinstall or a sign-in on a new device. Each purchase goes
        through the same path as a fresh one, so a restore cannot grant anything
        a purchase could not — and a purchase belonging to another shop is
        refused here too, which is the case that matters.
        """
        outcomes: list[PurchaseOutcome] = []
        for purchase in purchases:
            token = purchase.get("purchase_token", "")
            product_id = purchase.get("product_id", "")
            if not token or not product_id:
                continue
            try:
                outcomes.append(
                    await self.verify_play_purchase(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        purchase_token=token,
                        product_id=product_id,
                        package_name=purchase.get("package_name"),
                        channel=channel,
                    )
                )
            except BillingVerificationError as exc:
                outcomes.append(
                    PurchaseOutcome(
                        granted=False,
                        result=VerificationResult(
                            str(exc.details["verification_result"]) if exc.details else "REJECTED"
                        ),
                        subscription=None,
                        transaction=None,
                        detail=exc.message_en,
                    )
                )
        return outcomes

    # ===================================================================== #
    # Web / direct checkout
    # ===================================================================== #

    async def start_checkout(
        self,
        *,
        tenant_id: uuid.UUID,
        plan: PlanCode,
        provider_kind: BillingProviderKind,
    ) -> CheckoutSession:
        """Begin a purchase at a provider, recording our reference first.

        The reference is minted and written before the provider is called, so a
        callback that arrives while the response is still in flight has
        something to attach itself to.
        """
        if not self._settings.purchases_enabled:
            from app.core.errors import ServiceUnavailableError

            raise ServiceUnavailableError(
                "Plan purchases are currently unavailable.",
                message_bn="প্ল্যান কেনা বর্তমানে বন্ধ আছে।",
                details={"reason": "billing_disabled"},
            )
        provider = self._registry.get(provider_kind)
        reference = f"ecomsbd-{new_id().hex}"

        session = await provider.create_checkout(
            tenant_id=tenant_id, plan=plan, reference=reference
        )
        await self._record_transaction(
            tenant_id=tenant_id,
            subscription=await self.current_subscription(tenant_id),
            provider=provider_kind,
            plan=plan,
            kind=TransactionKind.PURCHASE,
            state=TransactionState.PENDING,
            result=VerificationResult.UNAVAILABLE,
            detail="Checkout started; awaiting provider confirmation",
            provider_reference=session.checkout_reference,
            amount_paisa=session.amount_paisa,
            currency=session.currency,
        )
        await record_audit(
            self._db,
            AuditAction.BILLING_CHECKOUT_STARTED,
            entity_type="tenant",
            entity_id=tenant_id,
            context={"provider": str(provider_kind), "plan": str(plan), "reference": reference},
            tenant_id=tenant_id,
        )
        return session

    async def confirm_checkout(
        self,
        *,
        tenant_id: uuid.UUID,
        provider_kind: BillingProviderKind,
        reference: str,
        channel: DistributionChannel = DistributionChannel.WEB,
    ) -> PurchaseOutcome:
        """Verify a web payment by asking the provider about our own reference."""
        provider = self._registry.get(provider_kind)
        verified = await provider.verify_purchase({"reference": reference})

        if not verified.verified:
            await self._record_refusal(tenant_id, verified, None, reference)
            raise BillingVerificationError(
                str(verified.result), verified.detail or "The payment could not be verified"
            )

        subscription = await self._apply_verified_purchase(
            tenant_id=tenant_id, verified=verified, provider=provider_kind, channel=channel
        )
        transaction = await self._record_transaction(
            tenant_id=tenant_id,
            subscription=subscription,
            provider=provider_kind,
            plan=verified.plan or PlanCode.FREE,
            kind=TransactionKind.PURCHASE,
            state=TransactionState.VERIFIED,
            result=VerificationResult.VERIFIED,
            detail=verified.detail,
            provider_reference=verified.provider_reference or reference,
            provider_event_id=verified.provider_event_id,
            amount_paisa=verified.amount_paisa,
            currency=verified.currency,
            metadata=verified.metadata,
        )
        if verified.account_reference:
            await self._link_provider_customer(tenant_id, provider_kind, verified.account_reference)
        return PurchaseOutcome(
            granted=True,
            result=VerificationResult.VERIFIED,
            subscription=subscription,
            transaction=transaction,
        )

    # ===================================================================== #
    # Subscription state machine
    # ===================================================================== #

    async def _apply_verified_purchase(
        self,
        *,
        tenant_id: uuid.UUID,
        verified: VerifiedPurchase,
        provider: BillingProviderKind,
        channel: DistributionChannel,
    ) -> Subscription:
        """Move a subscription to ACTIVE on the strength of provider evidence.

        The period end comes from the provider. If the provider did not say when
        the purchase expires, the period is left open rather than invented: an
        invented expiry either cuts a paying seller off early or gives away time
        nobody paid for.
        """
        subscription = await self._subscription_or_new(tenant_id, provider=provider)
        previous_status = subscription.status
        previous_plan = subscription.plan_code
        now = utc_now()

        subscription.plan_code = str(verified.plan or PlanCode.FREE)
        subscription.status = str(SubscriptionStatus.ACTIVE)
        subscription.source = str(provider)
        subscription.provider_reference = verified.provider_reference
        subscription.provider_product_id = verified.provider_product_id
        subscription.distribution_channel = str(channel)
        subscription.current_period_start = verified.starts_at or (
            subscription.current_period_start or now
        )
        subscription.current_period_end = verified.expiry_at
        subscription.cancel_at_period_end = not verified.auto_renewing and bool(verified.expiry_at)
        subscription.grace_until = None
        subscription.cancelled_at = None
        subscription.verified_at = now
        subscription.last_synced_at = now
        subscription.status_reason = "Verified with the billing provider"

        event_type = (
            BillingEventType.PLAN_CHANGED
            if previous_plan != subscription.plan_code
            and previous_status == str(SubscriptionStatus.ACTIVE)
            else BillingEventType.SUBSCRIPTION_STARTED
        )
        await self._record_event(
            subscription,
            event_type=event_type,
            from_status=previous_status,
            from_plan=previous_plan,
            provider=provider,
            provider_event_id=verified.provider_event_id,
            reason=verified.detail,
        )
        await self._resolve_open_attempts(subscription, recovered=True)
        return subscription

    async def cancel_subscription(
        self,
        *,
        tenant_id: uuid.UUID,
        at_period_end: bool = True,
        reason: str | None = None,
    ) -> Subscription:
        """Cancel, keeping paid-for access to the end of the period.

        Section 26's spirit: the seller stops paying, they do not stop having
        what they already paid for. Immediate cancellation is available but is
        an admin path, not a seller-facing one.
        """
        subscription = await self.current_subscription(tenant_id)
        if subscription is None:
            raise NotFoundError("This shop has no subscription to cancel")

        provider_kind = BillingProviderKind(subscription.source)
        provider = self._registry.get(provider_kind)
        result = await provider.cancel_subscription(
            provider_reference=subscription.provider_reference or "",
            at_period_end=at_period_end,
        )

        previous = subscription.status
        now = utc_now()
        if at_period_end and subscription.current_period_end is not None:
            subscription.status = str(SubscriptionStatus.CANCEL_AT_PERIOD_END)
            subscription.cancel_at_period_end = True
        else:
            subscription.status = str(SubscriptionStatus.CANCELLED)
            subscription.current_period_end = subscription.current_period_end or now
        subscription.cancelled_at = now
        subscription.status_reason = reason or result.detail or "Cancelled by the shop owner"

        await self._record_event(
            subscription,
            event_type=BillingEventType.SUBSCRIPTION_CANCELLED,
            from_status=previous,
            provider=provider_kind,
            reason=subscription.status_reason,
        )
        await record_audit(
            self._db,
            AuditAction.SUBSCRIPTION_CANCELLED,
            entity_type="subscription",
            entity_id=subscription.id,
            reason=reason,
            context={"provider": str(provider_kind), "at_period_end": at_period_end},
            tenant_id=tenant_id,
        )
        return subscription

    async def record_payment_failure(
        self,
        *,
        subscription: Subscription,
        provider: BillingProviderKind,
        failure_code: str | None,
        detail: str | None = None,
        amount_paisa: int = 0,
    ) -> BillingAttempt:
        """Open or advance a dunning cycle (master spec section 91).

        Grace is granted from *now*, not from the period end, and only once per
        cycle: a provider that reports the same failure three times must not
        extend the seller's grace to nine days.
        """
        now = utc_now()
        attempts = await self._open_attempts(subscription)
        attempt_number = len(attempts) + 1
        max_retries = self._settings.billing_max_payment_retries

        attempt = BillingAttempt(
            tenant_id=subscription.tenant_id,
            subscription_id=subscription.id,
            provider=str(provider),
            attempt_number=attempt_number,
            state=str(
                BillingAttemptState.RETRY_SCHEDULED
                if attempt_number < max_retries
                else BillingAttemptState.FAILED
            ),
            failure_code=failure_code,
            failure_detail=detail,
            amount_paisa=amount_paisa,
            occurred_at=now,
            next_retry_at=(
                now + timedelta(hours=self._settings.billing_retry_interval_hours)
                if attempt_number < max_retries
                else None
            ),
        )
        self._db.add(attempt)
        # The session runs with autoflush off, so without this the next call's
        # attempt count would still read the previous value and the retry
        # counter would never advance past one.
        await self._db.flush()

        previous = subscription.status
        if attempt_number >= max_retries:
            subscription.status = str(SubscriptionStatus.PAST_DUE)
            subscription.grace_until = None
            subscription.status_reason = f"Payment failed {attempt_number} times"
            await self._record_event(
                subscription,
                event_type=BillingEventType.GRACE_ENDED,
                from_status=previous,
                provider=provider,
                reason=subscription.status_reason,
            )
        else:
            subscription.status = str(SubscriptionStatus.GRACE)
            if subscription.grace_until is None:
                subscription.grace_until = now + timedelta(
                    days=self._settings.billing_grace_period_days
                )
                await self._record_event(
                    subscription,
                    event_type=BillingEventType.GRACE_STARTED,
                    from_status=previous,
                    provider=provider,
                    reason=f"Payment failed ({failure_code or 'unknown'})",
                )
            subscription.status_reason = (
                f"Payment failed; retrying until {subscription.grace_until:%Y-%m-%d}"
            )

        await self._record_event(
            subscription,
            event_type=BillingEventType.PAYMENT_FAILED,
            from_status=previous,
            provider=provider,
            reason=failure_code,
        )
        return attempt

    async def record_payment_recovery(
        self, *, subscription: Subscription, provider: BillingProviderKind
    ) -> None:
        """A retry succeeded: close the dunning cycle."""
        previous = subscription.status
        subscription.status = str(SubscriptionStatus.ACTIVE)
        subscription.grace_until = None
        subscription.status_reason = "Payment recovered"
        await self._resolve_open_attempts(subscription, recovered=True)
        await self._record_event(
            subscription,
            event_type=BillingEventType.PAYMENT_RECOVERED,
            from_status=previous,
            provider=provider,
        )

    async def expire_subscription(
        self, *, subscription: Subscription, reason: str, provider: BillingProviderKind
    ) -> None:
        previous = subscription.status
        subscription.status = str(
            SubscriptionStatus.CANCELLED
            if previous == str(SubscriptionStatus.CANCEL_AT_PERIOD_END)
            else SubscriptionStatus.EXPIRED
        )
        subscription.grace_until = None
        subscription.status_reason = reason
        await self._resolve_open_attempts(subscription, recovered=False)
        await self._record_event(
            subscription,
            event_type=BillingEventType.SUBSCRIPTION_EXPIRED,
            from_status=previous,
            provider=provider,
            reason=reason,
        )

    async def refund_subscription(
        self,
        *,
        subscription: Subscription,
        provider: BillingProviderKind,
        amount_paisa: int,
        reason: str,
        provider_event_id: str | None = None,
    ) -> BillingTransaction:
        """Record a refund and revoke access immediately.

        A refund is a new transaction pointing at the same subscription, never
        an edit of the original — the same rule the financial ledger follows in
        section 80.
        """
        previous = subscription.status
        subscription.status = str(SubscriptionStatus.REFUNDED)
        subscription.grace_until = None
        subscription.current_period_end = utc_now()
        subscription.status_reason = reason

        await self._record_event(
            subscription,
            event_type=BillingEventType.REFUNDED,
            from_status=previous,
            provider=provider,
            reason=reason,
        )
        return await self._record_transaction(
            tenant_id=subscription.tenant_id,
            subscription=subscription,
            provider=provider,
            plan=PlanCode(subscription.plan_code),
            kind=TransactionKind.REFUND,
            state=TransactionState.REFUNDED,
            result=VerificationResult.VERIFIED,
            detail=reason,
            provider_reference=subscription.provider_reference,
            provider_event_id=provider_event_id,
            amount_paisa=amount_paisa,
        )

    # ===================================================================== #
    # Admin / support grants
    # ===================================================================== #

    async def grant_manual_subscription(
        self,
        *,
        tenant_id: uuid.UUID,
        plan: PlanCode,
        days: int,
        reason: str,
        actor_id: uuid.UUID | None,
        actor_label: str | None = None,
    ) -> Subscription:
        """Issue support credit (master spec section 103).

        Requires a reason, records who granted it, and is audited. The manual
        provider is not reachable from any seller-facing route, so this is the
        only way a subscription can be created without provider evidence.
        """
        if days <= 0:
            raise ValueError("a manual grant must last at least one day")
        if not reason.strip():
            raise ValueError("a manual grant requires a reason")

        subscription = await self._subscription_or_new(
            tenant_id, provider=BillingProviderKind.MANUAL_ADMIN
        )
        previous_status, previous_plan = subscription.status, subscription.plan_code
        now = utc_now()
        # Extend from whichever is later: an existing paid period is not
        # shortened by a support credit added on top of it.
        base = max(now, subscription.current_period_end or now)

        subscription.plan_code = str(plan)
        subscription.status = str(SubscriptionStatus.ACTIVE)
        subscription.source = str(BillingProviderKind.MANUAL_ADMIN)
        subscription.current_period_start = subscription.current_period_start or now
        subscription.current_period_end = base + timedelta(days=days)
        subscription.grace_until = None
        subscription.cancel_at_period_end = True  # credit does not auto-renew
        subscription.verified_at = now
        subscription.last_synced_at = now
        subscription.granted_by_user_id = actor_id
        subscription.grant_reason = reason
        subscription.status_reason = f"Support credit: {reason}"

        await self._record_event(
            subscription,
            event_type=BillingEventType.SUBSCRIPTION_STARTED,
            from_status=previous_status,
            from_plan=previous_plan,
            provider=BillingProviderKind.MANUAL_ADMIN,
            reason=reason,
        )
        await self._record_transaction(
            tenant_id=tenant_id,
            subscription=subscription,
            provider=BillingProviderKind.MANUAL_ADMIN,
            plan=plan,
            kind=TransactionKind.CREDIT,
            state=TransactionState.VERIFIED,
            result=VerificationResult.VERIFIED,
            detail=reason,
            amount_paisa=0,
        )
        await record_audit(
            self._db,
            AuditAction.BILLING_MANUAL_GRANT,
            entity_type="subscription",
            entity_id=subscription.id,
            reason=reason,
            context={
                "plan": str(plan),
                "days": days,
                "granted_by": actor_label,
                "valid_until": subscription.current_period_end.isoformat(),
            },
            tenant_id=tenant_id,
        )
        return subscription

    # ===================================================================== #
    # Webhooks
    # ===================================================================== #

    async def handle_webhook(
        self,
        *,
        provider_kind: BillingProviderKind,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        """Ingest a provider notification, exactly once.

        A replay returns success with ``duplicate: true``. Returning an error
        would make a provider retry forever; applying it twice would extend a
        subscription twice. Both are worse than an idempotent acknowledgement.
        """
        provider = self._registry.get(provider_kind)

        if not provider.verify_webhook(headers, body):
            await self._record_webhook(
                provider_kind,
                dedupe_key=f"fingerprint:{hashlib.sha256(body).hexdigest()}",
                dedupe_source="fingerprint",
                state=WebhookProcessingState.REJECTED,
                signature_verified=False,
                payload={},
                body=body,
                error="signature verification failed",
            )
            await record_audit(
                self._db,
                AuditAction.BILLING_WEBHOOK_REJECTED,
                entity_type="billing_webhook",
                context={"provider": str(provider_kind), "reason": "signature"},
            )
            return {"accepted": False, "reason": "signature"}

        events = provider.parse_webhook(body)
        if not events:
            await self._record_webhook(
                provider_kind,
                dedupe_key=f"fingerprint:{hashlib.sha256(body).hexdigest()}",
                dedupe_source="fingerprint",
                state=WebhookProcessingState.PROCESSED,
                signature_verified=True,
                payload={},
                body=body,
                error="no recognised event in payload",
            )
            return {"accepted": True, "events": 0}

        applied, duplicates = 0, 0
        for event in events:
            record, is_duplicate = await self._claim_webhook(event, body)
            if is_duplicate:
                duplicates += 1
                continue
            try:
                await self._apply_event(event, record)
                record.state = str(WebhookProcessingState.PROCESSED)
                record.processed_at = utc_now()
                applied += 1
            except Exception as exc:
                record.state = str(WebhookProcessingState.FAILED)
                record.error = f"{type(exc).__name__}: {exc}"[:400]
                log.exception("billing webhook processing failed")

        await record_audit(
            self._db,
            AuditAction.BILLING_WEBHOOK_RECEIVED,
            entity_type="billing_webhook",
            context={
                "provider": str(provider_kind),
                "events": len(events),
                "applied": applied,
                "duplicates": duplicates,
            },
        )
        return {
            "accepted": True,
            "events": len(events),
            "applied": applied,
            "duplicates": duplicates,
        }

    async def _claim_webhook(
        self, event: BillingEvent, body: bytes
    ) -> tuple[BillingWebhookEvent, bool]:
        """Insert the dedupe row, or find the existing one.

        The unique constraint on ``(provider, dedupe_key)`` is what makes this
        exactly-once rather than best-effort: two concurrent deliveries race on
        the database, not on a Python check.
        """
        with allow_cross_tenant("billing: webhook arrives before a tenant is known"):
            existing = (
                await self._db.execute(
                    sa.select(BillingWebhookEvent).where(
                        BillingWebhookEvent.provider == str(event.provider),
                        BillingWebhookEvent.dedupe_key == event.dedupe_key,
                    )
                )
            ).scalar_one_or_none()

        if existing is not None:
            existing.delivery_count += 1
            return existing, True

        record = await self._record_webhook(
            event.provider,
            dedupe_key=event.dedupe_key,
            dedupe_source=event.dedupe_source,
            state=WebhookProcessingState.RECEIVED,
            signature_verified=True,
            payload=event.payload,
            body=body,
            event_type=str(event.event_type),
            occurred_at=event.occurred_at,
        )
        return record, False

    async def _record_webhook(
        self,
        provider: BillingProviderKind,
        *,
        dedupe_key: str,
        dedupe_source: str,
        state: WebhookProcessingState,
        signature_verified: bool,
        payload: dict[str, Any],
        body: bytes,
        event_type: str | None = None,
        occurred_at: datetime | None = None,
        error: str | None = None,
    ) -> BillingWebhookEvent:
        record = BillingWebhookEvent(
            provider=str(provider),
            dedupe_key=dedupe_key[:220],
            dedupe_source=dedupe_source,
            state=str(state),
            event_type=event_type,
            signature_verified=signature_verified,
            payload=redact_value(payload),
            payload_sha256=hashlib.sha256(body).hexdigest(),
            occurred_at=ensure_utc(occurred_at) if occurred_at else None,
            error=error,
        )
        self._db.add(record)
        await self._db.flush()
        return record

    async def _apply_event(self, event: BillingEvent, record: BillingWebhookEvent) -> None:
        """Route one provider-neutral event into the state machine."""
        subscription = await self._subscription_for_event(event)
        if subscription is None:
            # Unattributable. Kept for support rather than discarded: a webhook
            # we cannot place is a signal, not noise.
            record.error = "no subscription matched this event"
            return

        record.tenant_id = subscription.tenant_id
        record.subscription_id = subscription.id

        match event.event_type:
            case BillingEventType.SUBSCRIPTION_RENEWED:
                await self._extend_period(subscription, event)
            case BillingEventType.SUBSCRIPTION_STARTED | BillingEventType.SUBSCRIPTION_RESTORED:
                await self._extend_period(subscription, event, restore=True)
            case BillingEventType.PAYMENT_FAILED | BillingEventType.GRACE_STARTED:
                await self.record_payment_failure(
                    subscription=subscription,
                    provider=event.provider,
                    failure_code=event.failure_code,
                    amount_paisa=event.amount_paisa,
                )
            case BillingEventType.PAYMENT_RECOVERED:
                await self.record_payment_recovery(
                    subscription=subscription, provider=event.provider
                )
            case BillingEventType.SUBSCRIPTION_CANCELLED:
                previous = subscription.status
                subscription.cancel_at_period_end = True
                subscription.status = str(SubscriptionStatus.CANCEL_AT_PERIOD_END)
                subscription.cancelled_at = utc_now()
                subscription.status_reason = "Cancelled at the provider"
                await self._record_event(
                    subscription,
                    event_type=BillingEventType.SUBSCRIPTION_CANCELLED,
                    from_status=previous,
                    provider=event.provider,
                    provider_event_id=event.provider_event_id,
                )
            case BillingEventType.SUBSCRIPTION_EXPIRED:
                await self.expire_subscription(
                    subscription=subscription,
                    reason="The provider reported the subscription as expired",
                    provider=event.provider,
                )
            case BillingEventType.SUBSCRIPTION_REVOKED | BillingEventType.REFUNDED:
                await self.refund_subscription(
                    subscription=subscription,
                    provider=event.provider,
                    amount_paisa=event.amount_paisa,
                    reason="The provider revoked or refunded the purchase",
                    provider_event_id=event.provider_event_id,
                )
            case BillingEventType.PLAN_CHANGED:
                if event.plan is not None:
                    previous_plan = subscription.plan_code
                    subscription.plan_code = str(event.plan)
                    await self._record_event(
                        subscription,
                        event_type=BillingEventType.PLAN_CHANGED,
                        from_plan=previous_plan,
                        provider=event.provider,
                        provider_event_id=event.provider_event_id,
                    )
            case _:
                record.error = f"event type {event.event_type} carries no state change"

        subscription.last_synced_at = utc_now()

    async def _subscription_for_event(self, event: BillingEvent) -> Subscription | None:
        """Find the subscription an event belongs to, across tenants.

        A webhook is not authenticated as a tenant, so this read is deliberately
        unscoped — and narrow: it matches on the provider reference we recorded
        during verification, never on anything the payload asserts about a
        tenant id.
        """
        with allow_cross_tenant("billing: webhook attribution by provider reference"):
            if event.tenant_id is not None:
                return (
                    await self._db.execute(
                        sa.select(Subscription)
                        .where(Subscription.tenant_id == event.tenant_id)
                        .order_by(Subscription.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()

            reference = event.purchase_token_hash or event.provider_reference
            if reference is None:
                return None
            return (
                await self._db.execute(
                    sa.select(Subscription)
                    .where(
                        Subscription.source == str(event.provider),
                        Subscription.provider_reference == reference,
                    )
                    .order_by(Subscription.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def _extend_period(
        self, subscription: Subscription, event: BillingEvent, *, restore: bool = False
    ) -> None:
        """Move the period end to the provider's expiry.

        Assignment, not addition. A renewal delivered twice sets the same
        absolute expiry twice, which is a no-op; adding a month per delivery
        would hand out free time for every duplicate notification.
        """
        previous = subscription.status
        if event.expiry_at is not None:
            subscription.current_period_end = ensure_utc(event.expiry_at)
        if event.plan is not None:
            subscription.plan_code = str(event.plan)

        subscription.status = str(SubscriptionStatus.ACTIVE)
        subscription.grace_until = None
        subscription.verified_at = utc_now()
        subscription.status_reason = "Renewed at the provider" if not restore else "Restored"

        await self._resolve_open_attempts(subscription, recovered=True)
        await self._record_event(
            subscription,
            event_type=(
                BillingEventType.SUBSCRIPTION_RESTORED
                if restore
                else BillingEventType.SUBSCRIPTION_RENEWED
            ),
            from_status=previous,
            provider=event.provider,
            provider_event_id=event.provider_event_id,
        )
        await self._record_transaction(
            tenant_id=subscription.tenant_id,
            subscription=subscription,
            provider=event.provider,
            plan=PlanCode(subscription.plan_code),
            kind=TransactionKind.RENEWAL,
            state=TransactionState.VERIFIED,
            result=VerificationResult.VERIFIED,
            detail="Provider notification",
            provider_reference=event.provider_reference,
            provider_event_id=event.provider_event_id,
            amount_paisa=event.amount_paisa,
            currency=event.currency,
            metadata=event.payload,
        )

    # ===================================================================== #
    # Reconciliation and expiry jobs
    # ===================================================================== #

    async def reconcile_subscription(self, subscription: Subscription) -> str:
        """Compare provider truth with local state (master spec section 90).

        Returns what happened, as a short machine string, so the job can report
        counts and the admin console can show them.

        The rule that matters: **local expiry alone never revokes a verified
        provider-active subscription.** If the provider says active, we follow
        the provider. If the provider does not know — an unreachable API, a
        manual grant, a Play token we deliberately did not retain — we change
        nothing and leave ``last_synced_at`` untouched so the staleness stays
        visible.
        """
        provider_kind = BillingProviderKind(subscription.source)
        provider = self._registry.get(provider_kind)

        try:
            state = await provider.sync_subscription(
                provider_reference=subscription.provider_reference or ""
            )
        except Exception as exc:
            log.warning("subscription sync failed", extra={"error": type(exc).__name__})
            return "unavailable"

        if not state.known:
            return "unknown"

        subscription.last_synced_at = utc_now()

        if state.active:
            if subscription.status != str(SubscriptionStatus.ACTIVE):
                previous = subscription.status
                subscription.status = str(SubscriptionStatus.ACTIVE)
                subscription.grace_until = None
                subscription.status_reason = "Provider reports the subscription as active"
                await self._record_event(
                    subscription,
                    event_type=BillingEventType.RECONCILED,
                    from_status=previous,
                    provider=provider_kind,
                    reason="provider active, local inactive",
                )
            if state.current_period_end is not None:
                subscription.current_period_end = ensure_utc(state.current_period_end)
            subscription.verified_at = utc_now()
            return "activated"

        if subscription.is_current(at=utc_now()):
            await self.expire_subscription(
                subscription=subscription,
                reason="Provider reports the subscription as no longer active",
                provider=provider_kind,
            )
            await record_audit(
                self._db,
                AuditAction.BILLING_RECONCILED,
                entity_type="subscription",
                entity_id=subscription.id,
                context={"provider": str(provider_kind), "outcome": "expired"},
                tenant_id=subscription.tenant_id,
            )
            return "expired"
        return "unchanged"

    async def expire_lapsed(self, *, now: datetime | None = None, limit: int = 200) -> int:
        """Close out subscriptions whose paid period and grace have both ended.

        Deliberately *not* the only expiry path: a provider notification is
        faster and more authoritative. This is the safety net for the case
        section 90 calls out — a notification that never arrived.
        """
        moment = now or utc_now()
        rows = (
            (
                await self._db.execute(
                    sa.select(Subscription)
                    .where(
                        Subscription.status.in_(
                            [
                                str(SubscriptionStatus.ACTIVE),
                                str(SubscriptionStatus.GRACE),
                                str(SubscriptionStatus.CANCEL_AT_PERIOD_END),
                                str(SubscriptionStatus.PAST_DUE),
                            ]
                        ),
                        Subscription.current_period_end.is_not(None),
                        Subscription.current_period_end <= moment,
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

        expired = 0
        for subscription in rows:
            if subscription.grace_until is not None and subscription.grace_until > moment:
                continue  # still inside the dunning window
            await self.expire_subscription(
                subscription=subscription,
                reason="The paid period and any grace window have ended",
                provider=BillingProviderKind(subscription.source),
            )
            expired += 1
        return expired

    # ===================================================================== #
    # Writers
    # ===================================================================== #

    async def _record_transaction(
        self,
        *,
        tenant_id: uuid.UUID,
        subscription: Subscription | None,
        provider: BillingProviderKind,
        plan: PlanCode,
        kind: TransactionKind,
        state: TransactionState,
        result: VerificationResult,
        detail: str | None = None,
        provider_reference: str | None = None,
        provider_event_id: str | None = None,
        provider_product_id: str | None = None,
        amount_paisa: int = 0,
        currency: str = "BDT",
        metadata: dict[str, Any] | None = None,
    ) -> BillingTransaction:
        """Append a billing transaction.

        A duplicate ``(provider, provider_event_id)`` is not an error: it means
        the same provider event reached us twice, which the unique constraint
        catches and this turns back into the existing row.
        """
        # The row is created *inside* the savepoint. Adding it to the session
        # first and then opening one would leave the doomed object pending after
        # a rollback, and the very next flush would raise the same
        # IntegrityError somewhere unrelated.
        savepoint = await self._db.begin_nested()
        try:
            transaction = BillingTransaction(
                tenant_id=tenant_id,
                subscription_id=subscription.id if subscription else None,
                provider=str(provider),
                provider_event_id=provider_event_id,
                provider_reference=provider_reference,
                plan_code=str(plan),
                provider_product_id=provider_product_id,
                kind=str(kind),
                state=str(state),
                amount_paisa=max(0, amount_paisa),
                currency=currency,
                verification_result=str(result),
                verification_detail=detail[:400] if detail else None,
                metadata_json=redact_value(metadata or {}),
            )
            self._db.add(transaction)
            await self._db.flush()
            await savepoint.commit()
            return transaction
        except IntegrityError:
            await savepoint.rollback()

        existing = (
            await self._db.execute(
                sa.select(BillingTransaction).where(
                    BillingTransaction.provider == str(provider),
                    BillingTransaction.provider_event_id == provider_event_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            # The conflict was not the provider-event uniqueness. Re-raise by
            # letting the caller's transaction fail rather than swallowing a
            # constraint violation we do not understand.
            raise ConflictError("The billing transaction could not be recorded")
        return existing

    async def _record_refusal(
        self,
        tenant_id: uuid.UUID,
        verified: VerifiedPurchase,
        product_id: str | None,
        reference: str | None,
    ) -> None:
        await self._record_transaction(
            tenant_id=tenant_id,
            subscription=await self.current_subscription(tenant_id),
            provider=verified.provider,
            plan=verified.plan or PlanCode.FREE,
            kind=TransactionKind.PURCHASE,
            state=TransactionState.FAILED,
            result=verified.result,
            detail=verified.detail,
            provider_product_id=product_id,
            provider_reference=reference,
            metadata=verified.metadata,
        )
        await record_audit(
            self._db,
            AuditAction.BILLING_PURCHASE_REFUSED,
            entity_type="tenant",
            entity_id=tenant_id,
            context={
                "provider": str(verified.provider),
                "result": str(verified.result),
                "product_id": product_id,
            },
            tenant_id=tenant_id,
        )
        await self._commit_refusal()

    async def _commit_refusal(self) -> None:
        """Commit a refusal before the exception that reports it.

        The request session rolls back on an exception, so a refusal recorded
        and then raised would leave no trace — and a seller asking "what
        happened to my payment?" would be told nothing was ever attempted. The
        same reasoning made Phase A commit an OTP attempt before comparing it.

        Only the refusal record and its audit row exist at this point in the
        request, so committing them commits nothing else.
        """
        await self._db.commit()

    async def _record_event(
        self,
        subscription: Subscription,
        *,
        event_type: BillingEventType,
        provider: BillingProviderKind,
        from_status: str | None = None,
        from_plan: str | None = None,
        provider_event_id: str | None = None,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> SubscriptionEvent:
        event = SubscriptionEvent(
            tenant_id=subscription.tenant_id,
            subscription_id=subscription.id,
            event_type=str(event_type),
            from_status=from_status,
            to_status=subscription.status,
            from_plan=from_plan,
            to_plan=subscription.plan_code,
            provider=str(provider),
            provider_event_id=provider_event_id,
            reason=reason[:400] if reason else None,
            payload=redact_value(payload or {}),
        )
        self._db.add(event)
        if from_status is not None and from_status != subscription.status:
            await record_audit(
                self._db,
                AuditAction.SUBSCRIPTION_STATE_CHANGED,
                entity_type="subscription",
                entity_id=subscription.id,
                context={
                    "from": from_status,
                    "to": subscription.status,
                    "event": str(event_type),
                    "provider": str(provider),
                },
                tenant_id=subscription.tenant_id,
            )
        return event

    async def _open_attempts(self, subscription: Subscription) -> list[BillingAttempt]:
        return list(
            (
                await self._db.execute(
                    sa.select(BillingAttempt).where(
                        BillingAttempt.subscription_id == subscription.id,
                        BillingAttempt.resolved_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    async def _resolve_open_attempts(self, subscription: Subscription, *, recovered: bool) -> None:
        now = utc_now()
        for attempt in await self._open_attempts(subscription):
            attempt.state = str(
                BillingAttemptState.RECOVERED if recovered else BillingAttemptState.ABANDONED
            )
            attempt.resolved_at = now
            attempt.next_retry_at = None

    async def _link_provider_customer(
        self, tenant_id: uuid.UUID, provider: BillingProviderKind, reference: str
    ) -> None:
        existing = (
            await self._db.execute(
                sa.select(BillingProviderCustomer).where(
                    BillingProviderCustomer.tenant_id == tenant_id,
                    BillingProviderCustomer.provider == str(provider),
                )
            )
        ).scalar_one_or_none()
        digest = hash_provider_token(reference)
        if existing is not None:
            existing.reference_hash = digest
            existing.reference_suffix = reference[-4:]
            return
        self._db.add(
            BillingProviderCustomer(
                tenant_id=tenant_id,
                provider=str(provider),
                reference_hash=digest,
                reference_suffix=reference[-4:],
            )
        )
