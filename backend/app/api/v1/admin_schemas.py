"""Admin console wire types.

Every response here is operator-facing. Two rules:

*   a phone number is masked unless it came from the explicit reveal route;
*   nothing carries a credential, a provider payload or a purchase token.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AdminIdentityResponse",
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
