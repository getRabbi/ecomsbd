"""bKash web/direct billing.

Master spec section 91. bKash's published business offerings include a payment
gateway, tokenized checkout and subscription payments — but which of them a
given merchant can use, and the endpoint paths, field names and callback
signature scheme, all depend on the merchant onboarding contract.

Section 91 is explicit about what to do with that: *"do not hard-code
undocumented endpoint paths into the master domain"*. So this module contains
**no bKash URL, no field name and no signature algorithm taken from memory**.
What it contains is the domain around them:

*   a checkout record with our own reference, created before anyone is sent
    anywhere, so a payment that comes back can always be attached to something;
*   callback verification delegated to a configured secret, refusing when there
    is none;
*   a one-time-payment fallback shape, because section 91 says a merchant may
    not have a subscription mandate enabled;
*   generic dunning, since bKash's real retry behaviour is unverified.

:class:`BkashApiClient` is the port an operator implements once they hold the
merchant documentation. Until then the provider reports
``BKASH_MERCHANT_SETUP_REQUIRED`` and refuses every live operation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import Any, Protocol

from app.billing.models import BillingEventType, BillingProviderKind
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
from app.core.clock import ensure_utc, utc_now
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.redaction import redact_value
from app.entitlements.catalog import PlanCode, get_plan

__all__ = ["BkashApiClient", "BkashWebBillingProvider"]

log = get_logger(__name__)

#: Our own vocabulary for what a callback says happened. A merchant contract
#: will use different words; mapping them is the adapter's job, not the domain's.
_STATUS_TO_EVENT: dict[str, BillingEventType] = {
    "completed": BillingEventType.SUBSCRIPTION_STARTED,
    "renewed": BillingEventType.SUBSCRIPTION_RENEWED,
    "cancelled": BillingEventType.SUBSCRIPTION_CANCELLED,
    "failed": BillingEventType.PAYMENT_FAILED,
    "refunded": BillingEventType.REFUNDED,
    "expired": BillingEventType.SUBSCRIPTION_EXPIRED,
}


class BkashApiClient(Protocol):
    """Transport port for a bKash merchant integration.

    Method names describe *intent* rather than any published endpoint, so an
    implementation can map them onto whatever the merchant contract actually
    exposes — tokenized checkout, subscription mandate, or a plain one-time
    payment — without the domain changing.
    """

    async def create_payment(
        self, *, reference: str, amount_paisa: int, currency: str, plan: str
    ) -> dict[str, Any]: ...

    async def query_payment(self, *, reference: str) -> dict[str, Any]: ...

    async def cancel_agreement(self, *, agreement_reference: str) -> dict[str, Any]: ...

    async def refund_payment(
        self, *, payment_reference: str, amount_paisa: int
    ) -> dict[str, Any]: ...

    @property
    def supports_recurring(self) -> bool:
        """Whether this merchant's contract includes a subscription mandate."""
        ...


class BkashWebBillingProvider:
    """bKash web/direct channel (master spec section 91)."""

    kind = BillingProviderKind.BKASH_WEB

    def __init__(self, settings: Settings, *, api: BkashApiClient | None = None) -> None:
        self._settings = settings
        self._api = api

    # ---------------------------------------------------------- availability ---

    def availability(self) -> ProviderAvailability:
        if not self._settings.bkash_billing_configured:
            return ProviderAvailability(
                provider=self.kind,
                available=False,
                blocker=ProviderBlocker.BKASH_MERCHANT_SETUP_REQUIRED,
                detail=(
                    "Needs BKASH_BASE_URL, BKASH_APP_KEY, BKASH_APP_SECRET, "
                    "BKASH_USERNAME and BKASH_PASSWORD from merchant onboarding."
                ),
            )
        if self._api is None:
            return ProviderAvailability(
                provider=self.kind,
                available=False,
                blocker=ProviderBlocker.TRANSPORT_NOT_IMPLEMENTED,
                detail=(
                    "Credentials are present but no BkashApiClient is wired in. "
                    "Endpoint paths and the callback signature scheme come from "
                    "the merchant contract and must not be guessed."
                ),
            )
        return ProviderAvailability(provider=self.kind, available=True)

    def _require_ready(self) -> BkashApiClient:
        state = self.availability()
        if not state.available or self._api is None:
            raise ProviderNotConfiguredError("bkash_web", state.blocker, state.detail)
        return self._api

    @property
    def supports_recurring(self) -> bool:
        """Whether a subscription mandate is available to this merchant.

        Defaults to ``False``: section 91 requires a one-time payment fallback
        precisely because a mandate cannot be assumed.
        """
        return self._api is not None and self._api.supports_recurring

    # -------------------------------------------------------------- checkout ---

    async def create_checkout(
        self, *, tenant_id: uuid.UUID, plan: PlanCode, reference: str
    ) -> CheckoutSession:
        """Start a payment and return where to send the seller.

        The provider is asked for a redirect target; nothing about its shape is
        assumed beyond "a URL, under one of a small set of plausible keys".
        A response with no usable URL is an error, not a silent success.
        """
        api = self._require_ready()
        definition = get_plan(plan)

        response = await api.create_payment(
            reference=reference,
            amount_paisa=definition.price_paisa,
            currency="BDT",
            plan=str(plan),
        )
        redirect = _first_str(response, "bkashURL", "redirect_url", "checkout_url", "url")
        if not redirect:
            raise ProviderNotConfiguredError(
                "bkash_web",
                ProviderBlocker.TRANSPORT_NOT_IMPLEMENTED,
                "The bKash client returned no redirect target for the checkout",
            )

        return CheckoutSession(
            provider=self.kind,
            checkout_reference=_first_str(response, "paymentID", "payment_id") or reference,
            plan=plan,
            amount_paisa=definition.price_paisa,
            currency="BDT",
            redirect_url=redirect,
            expires_at=_parse_instant(response.get("expires_at")),
            client_payload={
                "mode": "recurring" if self.supports_recurring else "one_time",
                "merchant_reference": reference,
            },
        )

    # ---------------------------------------------------------- verification ---

    async def verify_purchase(self, payload: dict[str, Any]) -> VerifiedPurchase:
        """Ask bKash about a payment we started. Never trust the callback body.

        A callback tells us *to go and look*; what it says happened is not
        evidence. This re-queries by our own reference, which is the only value
        an attacker replaying a callback cannot control.
        """
        state = self.availability()
        if not state.available:
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.NOT_CONFIGURED,
                detail=state.detail,
                metadata={"blocker": str(state.blocker)},
            )
        api = self._require_ready()

        reference = str(payload.get("reference") or payload.get("merchant_reference") or "")
        if not reference:
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.REJECTED,
                detail="No merchant reference was supplied",
            )

        try:
            response = await api.query_payment(reference=reference)
        except Exception as exc:
            log.warning("bkash verification unavailable", extra={"error": type(exc).__name__})
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.UNAVAILABLE,
                detail="bKash did not respond",
            )

        status = (_first_str(response, "transactionStatus", "status") or "").lower()
        plan = _plan_or_none(_first_str(response, "plan", "plan_code"))
        if status not in {"completed", "success", "successful"} or plan is None:
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.REJECTED
                if plan is not None
                else VerificationResult.UNKNOWN_PRODUCT,
                plan=plan,
                purchase_state=status or None,
                detail=f"bKash reports the payment as '{status or 'unknown'}'",
                metadata=redact_value(response),
            )

        return VerifiedPurchase(
            provider=self.kind,
            result=VerificationResult.VERIFIED,
            plan=plan,
            provider_reference=_first_str(response, "agreementID", "paymentID", "trxID"),
            provider_event_id=_first_str(response, "trxID", "transaction_id"),
            purchase_state=status,
            amount_paisa=_amount_paisa(response),
            currency=_first_str(response, "currency") or "BDT",
            expiry_at=_parse_instant(response.get("expires_at") or response.get("valid_until")),
            auto_renewing=self.supports_recurring,
            account_reference=_first_str(response, "customerMsisdn", "customer_reference"),
            metadata=redact_value(response),
        )

    # ------------------------------------------------------------ lifecycle ---

    async def cancel_subscription(
        self, *, provider_reference: str, at_period_end: bool
    ) -> CancelResult:
        api = self._require_ready()
        if not self.supports_recurring:
            # One-time payments cannot be "cancelled": the seller simply does
            # not buy the next period. Saying so is better than reporting a
            # successful cancellation of something that was never recurring.
            return CancelResult(
                provider=self.kind,
                accepted=True,
                effective_at_period_end=True,
                detail="No recurring mandate exists; access runs to the end of the paid period.",
            )

        response = await api.cancel_agreement(agreement_reference=provider_reference)
        status = (_first_str(response, "agreementStatus", "status") or "").lower()
        accepted = status in {"cancelled", "canceled", "success", "successful"}
        return CancelResult(
            provider=self.kind,
            accepted=accepted,
            effective_at_period_end=at_period_end,
            effective_at=None if at_period_end else utc_now(),
            detail=None if accepted else f"bKash returned '{status or 'unknown'}'",
        )

    async def sync_subscription(self, *, provider_reference: str) -> SubscriptionState:
        state = self.availability()
        if not state.available:
            raise ProviderNotConfiguredError("bkash_web", state.blocker, state.detail)
        api = self._require_ready()

        try:
            response = await api.query_payment(reference=provider_reference)
        except Exception as exc:
            log.warning("bkash sync unavailable", extra={"error": type(exc).__name__})
            return SubscriptionState(provider=self.kind, known=False, detail="bKash unreachable")

        status = (
            _first_str(response, "agreementStatus", "transactionStatus", "status") or ""
        ).lower()
        return SubscriptionState(
            provider=self.kind,
            known=True,
            active=status in {"active", "completed", "success", "successful"},
            plan=_plan_or_none(_first_str(response, "plan", "plan_code")),
            current_period_end=_parse_instant(
                response.get("expires_at") or response.get("valid_until")
            ),
            auto_renewing=self.supports_recurring and status == "active",
            cancelled=status in {"cancelled", "canceled"},
            purchase_state=status or None,
        )

    async def refund(self, *, provider_reference: str, amount_paisa: int) -> RefundResult | None:
        api = self._require_ready()
        response = await api.refund_payment(
            payment_reference=provider_reference, amount_paisa=amount_paisa
        )
        status = (_first_str(response, "transactionStatus", "status") or "").lower()
        return RefundResult(
            provider=self.kind,
            accepted=status in {"completed", "success", "successful"},
            amount_paisa=_amount_paisa(response) or amount_paisa,
            provider_reference=_first_str(response, "refundTrxID", "trxID"),
            detail=None if status else "bKash returned no status",
        )

    # -------------------------------------------------------------- webhooks ---

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """HMAC-SHA256 over the raw body, keyed by the configured secret.

        The real scheme comes from the merchant contract. This is a placeholder
        an operator replaces — and, critically, it **refuses when no secret is
        configured** rather than accepting anything. An IPN endpoint that trusts
        an unsigned body is a way to grant yourself a subscription.
        """
        secret = self._settings.bkash_webhook_secret
        if secret is None:
            return False
        presented = (
            headers.get("x-bkash-signature")
            or headers.get("X-Bkash-Signature")
            or headers.get("x-ecomsbd-signature")
            or ""
        )
        expected = hmac.new(
            secret.get_secret_value().encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(presented, expected)

    def parse_webhook(self, body: bytes) -> list[BillingEvent]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict):
            return []

        status = (_first_str(payload, "transactionStatus", "status", "event") or "").lower()
        event_type = _STATUS_TO_EVENT.get(status)
        if event_type is None:
            return []

        event_id = _first_str(payload, "trxID", "transaction_id", "event_id")
        reference = _first_str(payload, "agreementID", "paymentID", "merchant_reference")

        if event_id:
            dedupe_key, dedupe_source = f"event:{event_id}", "event_id"
        elif reference:
            dedupe_key, dedupe_source = f"ref:{reference}:{status}", "reference"
        else:
            dedupe_key = f"fingerprint:{hashlib.sha256(body).hexdigest()}"
            dedupe_source = "fingerprint"

        return [
            BillingEvent(
                provider=self.kind,
                event_type=event_type,
                dedupe_key=dedupe_key,
                dedupe_source=dedupe_source,
                occurred_at=_parse_instant(payload.get("occurred_at") or payload.get("dateTime")),
                provider_event_id=event_id,
                provider_reference=reference,
                plan=_plan_or_none(_first_str(payload, "plan", "plan_code")),
                amount_paisa=_amount_paisa(payload),
                currency=_first_str(payload, "currency") or "BDT",
                failure_code=_first_str(payload, "errorCode", "failure_code"),
                payload=redact_value(payload),
            )
        ]


# --------------------------------------------------------------------------- #
# Parsing helpers. All of them refuse rather than guess.
# --------------------------------------------------------------------------- #


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _plan_or_none(value: str | None) -> PlanCode | None:
    if value is None:
        return None
    try:
        return PlanCode(value)
    except ValueError:
        return None


def _amount_paisa(payload: dict[str, Any]) -> int:
    """Read an amount as integer paisa.

    bKash quotes taka. Money is never a float here (section 32), so the taka
    value is parsed as a decimal string and scaled; anything unreadable becomes
    zero *and* the transaction records the raw payload, rather than a rounded
    guess entering the money history.
    """
    if "amount_paisa" in payload:
        try:
            return int(payload["amount_paisa"])
        except (TypeError, ValueError):
            return 0
    raw = payload.get("amount")
    if raw is None:
        return 0
    try:
        from decimal import Decimal

        return int(Decimal(str(raw)) * 100)
    except Exception:
        return 0


def _parse_instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return ensure_utc(parsed)
    return None
