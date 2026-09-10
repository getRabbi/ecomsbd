"""Google Play Billing, server side.

Master spec section 90. The flow this implements::

    Flutter -> Play purchase -> purchase token
            -> POST /v1/billing/play/verify
            -> backend asks Play whether that token is real
            -> billing transaction written
            -> subscription state updated
            -> entitlement granted

Everything above the dashed line below is implemented and tested. What is *not*
implemented is the single call that talks to Google:

    ┌─────────────────────────────────────────────────────────────┐
    │ token validation, replay protection, product/package checks,│
    │ plan mapping, state transitions, acknowledgement bookkeeping│  <- here
    ├─────────────────────────────────────────────────────────────┤
    │ PlayApiClient.get_subscription(...)                         │  <- not here
    └─────────────────────────────────────────────────────────────┘

Why: three things are missing and none of them can be guessed.

*   ``PACKAGE_ID_DECISION_REQUIRED`` — the Android ``applicationId`` is still
    the Flutter scaffold default. A Play purchase is bound to a package name,
    so verification cannot be written against an unknown one.
*   ``PLAY_SERVICE_ACCOUNT_REQUIRED`` — no service account exists to
    authenticate the call.
*   The exact current Google API surface and client library must be confirmed at
    implementation time (section 90's own last line). Writing it from memory and
    shipping it as working would be exactly the failure mode section 140
    forbids.

So :class:`PlayApiClient` is a port with no production implementation in this
repository. Supplying one — plus the package name, the service account and the
product map — is all that stands between this module and live verification.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.billing.models import BillingEventType, BillingProviderKind, hash_provider_token
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
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.redaction import redact_value
from app.entitlements.catalog import PlanCode, get_plan

__all__ = ["PLACEHOLDER_PACKAGE_PREFIX", "GooglePlayBillingProvider", "PlayApiClient"]

log = get_logger(__name__)

#: Play rejects anything under ``com.example``; the scaffold default lives there.
PLACEHOLDER_PACKAGE_PREFIX = "com.example"

#: Play's own notification type numbers, from the Real-time Developer
#: Notifications payload. Mapped to our provider-neutral vocabulary rather than
#: used directly, so a schema change on Google's side is a one-line edit here
#: instead of a change to the subscription state machine.
_RTDN_TYPE_TO_EVENT: dict[int, BillingEventType] = {
    1: BillingEventType.SUBSCRIPTION_RESTORED,  # RECOVERED
    2: BillingEventType.SUBSCRIPTION_RENEWED,
    3: BillingEventType.SUBSCRIPTION_CANCELLED,
    4: BillingEventType.SUBSCRIPTION_STARTED,
    5: BillingEventType.PAYMENT_FAILED,  # ON_HOLD
    6: BillingEventType.GRACE_STARTED,
    7: BillingEventType.SUBSCRIPTION_RESTORED,
    8: BillingEventType.PLAN_CHANGED,
    9: BillingEventType.SUBSCRIPTION_CANCELLED,  # deferred
    12: BillingEventType.SUBSCRIPTION_REVOKED,
    13: BillingEventType.SUBSCRIPTION_EXPIRED,
}


class PlayApiClient(Protocol):
    """Transport port for the Google Play Developer API.

    One method, because one call is all the domain needs: *given a purchase
    token, what does Google say about it?* Deliberately typed as a plain dict so
    this repository does not encode a guess at Google's response schema; the
    provider reads the handful of fields it needs and keeps the rest, redacted,
    on the billing transaction.
    """

    async def get_subscription(self, *, package_name: str, token: str) -> dict[str, Any]: ...

    async def acknowledge(self, *, package_name: str, token: str) -> None: ...

    async def revoke(self, *, package_name: str, token: str) -> None: ...


class GooglePlayBillingProvider:
    """Server-side Play Billing (master spec section 90)."""

    kind = BillingProviderKind.PLAY

    def __init__(self, settings: Settings, *, api: PlayApiClient | None = None) -> None:
        self._settings = settings
        self._api = api

    # ---------------------------------------------------------- availability ---

    def availability(self) -> ProviderAvailability:
        package = self._settings.play_package_name
        if package and package.startswith(PLACEHOLDER_PACKAGE_PREFIX):
            return ProviderAvailability(
                provider=self.kind,
                available=False,
                blocker=ProviderBlocker.PACKAGE_ID_DECISION_REQUIRED,
                detail=(
                    f"PLAY_PACKAGE_NAME is still the scaffold default '{package}'. "
                    "Play rejects com.example, and the id is permanent once published."
                ),
            )
        if not self._settings.play_billing_configured:
            return ProviderAvailability(
                provider=self.kind,
                available=False,
                blocker=ProviderBlocker.PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED,
                detail=(
                    "Needs PLAY_PACKAGE_NAME, PLAY_SERVICE_ACCOUNT_JSON and a "
                    "PLAY_PRODUCT_PLAN_MAP entry for every published product."
                ),
            )
        if self._api is None:
            return ProviderAvailability(
                provider=self.kind,
                available=False,
                blocker=ProviderBlocker.TRANSPORT_NOT_IMPLEMENTED,
                detail=(
                    "Configuration is present but no PlayApiClient is wired in. "
                    "The Google Play Developer API surface must be verified "
                    "against current documentation before it is called."
                ),
            )
        return ProviderAvailability(provider=self.kind, available=True)

    def _require_ready(self) -> tuple[str, PlayApiClient]:
        state = self.availability()
        if not state.available or self._api is None:
            raise ProviderNotConfiguredError("play", state.blocker, state.detail)
        assert self._settings.play_package_name is not None  # noqa: S101 - checked above
        return self._settings.play_package_name, self._api

    # -------------------------------------------------------------- checkout ---

    async def create_checkout(
        self, *, tenant_id: uuid.UUID, plan: PlanCode, reference: str
    ) -> CheckoutSession:
        """Tell the client which Play product to launch.

        There is no server-side checkout for Play: the purchase happens in the
        app through the platform billing library, and the server's only job is
        naming the product. No redirect URL is returned, because sending a Play
        user to an external payment page is precisely what section 27.1 forbids.
        """
        state = self.availability()
        if not state.available:
            raise ProviderNotConfiguredError("play", state.blocker, state.detail)

        product_id = self._product_for_plan(plan)
        if product_id is None:
            raise ProviderNotConfiguredError(
                "play",
                ProviderBlocker.PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED,
                f"No Play product is mapped to plan '{plan}'",
            )
        return CheckoutSession(
            provider=self.kind,
            checkout_reference=reference,
            plan=plan,
            amount_paisa=get_plan(plan).price_paisa,
            redirect_url=None,
            client_payload={"play_product_id": product_id, "mode": "in_app_billing"},
        )

    # ---------------------------------------------------------- verification ---

    async def verify_purchase(self, payload: dict[str, Any]) -> VerifiedPurchase:
        """Ask Play whether a purchase token is real.

        Package and product are validated *before* the network call. A token
        presented with a package name that is not this build is not a provider
        question at all — it is an attempt to spend another app's purchase here.
        """
        token = str(payload.get("purchase_token") or "")
        product_id = str(payload.get("product_id") or "")
        claimed_package = str(payload.get("package_name") or "")

        state = self.availability()
        if not state.available:
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.NOT_CONFIGURED,
                detail=state.detail,
                metadata={"blocker": str(state.blocker)},
            )

        package_name, api = self._require_ready()

        if claimed_package and claimed_package != package_name:
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.WRONG_PACKAGE,
                detail="The purchase was made against a different application package",
            )

        plan = self._plan_for_product(product_id)
        if plan is None:
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.UNKNOWN_PRODUCT,
                provider_product_id=product_id or None,
                detail=f"Product '{product_id}' is not sold by this application",
            )

        try:
            response = await api.get_subscription(package_name=package_name, token=token)
        except Exception as exc:
            # Never REJECTED. A network failure says nothing about the purchase,
            # and refusing it permanently would take a paying seller's plan away
            # because Google had a bad minute.
            log.warning("play verification unavailable", extra={"error": type(exc).__name__})
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.UNAVAILABLE,
                plan=plan,
                provider_product_id=product_id,
                detail="Google Play did not respond",
            )

        return self._interpret(response, plan=plan, product_id=product_id, token=token)

    def _interpret(
        self, response: dict[str, Any], *, plan: PlanCode, product_id: str, token: str
    ) -> VerifiedPurchase:
        """Turn a Play response into evidence.

        Only fields whose meaning is stable across the API's versions are read;
        anything else is kept, redacted, as metadata rather than interpreted.
        """
        purchase_state = str(
            response.get("subscriptionState") or response.get("purchaseState") or ""
        )
        expiry = _parse_instant(
            response.get("expiryTime")
            or response.get("expiryTimeMillis")
            or _nested(response, "lineItems", 0, "expiryTime")
        )
        starts = _parse_instant(response.get("startTime") or response.get("startTimeMillis"))
        acknowledged = str(response.get("acknowledgementState") or "") in {
            "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
            "1",
        }
        auto_renewing = bool(
            _nested(response, "lineItems", 0, "autoRenewingPlan", "autoRenewEnabled")
            or response.get("autoRenewing")
        )

        active_states = {
            "SUBSCRIPTION_STATE_ACTIVE",
            "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
            "SUBSCRIPTION_STATE_CANCELED",  # cancelled but paid through expiry
        }
        # An empty state string is not an approval. If Play did not say the
        # subscription is in a state we recognise as entitling, we do not grant.
        is_entitling = purchase_state in active_states or str(response.get("purchaseState")) == "0"
        if not is_entitling:
            return VerifiedPurchase(
                provider=self.kind,
                result=VerificationResult.REJECTED,
                plan=plan,
                provider_product_id=product_id,
                purchase_state=purchase_state or None,
                expiry_at=expiry,
                detail=f"Play reports the purchase as '{purchase_state or 'unknown'}'",
                metadata=redact_value(response),
            )

        return VerifiedPurchase(
            provider=self.kind,
            result=VerificationResult.VERIFIED,
            plan=plan,
            provider_reference=hash_provider_token(token),
            provider_event_id=str(response.get("latestOrderId") or response.get("orderId") or "")
            or None,
            provider_product_id=product_id,
            purchase_state=purchase_state or None,
            expiry_at=expiry,
            starts_at=starts,
            auto_renewing=auto_renewing,
            acknowledgement_required=not acknowledged,
            account_reference=str(
                _nested(response, "externalAccountIdentifiers", "obfuscatedExternalAccountId")
                or response.get("obfuscatedExternalAccountId")
                or ""
            )
            or None,
            metadata=redact_value(response),
        )

    async def acknowledge(self, token: str) -> None:
        """Acknowledge a purchase, where the current flow requires it.

        Play voids an unacknowledged purchase after its acknowledgement window.
        The call is only made when Play itself reported the purchase as
        unacknowledged, so a re-verification does not acknowledge twice.
        """
        package_name, api = self._require_ready()
        await api.acknowledge(package_name=package_name, token=token)

    # ------------------------------------------------------------ lifecycle ---

    async def cancel_subscription(
        self, *, provider_reference: str, at_period_end: bool
    ) -> CancelResult:
        """Play subscriptions are cancelled by the buyer, in Play.

        The server cannot cancel on the seller's behalf, and pretending it can
        would leave a seller believing they had stopped a renewal that then
        charged them. The honest answer is to say where the control lives.
        """
        state = self.availability()
        if not state.available:
            raise ProviderNotConfiguredError("play", state.blocker, state.detail)
        return CancelResult(
            provider=self.kind,
            accepted=False,
            effective_at_period_end=True,
            detail=(
                "A Play subscription is cancelled from the Play Store subscription "
                "settings. The app deep-links there; the server records the "
                "resulting notification."
            ),
        )

    async def sync_subscription(self, *, provider_reference: str) -> SubscriptionState:
        """Re-read provider truth for the missed-notification reconciler.

        ``provider_reference`` is a token *hash*, and a hash cannot be sent to
        Google. Syncing therefore needs the caller to supply the token it still
        holds — which the server deliberately does not store. So this returns
        "unknown" rather than a fabricated state, and reconciliation for Play
        runs from Real-time Developer Notifications plus the client's
        restore-purchases path instead.
        """
        state = self.availability()
        if not state.available:
            raise ProviderNotConfiguredError("play", state.blocker, state.detail)
        return SubscriptionState(
            provider=self.kind,
            known=False,
            detail=(
                "Play state is refreshed from developer notifications or from a "
                "client restore, because the purchase token is not retained."
            ),
        )

    async def refund(self, *, provider_reference: str, amount_paisa: int) -> RefundResult | None:
        """Refunds are issued in the Play Console, not by this server.

        Returning ``None`` says "this provider does not support the operation",
        which is different from "the refund failed".
        """
        return None

    # -------------------------------------------------------------- webhooks ---

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Authenticate a Real-time Developer Notification.

        Play delivers RTDNs through Pub/Sub push, whose authenticity is normally
        established by an OIDC token on the request. That verification needs the
        service account and audience that do not exist yet, so this deployment
        falls back to a shared secret an operator can configure on the push
        endpoint — and, with neither present, **refuses**.

        Refusing is the only safe default. A webhook verifier that returns
        ``True`` when it has nothing to check with turns the endpoint into an
        unauthenticated "grant me a subscription" API.
        """
        secret = self._settings.play_rtdn_shared_secret
        if secret is None:
            return False
        presented = headers.get("x-ecomsbd-signature") or headers.get("X-Ecomsbd-Signature") or ""
        expected = hmac.new(
            secret.get_secret_value().encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(presented, expected)

    def parse_webhook(self, body: bytes) -> list[BillingEvent]:
        """Decode an RTDN envelope into provider-neutral events.

        Play wraps the notification in a Pub/Sub message whose ``data`` is
        base64 JSON. Both the wrapped and unwrapped shapes are accepted, because
        the unwrapping is a transport detail and a test fixture should not have
        to imitate Pub/Sub to exercise the domain.
        """
        try:
            envelope = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []

        inner = envelope
        data = _nested(envelope, "message", "data")
        if isinstance(data, str):
            try:
                inner = json.loads(base64.b64decode(data).decode("utf-8"))
            except Exception:
                return []

        notification = inner.get("subscriptionNotification") or {}
        if not isinstance(notification, dict) or not notification:
            return []

        event_type = _RTDN_TYPE_TO_EVENT.get(int(notification.get("notificationType") or 0))
        if event_type is None:
            return []

        token = str(notification.get("purchaseToken") or "")
        product_id = str(notification.get("subscriptionId") or "")
        token_hash = hash_provider_token(token) if token else None
        message_id = str(_nested(envelope, "message", "messageId") or "") or None

        # Section 78's dedupe priority: message id, else the token, else a
        # fingerprint of the payload itself.
        if message_id:
            dedupe_key, dedupe_source = f"event:{message_id}", "event_id"
        elif token_hash:
            dedupe_key = f"ref:{token_hash}:{notification.get('notificationType')}"
            dedupe_source = "reference"
        else:
            dedupe_key = f"fingerprint:{hashlib.sha256(body).hexdigest()}"
            dedupe_source = "fingerprint"

        return [
            BillingEvent(
                provider=self.kind,
                event_type=event_type,
                dedupe_key=dedupe_key,
                dedupe_source=dedupe_source,
                occurred_at=_parse_instant(inner.get("eventTimeMillis")),
                provider_event_id=message_id,
                provider_reference=token_hash,
                provider_product_id=product_id or None,
                plan=self._plan_for_product(product_id),
                purchase_token_hash=token_hash,
                payload=redact_value(inner),
            )
        ]

    # --------------------------------------------------------------- mapping ---

    def _plan_for_product(self, product_id: str) -> PlanCode | None:
        mapped = self._settings.play_product_plan_map.get(product_id)
        if mapped is None:
            return None
        try:
            return PlanCode(mapped)
        except ValueError:
            log.error("play product maps to unknown plan", extra={"plan": mapped})
            return None

    def _product_for_plan(self, plan: PlanCode) -> str | None:
        for product_id, plan_code in self._settings.play_product_plan_map.items():
            if plan_code == str(plan):
                return product_id
        return None


def _nested(value: Any, *path: Any) -> Any:
    """Walk a nested dict/list path, returning ``None`` at the first miss."""
    current = value
    for step in path:
        if isinstance(step, int):
            if not isinstance(current, list) or len(current) <= step:
                return None
            current = current[step]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(step)
        if current is None:
            return None
    return current


def _parse_instant(value: Any) -> datetime | None:
    """Accept RFC 3339 text or epoch milliseconds; reject anything else.

    Play has used both representations across API versions. Guessing wrong
    turns a 2026 expiry into 1970, so an unparseable value becomes ``None`` —
    "we do not know when this expires" — rather than a plausible wrong date.
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    if isinstance(value, str):
        if value.isdigit():
            return datetime.fromtimestamp(int(value) / 1000.0, tz=UTC)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
