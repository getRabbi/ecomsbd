"""Request and response schemas for /v1.

Money always crosses the wire as integer paisa plus an explicit currency
(master spec section 32). No endpoint ever sends or accepts a formatted money
string, because parsing one back is exactly where rounding errors enter.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.auth.models import DevicePlatform
from app.tenants.models import BusinessCategory, OnboardingStep

__all__ = [
    "DevicePayload",
    "EntitlementsResponse",
    "MeResponse",
    "MoneyAmount",
    "OtpRequestPayload",
    "OtpRequestResponse",
    "OtpVerifyPayload",
    "PlanResponse",
    "ProviderResponse",
    "RefreshPayload",
    "SelectTenantPayload",
    "SessionResponse",
    "ShopCreatePayload",
    "ShopUpdatePayload",
    "TenantResponse",
    "TenantSummaryResponse",
]


class MoneyAmount(BaseModel):
    """Integer paisa on the wire, never a float or a formatted string."""

    amount_paisa: int
    currency: str = "BDT"


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


class DevicePayload(BaseModel):
    """Optional device description sent with sign-in."""

    install_id: str | None = Field(default=None, max_length=120)
    platform: DevicePlatform = DevicePlatform.UNKNOWN
    app_version: str | None = Field(default=None, max_length=40)
    os_version: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    push_token: str | None = Field(default=None, max_length=400)


class OtpRequestPayload(BaseModel):
    phone: str = Field(
        min_length=6,
        max_length=24,
        description="Bangladeshi mobile number. Bangla digits and separators are accepted.",
        examples=["01712345678", "+8801712345678", "০১৭১২৩৪৫৬৭৮"],
    )


class OtpRequestResponse(BaseModel):
    challenge_id: uuid.UUID
    masked_phone: str
    expires_in_seconds: int
    resend_available_in_seconds: int
    debug_code: str | None = Field(
        default=None,
        description=(
            "The OTP itself. Present only in local/test environments running the "
            "development provider; always null in staging and production."
        ),
    )


class OtpVerifyPayload(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(min_length=4, max_length=10)
    device: DevicePayload = Field(default_factory=DevicePayload)


class RefreshPayload(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=400)


class LogoutPayload(BaseModel):
    all_devices: bool = False


class SelectTenantPayload(BaseModel):
    tenant_id: uuid.UUID


class TenantSummaryResponse(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    onboarding_complete: bool


class ShopSessionResponse(BaseModel):
    session_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: str | None
    is_new_user: bool
    needs_onboarding: bool
    tenants: list[TenantSummaryResponse]


class SessionResponse(BaseModel):
    """Tokens plus the state the client needs to route its next screen."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in_seconds: int
    session_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: str | None
    is_new_user: bool
    needs_onboarding: bool
    tenants: list[TenantSummaryResponse]


class RegisterPayload(BaseModel):
    """Create an account from an email and a password."""

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=160)
    device: DevicePayload = Field(default_factory=DevicePayload)


class RegisterResponse(BaseModel):
    """The new session, plus what the client should say about verification."""

    session: SessionResponse
    email_verification_sent: bool
    verification_token: str | None = Field(
        default=None,
        description=(
            "The raw verification token. Present only in local/test environments "
            "where no email provider is configured, so the flow is testable "
            "without one; always null in staging and production."
        ),
    )


class LoginPayload(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    device: DevicePayload = Field(default_factory=DevicePayload)


class EmailPayload(BaseModel):
    """Used by the two flows that must not reveal whether an account exists."""

    email: str = Field(min_length=3, max_length=254)


class AuthTokenPayload(BaseModel):
    """A verification or reset link token."""

    token: str = Field(min_length=16, max_length=400)


class PasswordResetPayload(AuthTokenPayload):
    new_password: str = Field(min_length=1, max_length=200)


class PasswordChangePayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class ProviderSignInPayload(BaseModel):
    """A Google or Apple identity token, to be verified server-side.

    There is deliberately no ``email``, ``user_id`` or ``verified`` field: every
    fact about the person comes out of the token after the server checks it,
    never out of the request body.
    """

    id_token: str = Field(min_length=16, max_length=8192)
    device: DevicePayload = Field(default_factory=DevicePayload)


class AcknowledgedResponse(BaseModel):
    """A deliberately uninformative success.

    Forgot-password and resend-verification return this whether or not the
    address has an account. Saying anything more precise would turn either
    endpoint into a way to test a leaked address list against ecomsbd's sellers.
    """

    acknowledged: bool = True
    #: Local/test only, for the same reason as ``RegisterResponse.verification_token``.
    debug_token: str | None = None


# --------------------------------------------------------------------------- #
# Tenant / me
# --------------------------------------------------------------------------- #


class ShopCreatePayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    business_category: BusinessCategory = BusinessCategory.OTHER
    pickup_contact_name: str | None = Field(default=None, max_length=160)
    pickup_phone: str | None = Field(default=None, max_length=24)
    pickup_address: str | None = Field(default=None, max_length=1000)
    pickup_district: str | None = Field(default=None, max_length=80)
    pickup_area: str | None = Field(default=None, max_length=120)


class ShopUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    business_category: BusinessCategory | None = None
    pickup_contact_name: str | None = Field(default=None, max_length=160)
    pickup_phone: str | None = Field(default=None, max_length=24)
    pickup_address: str | None = Field(default=None, max_length=1000)
    pickup_district: str | None = Field(default=None, max_length=80)
    pickup_area: str | None = Field(default=None, max_length=120)
    onboarding_step: OnboardingStep | None = None


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    business_category: str
    status: str
    timezone: str
    currency: str
    order_number_prefix: str
    pickup_contact_name: str | None
    pickup_address_raw: str | None
    pickup_district: str | None
    pickup_area: str | None
    onboarding_step: str
    onboarding_completed_at: datetime | None
    created_at: datetime


class MeResponse(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    masked_phone: str | None
    locale: str
    session_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: str | None
    permissions: list[str]
    tenants: list[TenantSummaryResponse]
    needs_onboarding: bool


# --------------------------------------------------------------------------- #
# Billing / providers
# --------------------------------------------------------------------------- #


class PlanResponse(BaseModel):
    code: str
    name: str
    price: MoneyAmount
    entitlements: dict[str, Any]


class EntitlementsResponse(BaseModel):
    free_launch_mode: bool = False
    billing_enabled: bool = False
    effective_access: str = "plan"
    plan: str
    status: str
    source: str | None
    valid_until: datetime | None
    entitlements: dict[str, Any]


class ProviderResponse(BaseModel):
    """A courier provider and what it is *verified* to support.

    The client drives its UI from ``capabilities`` rather than the provider
    name, so a provider without a payout API shows the statement-upload path
    instead of a button that cannot work (master spec section 75).
    """

    provider: str
    display_name: str
    verified_at: str | None
    capabilities: dict[str, str]
    manual_fallback: str | None
    fully_unverified: bool
    enabled: bool
    #: Which revision of the provider's documentation the capabilities were
    #: read from. Shown in settings so a seller filing a support ticket and the
    #: engineer reading it are talking about the same document.
    documentation_version: str | None = None
    #: Named things the provider's documentation does not say, so the UI can be
    #: honest about a gap rather than rendering an empty state that looks like
    #: a bug (for example: no webhook contract, so status arrives by polling).
    unknowns: dict[str, str] = Field(default_factory=dict)
    #: How to connect this provider: which fields to ask for, in both languages,
    #: and whether it needs a pickup store, a sandbox toggle or a webhook
    #: secret. The client renders the connect form from this rather than
    #: carrying one hand-written form per provider, so adding RedX does not mean
    #: shipping a new app build. Contains no value — only the shape of the form.
    connect_form: dict[str, Any] | None = None
