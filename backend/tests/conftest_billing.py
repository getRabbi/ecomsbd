"""Billing test doubles.

Master spec section 113: *"for each adapter create recorded/redacted fixtures …
tests never depend entirely on live provider uptime."*

Neither Play nor bKash can be called from this repository — no service account,
no merchant contract, no decided package name. These fakes stand in for the
transport ports so the whole domain above them (replay protection, plan
mapping, state transitions, dunning, reconciliation) is exercised for real.

They are deliberately dumb: they return what they are told to return and record
what they were asked. A fake that contained logic would end up testing itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from app.billing.providers.registry import build_registry
from app.core.config import Settings

__all__ = [
    "BKASH_WEBHOOK_SECRET",
    "PLAY_PACKAGE",
    "PLAY_PRODUCT_PRO",
    "PLAY_PRODUCT_STARTER",
    "PLAY_RTDN_SECRET",
    "FakeBkashApi",
    "FakePlayApi",
    "billing_settings",
    "install_registry",
    "play_response",
]

#: A plausible-but-fictional package name. Deliberately NOT com.example (which
#: the provider refuses as the undecided scaffold default) and deliberately not
#: a guess at the real one, which is still PACKAGE_ID_DECISION_REQUIRED.
PLAY_PACKAGE = "test.ecomsbd.fixture"
PLAY_PRODUCT_STARTER = "ecomsbd_starter_monthly"
PLAY_PRODUCT_PRO = "ecomsbd_pro_monthly"


#: Shared-secret values used by the webhook signature fixtures.
PLAY_RTDN_SECRET = "fixture-rtdn-secret"
BKASH_WEBHOOK_SECRET = "fixture-bkash-secret"


def billing_settings(base: Settings, **overrides: Any) -> Settings:
    """A settings copy with both billing providers configured.

    ``model_copy`` skips validation, so every secret is wrapped explicitly:
    a plain ``str`` where a ``SecretStr`` is expected would only fail later,
    inside the code under test.
    """
    values: dict[str, Any] = {
        "play_package_name": PLAY_PACKAGE,
        "play_service_account_json": SecretStr("INSECURE_DEV_fixture_service_account"),
        "play_product_plan_map": {
            PLAY_PRODUCT_STARTER: "starter",
            PLAY_PRODUCT_PRO: "pro",
        },
        "play_rtdn_shared_secret": SecretStr(PLAY_RTDN_SECRET),
        "bkash_base_url": "https://bkash.invalid/fixture",
        "bkash_app_key": SecretStr("fixture-app-key"),
        "bkash_app_secret": SecretStr("fixture-app-secret"),
        "bkash_username": SecretStr("fixture-user"),
        "bkash_password": SecretStr("fixture-pass"),
        "bkash_webhook_secret": SecretStr(BKASH_WEBHOOK_SECRET),
    }
    values.update(overrides)
    return base.model_copy(update=values)


def play_response(
    *,
    state: str = "SUBSCRIPTION_STATE_ACTIVE",
    expiry: str | None = "2027-01-01T00:00:00Z",
    order_id: str = "GPA.FIXTURE-0001",
    acknowledged: bool = True,
    auto_renewing: bool = True,
) -> dict[str, Any]:
    """A Play subscription response shaped like the fields the provider reads.

    Only the fields the adapter actually interprets are present. Inventing a
    full response would imply a verified schema this project does not have.
    """
    return {
        "subscriptionState": state,
        "latestOrderId": order_id,
        "startTime": "2026-09-01T00:00:00Z",
        "acknowledgementState": (
            "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED"
            if acknowledged
            else "ACKNOWLEDGEMENT_STATE_PENDING"
        ),
        "lineItems": [
            {
                "expiryTime": expiry,
                "autoRenewingPlan": {"autoRenewEnabled": auto_renewing},
            }
        ],
    }


class FakePlayApi:
    """A scripted :class:`~app.billing.providers.google_play.PlayApiClient`."""

    def __init__(
        self,
        responses: dict[str, dict[str, Any]] | None = None,
        *,
        default: dict[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.responses = responses or {}
        self.default = default if default is not None else play_response()
        self.raises = raises
        self.acknowledged: list[str] = []
        self.revoked: list[str] = []
        self.calls: list[str] = []

    async def get_subscription(self, *, package_name: str, token: str) -> dict[str, Any]:
        self.calls.append(token)
        if self.raises is not None:
            raise self.raises
        if token in self.responses:
            return self.responses[token]
        # Distinct order ids per token, because Play gives one purchase one
        # order id and the transaction table enforces that uniqueness. A fake
        # that returned the same id for every token would make two unrelated
        # purchases look like one replay.
        response = dict(self.default)
        response["latestOrderId"] = f"GPA.FIXTURE-{token}"
        return response

    async def acknowledge(self, *, package_name: str, token: str) -> None:
        self.acknowledged.append(token)

    async def revoke(self, *, package_name: str, token: str) -> None:
        self.revoked.append(token)


class FakeBkashApi:
    """A scripted :class:`~app.billing.providers.bkash_web.BkashApiClient`."""

    def __init__(
        self,
        *,
        create_response: dict[str, Any] | None = None,
        query_response: dict[str, Any] | None = None,
        recurring: bool = False,
    ) -> None:
        self.create_response = create_response or {
            "paymentID": "TR0011FIXTURE",
            "bkashURL": "https://bkash.invalid/fixture/checkout/TR0011FIXTURE",
        }
        self.query_response = query_response or {
            "transactionStatus": "Completed",
            "plan": "starter",
            "trxID": "TRX-FIXTURE-1",
            "agreementID": "AGR-FIXTURE-1",
            "amount": "199",
            "currency": "BDT",
            "valid_until": "2026-10-10T00:00:00Z",
            "customerMsisdn": "01712345678",
        }
        self._recurring = recurring
        self.cancelled: list[str] = []
        self.refunded: list[tuple[str, int]] = []

    @property
    def supports_recurring(self) -> bool:
        return self._recurring

    async def create_payment(
        self, *, reference: str, amount_paisa: int, currency: str, plan: str
    ) -> dict[str, Any]:
        return dict(self.create_response)

    async def query_payment(self, *, reference: str) -> dict[str, Any]:
        return dict(self.query_response)

    async def cancel_agreement(self, *, agreement_reference: str) -> dict[str, Any]:
        self.cancelled.append(agreement_reference)
        return {"agreementStatus": "Cancelled"}

    async def refund_payment(self, *, payment_reference: str, amount_paisa: int) -> dict[str, Any]:
        self.refunded.append((payment_reference, amount_paisa))
        return {
            "transactionStatus": "Completed",
            "refundTrxID": "RFD-1",
            "amount_paisa": amount_paisa,
        }


def install_registry(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    play_api: FakePlayApi | None = None,
    bkash_api: FakeBkashApi | None = None,
) -> None:
    """Replace the process-wide provider registry for one test.

    The registry is a singleton so the app builds it once; overriding the module
    global is how a test supplies transports without changing production wiring.
    ``reset_singletons()`` in the autouse fixture clears it again afterwards.
    """
    import app.api.deps as deps

    monkeypatch.setattr(
        deps,
        "_registry",
        build_registry(settings, play_api=play_api, bkash_api=bkash_api),
    )
