"""Admin/manual billing.

Master spec section 92: ``AdminManualProvider  # support/test-controlled only``.

This is how a support engineer issues plan credit, how a pilot seller is given
access, and how the test suite exercises the subscription state machine without
a provider. It is **never** self-service: :meth:`create_checkout` refuses, so
there is no route by which a seller could reach this provider from the app and
grant themselves a plan.

Everything it does is audited by the caller
(:class:`~app.billing.service.BillingService`), and each grant records who made
it and why.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.billing.models import BillingProviderKind
from app.billing.providers.base import (
    BillingEvent,
    CancelResult,
    CheckoutSession,
    ProviderAvailability,
    ProviderBlocker,
    ProviderNotConfiguredError,
    RefundResult,
    SubscriptionState,
    VerificationResult,
    VerifiedPurchase,
)
from app.core.clock import utc_now
from app.entitlements.catalog import PlanCode

__all__ = ["AdminManualBillingProvider"]


class AdminManualBillingProvider:
    """Support-controlled grants. Always available; never seller-facing."""

    kind = BillingProviderKind.MANUAL_ADMIN

    def availability(self) -> ProviderAvailability:
        # Available, but not offerable: a client must never render a purchase
        # button for it, which is what ``allowed_in_channel=False`` says.
        return ProviderAvailability(
            provider=self.kind,
            available=True,
            allowed_in_channel=False,
            detail="Support-issued credit only. Not a self-service billing channel.",
        )

    async def create_checkout(
        self, *, tenant_id: uuid.UUID, plan: PlanCode, reference: str
    ) -> CheckoutSession:
        raise ProviderNotConfiguredError(
            "manual_admin",
            ProviderBlocker.NONE,
            "Manual billing has no checkout. Support issues credit through the admin console.",
        )

    async def verify_purchase(self, payload: dict[str, Any]) -> VerifiedPurchase:
        """A manual grant carries its own authority.

        The authority is the platform-admin authorisation and audit trail on the
        calling endpoint, not anything in this payload — which is why the plan
        must be supplied explicitly and an unknown one is refused rather than
        defaulted.
        """
        raw_plan = str(payload.get("plan") or "")
        try:
            plan = PlanCode(raw_plan)
        except ValueError:
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.UNKNOWN_PRODUCT,
                detail=f"'{raw_plan}' is not a plan code",
            )
        return VerifiedPurchase(
            provider=self.kind,
            result=VerificationResult.VERIFIED,
            plan=plan,
            provider_reference=str(payload["reference"]) if payload.get("reference") else None,
            detail=str(payload.get("reason") or "support grant"),
        )

    async def cancel_subscription(
        self, *, provider_reference: str, at_period_end: bool
    ) -> CancelResult:
        return CancelResult(
            provider=self.kind,
            accepted=True,
            effective_at_period_end=at_period_end,
            effective_at=None if at_period_end else utc_now(),
        )

    async def sync_subscription(self, *, provider_reference: str) -> SubscriptionState:
        """There is no provider to ask.

        Returning ``known=False`` matters: it stops the reconciliation job from
        treating "no provider truth" as "the provider says this is gone" and
        expiring a support grant that was deliberately issued.
        """
        return SubscriptionState(
            provider=self.kind,
            known=False,
            detail="Manual grants have no provider to reconcile against.",
        )

    async def refund(self, *, provider_reference: str, amount_paisa: int) -> RefundResult | None:
        return None

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return False

    def parse_webhook(self, body: bytes) -> list[BillingEvent]:
        return []
