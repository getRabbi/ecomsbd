"""Shop referrals (V4 WP7.4).

``GET /referrals`` — whether referrals are on for this shop, its code, who
referred it and the shops that joined with its code (states and dates only:
another shop's name never reaches this shop). ``POST /referrals/code`` makes
the code on first use. ``POST /referrals/apply`` joins with another shop's
code, before this shop's first payment. Owner only (``billing.manage``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from app.api.deps import (
    DbSession,
    DistributionChannelDep,
    SettingsDep,
    TenantPrincipal,
    require_permission,
)
from app.referrals.models import Referral
from app.referrals.service import CREDIT_DAYS, ReferralService, ReferredBy
from app.tenants.roles import Permission

router = APIRouter(prefix="/referrals", tags=["referrals"])

BillingManager = Annotated[object, Depends(require_permission(Permission.BILLING_MANAGE))]


def _service(db: DbSession, settings: SettingsDep) -> ReferralService:
    return ReferralService(db, settings=settings)


ReferralsDep = Annotated[ReferralService, Depends(_service)]


class ReferredByResponse(BaseModel):
    code: str
    state: str
    #: This shop's own 30 days: PENDING, GRANTED.
    credit_state: str
    applied_at: datetime
    rewarded_at: datetime | None


class ReferralRowResponse(BaseModel):
    id: uuid.UUID
    state: str
    #: The referrer's own 30 days: PENDING, GRANTED or HELD (support decides).
    credit_state: str
    joined_at: datetime
    rewarded_at: datetime | None


class ReferralOverviewResponse(BaseModel):
    available: bool
    unavailable_reason: str | None
    credit_days: int
    code: str | None = None
    referred_by: ReferredByResponse | None = None
    referrals: list[ReferralRowResponse] = []


class ReferralCodeResponse(BaseModel):
    code: str
    credit_days: int


class ReferralApplyPayload(BaseModel):
    code: str = Field(min_length=1, max_length=32)


def _referred_by(row: ReferredBy) -> ReferredByResponse:
    return ReferredByResponse(
        code=row.code,
        state=row.state,
        credit_state=row.credit_state,
        applied_at=row.applied_at,
        rewarded_at=row.rewarded_at,
    )


def _row(row: Referral) -> ReferralRowResponse:
    return ReferralRowResponse(
        id=row.id,
        state=row.state,
        credit_state=row.referrer_credit_state,
        joined_at=row.created_at,
        rewarded_at=row.rewarded_at,
    )


@router.get("", response_model=ReferralOverviewResponse, summary="This shop's referrals")
async def get_referrals(
    principal: TenantPrincipal,
    _: BillingManager,
    service: ReferralsDep,
    channel: DistributionChannelDep,
) -> ReferralOverviewResponse:
    """Off (and empty) unless the flag is on and this shop could pay by TrxID —
    the credit only ever follows such a payment."""
    tenant_id = principal.require_tenant()
    availability = await service.availability(tenant_id, channel)
    if not availability.available:
        return ReferralOverviewResponse(
            available=False,
            unavailable_reason=availability.reason,
            credit_days=CREDIT_DAYS,
        )
    overview = await service.overview(tenant_id)
    return ReferralOverviewResponse(
        available=True,
        unavailable_reason=None,
        credit_days=CREDIT_DAYS,
        code=overview.code,
        referred_by=_referred_by(overview.referred_by) if overview.referred_by else None,
        referrals=[_row(row) for row in overview.referrals],
    )


@router.post(
    "/code",
    response_model=ReferralCodeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="This shop's referral code (made on first use)",
)
async def make_code(
    response: Response,
    principal: TenantPrincipal,
    _: BillingManager,
    service: ReferralsDep,
    channel: DistributionChannelDep,
) -> ReferralCodeResponse:
    row, created = await service.ensure_code(
        tenant_id=principal.require_tenant(), actor_id=principal.user_id, channel=channel
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return ReferralCodeResponse(code=row.code, credit_days=CREDIT_DAYS)


@router.post(
    "/apply",
    response_model=ReferredByResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Join with another shop's referral code",
)
async def apply_code(
    payload: ReferralApplyPayload,
    response: Response,
    principal: TenantPrincipal,
    _: BillingManager,
    service: ReferralsDep,
    channel: DistributionChannelDep,
) -> ReferredByResponse:
    """Before this shop's first payment only. The same code again returns the
    first answer (200); refusals carry ``details.code``: REFERRAL_CODE_INVALID,
    REFERRAL_CODE_UNKNOWN, REFERRAL_OWN_SHOP, REFERRAL_SAME_OWNER,
    REFERRAL_CYCLE (422); REFERRAL_ALREADY_APPLIED,
    REFERRAL_AFTER_FIRST_PAYMENT (409)."""
    row, created = await service.apply(
        tenant_id=principal.require_tenant(),
        actor_id=principal.user_id,
        channel=channel,
        raw_code=payload.code,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return _referred_by(row)
