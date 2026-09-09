"""Billing read endpoints.

Master spec section 39 lists five billing routes. Two of them are read-only and
ship here::

    GET /v1/billing/entitlements
    GET /v1/billing/plans

The three that move money — ``POST /v1/billing/play/verify``,
``POST /v1/billing/web/checkout`` and ``POST /v1/billing/webhooks/{provider}`` —
are Phase F. They require a Play package name and service account, and a bKash
merchant contract, neither of which exists yet. Section 90 is explicit that a
paid entitlement must never be granted from a client callback, so shipping a
stub that accepts a purchase token would be worse than shipping nothing.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import EntitlementsDep, TenantPrincipal
from app.api.v1.schemas import EntitlementsResponse, MoneyAmount, PlanResponse
from app.entitlements.catalog import PLANS

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get(
    "/entitlements",
    response_model=EntitlementsResponse,
    summary="The active shop's plan and entitlements",
)
async def get_entitlements(
    principal: TenantPrincipal,
    entitlements: EntitlementsDep,
) -> EntitlementsResponse:
    """Server-authoritative entitlement state.

    The client uses this to shape its UI. It is never the basis of an
    authorisation decision: every paid operation re-checks server-side
    (master spec section 27.3).
    """
    snapshot = await entitlements.snapshot(principal.require_tenant())
    return EntitlementsResponse(
        plan=str(snapshot.plan),
        status=snapshot.status,
        source=snapshot.source,
        valid_until=snapshot.valid_until,
        entitlements=snapshot.entitlements,
    )


@router.get("/plans", response_model=list[PlanResponse], summary="Available plans")
async def list_plans(principal: TenantPrincipal) -> list[PlanResponse]:
    """Plan catalogue.

    Prices are the section 26 hypothesis pending seller validation. Purchase
    flows are not exposed here; the client must use the billing channel allowed
    for its distribution source (master spec section 27).
    """
    return [
        PlanResponse(
            code=str(plan.code),
            name=plan.name,
            price=MoneyAmount(amount_paisa=plan.price_paisa),
            entitlements={str(k): v for k, v in plan.entitlements.items()},
        )
        for plan in PLANS.values()
    ]


__all__ = ["router"]
