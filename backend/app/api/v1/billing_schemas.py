"""Billing request and response schemas.

Kept out of :mod:`app.api.v1.schemas` because Phase F adds enough of them to
make that module hard to read, and because the billing wire contract is the one
a Play release review will be read against.

Two rules hold throughout:

*   **No secret crosses the wire outward.** A purchase token is accepted on the
    way in and never returned; provider references leave as hashes or last-four
    suffixes.
*   **Money is integer paisa** (section 32), never a formatted string.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.api.v1.schemas import MoneyAmount

__all__ = [
    "BillingChannelResponse",
    "BillingProviderStateResponse",
    "BillingTransactionResponse",
    "CancelSubscriptionPayload",
    "CheckoutPayload",
    "CheckoutResponse",
    "ConfirmCheckoutPayload",
    "PlayVerifyPayload",
    "PurchaseResultResponse",
    "RestorePurchase",
    "RestorePurchasesPayload",
    "RestorePurchasesResponse",
    "SubscriptionEventResponse",
    "SubscriptionResponse",
    "UsageResponse",
    "UsageView",
]


class UsageView(BaseModel):
    """One metered entitlement's spend this period."""

    entitlement: str
    period: str
    period_key: str
    used: int
    limit: int
    remaining: int | None
    unlimited: bool


class UsageResponse(BaseModel):
    plan: str
    usage: list[UsageView]


class SubscriptionResponse(BaseModel):
    """The shop's subscription, as the app renders it.

    ``status_reason`` is deliberately included: a seller looking at "Payment
    failed; retrying until 14 March" understands their situation, and a seller
    looking at "PAST_DUE" does not.
    """

    id: uuid.UUID
    plan: str
    status: str
    provider: str
    distribution_channel: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_end: datetime | None
    grace_until: datetime | None
    cancel_at_period_end: bool
    in_grace: bool
    status_reason: str | None
    verified_at: datetime | None
    last_synced_at: datetime | None
    #: Last four characters of the provider reference, for support. Never the
    #: reference itself.
    provider_reference_suffix: str | None = None


class BillingProviderStateResponse(BaseModel):
    provider: str
    available: bool
    blocker: str
    allowed_in_channel: bool
    #: Operator-facing. Present only for platform admins; ``None`` for sellers,
    #: because "PLAY_SERVICE_ACCOUNT_JSON is missing" is not seller-facing copy.
    detail: str | None = None


class BillingChannelResponse(BaseModel):
    """What this build may offer (master spec section 27.1).

    The client renders its billing call-to-action from this and nothing else.
    No platform check belongs in a widget.
    """

    channel: str
    can_purchase: bool
    allows_external_payment_link: bool
    providers: list[BillingProviderStateResponse]
    #: Seller-facing sentence for the case where nothing can be purchased,
    #: already written so the app does not have to compose one.
    unavailable_message_bn: str | None = None


class PlayVerifyPayload(BaseModel):
    """A Play purchase the client is asking us to verify.

    The token is a bearer credential: it is accepted here, hashed immediately,
    and never stored, logged or returned.
    """

    purchase_token: str = Field(min_length=8, max_length=4000)
    product_id: str = Field(min_length=1, max_length=200)
    package_name: str | None = Field(default=None, max_length=200)


class RestorePurchase(BaseModel):
    purchase_token: str = Field(min_length=8, max_length=4000)
    product_id: str = Field(min_length=1, max_length=200)
    package_name: str | None = Field(default=None, max_length=200)


class RestorePurchasesPayload(BaseModel):
    purchases: list[RestorePurchase] = Field(default_factory=list, max_length=20)


class PurchaseResultResponse(BaseModel):
    granted: bool
    result: str
    detail: str | None = None
    subscription: SubscriptionResponse | None = None


class RestorePurchasesResponse(BaseModel):
    restored: int
    results: list[PurchaseResultResponse]
    subscription: SubscriptionResponse | None = None


class CheckoutPayload(BaseModel):
    plan: str = Field(min_length=1, max_length=32)
    provider: str | None = Field(default=None, max_length=32)


class CheckoutResponse(BaseModel):
    provider: str
    checkout_reference: str
    plan: str
    amount: MoneyAmount
    redirect_url: str | None
    expires_at: datetime | None
    client_payload: dict[str, Any]


class ConfirmCheckoutPayload(BaseModel):
    reference: str = Field(min_length=1, max_length=200)
    provider: str | None = Field(default=None, max_length=32)


class CancelSubscriptionPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=400)
    #: Immediate cancellation is not offered to sellers: they keep what they
    #: paid for. The field exists so an admin path can pass ``False``.
    at_period_end: bool = True


class BillingTransactionResponse(BaseModel):
    id: uuid.UUID
    provider: str
    plan: str
    kind: str
    state: str
    amount: MoneyAmount
    verification_result: str
    detail: str | None
    occurred_at: datetime


class SubscriptionEventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    from_status: str | None
    to_status: str | None
    provider: str
    reason: str | None
    occurred_at: datetime
