"""Admin console wire types.

Every response here is operator-facing. Two rules:

*   a phone number is masked unless it came from the explicit reveal route;
*   nothing carries a credential, a provider payload or a purchase token.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.intake.rules_schema import IntakeRulesV1

__all__ = [
    "AdminIdentityResponse",
    "IntakeRuleSetResponse",
    "IntakeRulesActivatePayload",
    "IntakeRulesCreatePayload",
    "ManualPaymentApprovePayload",
    "ManualPaymentRejectPayload",
    "ManualPaymentReviewResponse",
    "OpsCountsResponse",
    "ProviderHealthResponse",
    "RepairActionResponse",
    "RepairRequestPayload",
    "RepairResultResponse",
    "RevealPhonePayload",
    "RevealPhoneResponse",
    "SupportCaseCreatePayload",
    "SupportCaseResponse",
    "SupportCaseUpdatePayload",
    "TenantSummaryResponse",
]


class AdminIdentityResponse(BaseModel):
    label: str
    role: str
    is_bootstrap: bool
    permissions: list[str]


class TenantSummaryResponse(BaseModel):
    tenant_id: uuid.UUID
    name: str
    status: str
    business_category: str
    created_at: datetime
    plan: str
    subscription_status: str
    #: ``*******78``. The console never lists a dialable number.
    owner_masked_phone: str | None
    order_count: int


class FunnelStepResponse(BaseModel):
    name: str
    shops: int


class FunnelResponse(BaseModel):
    from_date: date = Field(serialization_alias="from")
    to_date: date = Field(serialization_alias="to")
    steps: list[FunnelStepResponse]


class OpsCountsResponse(BaseModel):
    booking_unknown: int
    reconciliation_cases_open: int
    payout_imports_unparsed: int
    billing_webhook_failures: int
    billing_verification_failures: int
    outbox_dead_letters: int
    sync_conflicts: int
    notifications_unsent: int
    admin_repairs_today: int


class ProviderHealthResponse(BaseModel):
    kind: str
    provider: str
    capability: str
    state: str
    breaker_state: str
    success_count: int
    error_count: int
    auth_failure_count: int
    latency_ms: int
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error_code: str | None


class RevealPhonePayload(BaseModel):
    """Section 101: a reveal states why."""

    reason: str = Field(min_length=8, max_length=400)


class RevealPhoneResponse(BaseModel):
    phone: str
    reason: str
    #: How long the console should keep it on screen before re-hiding it.
    expires_in_seconds: int


class SupportCaseCreatePayload(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    case_type: str = Field(default="OTHER", max_length=24)
    severity: str = Field(default="NORMAL", max_length=12)
    tenant_id: uuid.UUID | None = None
    detail: str | None = Field(default=None, max_length=4000)
    order_id: uuid.UUID | None = None
    consignment_id: uuid.UUID | None = None
    payout_id: uuid.UUID | None = None
    reconciliation_case_id: uuid.UUID | None = None
    subscription_id: uuid.UUID | None = None


class SupportCaseUpdatePayload(BaseModel):
    status: str | None = Field(default=None, max_length=24)
    severity: str | None = Field(default=None, max_length=12)
    assigned_to: str | None = Field(default=None, max_length=120)
    resolution: str | None = Field(default=None, max_length=4000)


class SupportCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str
    tenant_id: uuid.UUID | None
    case_type: str
    severity: str
    status: str
    subject: str
    detail: str | None
    assigned_to: str | None
    resolution: str | None
    created_at: datetime
    resolved_at: datetime | None


class RepairActionResponse(BaseModel):
    name: str
    summary: str
    permission: str
    is_sensitive: bool
    #: Whether *this* caller may run it. A listed-but-not-permitted action is
    #: more useful than a hidden one: the operator knows who to ask.
    permitted: bool


class RepairRequestPayload(BaseModel):
    """Section 103: every repair states why."""

    reason: str = Field(min_length=8, max_length=1000)
    tenant_id: uuid.UUID | None = None
    target_type: str | None = Field(default=None, max_length=60)
    target_id: str | None = Field(default=None, max_length=80)
    #: Supplied by the console so a double-click cannot run a reversal twice.
    idempotency_key: str | None = Field(default=None, max_length=200)
    params: dict[str, Any] | None = None


class RepairResultResponse(BaseModel):
    id: uuid.UUID
    action: str
    outcome: str
    detail: str
    data: dict[str, Any]
    reason: str
    admin_label: str
    created_at: datetime


class IntakeRulesCreatePayload(BaseModel):
    """V4 WP1.2: a new rules version. It is stored inactive."""

    model_config = ConfigDict(extra="forbid")

    payload: IntakeRulesV1
    note: str | None = Field(default=None, max_length=200)


class IntakeRulesActivatePayload(BaseModel):
    """Every device of every shop follows the active version, so say why."""

    reason: str = Field(min_length=8, max_length=1000)


class IntakeRuleSetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    schema_version: int
    active: bool
    payload: dict[str, Any]
    created_by: str
    note: str | None
    created_at: datetime
    activated_at: datetime | None
    activated_by: str | None


class ManualPaymentReviewResponse(BaseModel):
    """A seller's bKash / Nagad TrxID submission (V4 WP2.5). Payer number masked."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_code: str
    months: int
    amount_paisa: int
    method: str
    payer_number_masked: str
    trx_id: str
    state: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    reason: str | None = None
    period_end: datetime | None = None
    subscription_id: uuid.UUID | None = None
    created_at: datetime


class ManualPaymentApprovePayload(BaseModel):
    note: str | None = Field(default=None, max_length=300)


class ManualPaymentRejectPayload(BaseModel):
    """Shown to the seller, so write it for them."""

    reason: str = Field(min_length=3, max_length=400)


class VoiceProviderConnectionView(BaseModel):
    """The platform's voice account as the console sees it (V4 WP3.1). No secrets."""

    enabled: bool
    has_credentials: bool
    has_callback_secret: bool
    masked_identifier: str | None
    caller_id: str | None
    credentials_rotated_at: datetime | None
    updated_at: datetime | None
    updated_by: str


class VoiceProviderResponse(BaseModel):
    provider: str
    #: ``None`` when a connection outlived its manifest (contract withdrawn).
    manifest: dict[str, Any] | None
    connection: VoiceProviderConnectionView | None
    health: list[ProviderHealthResponse]


class VoiceIntroAdminView(BaseModel):
    """One version of a shop's call intro (V4 WP3.3). No audio, no secrets."""

    model_config = ConfigDict(from_attributes=True)

    version: int
    state: str
    source: str
    shop_name: str
    content_type: str
    byte_size: int
    sha256: str
    duration_ms: int | None
    created_by: str
    created_at: datetime
    previewed_at: datetime | None
    approved_at: datetime | None
    retired_at: datetime | None
    retired_reason: str | None
    #: Providers that already hold this recording (names only).
    uploaded_to: list[str]


class VoiceIntrosAdminResponse(BaseModel):
    tenant_id: uuid.UUID
    shop_name: str
    #: What to record for the shop's current name.
    script: str
    versions: list[VoiceIntroAdminView]


class VoiceProviderUpdatePayload(BaseModel):
    """Connect, rotate or switch. An omitted secret keeps the stored one."""

    credentials: dict[str, str] | None = None
    callback_secret: str | None = Field(default=None, min_length=16, max_length=4096)
    caller_id: str | None = Field(default=None, max_length=20)
    enabled: bool = False
    reason: str = Field(min_length=8, max_length=400)
