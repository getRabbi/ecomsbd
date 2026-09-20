"""The /v1 router.

Modules for risk, money, analytics, exports and admin are registered here as
they ship, in the phase that owns them (master spec section 139). They are
deliberately absent rather than stubbed: an endpoint that exists and returns
nothing is indistinguishable, from the client's side, from one that works and
has no data.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    account,
    admin,
    analytics,
    auth,
    billing,
    consignments,
    courier_webhooks,
    couriers,
    customers,
    imports,
    insights,
    money,
    orders,
    payouts,
    products,
    providers,
    reconciliation,
    rto,
    sync,
    team,
    tenants,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tenants.router)
api_router.include_router(products.router)
api_router.include_router(customers.router)
api_router.include_router(orders.router)
api_router.include_router(imports.router)
api_router.include_router(sync.router)
api_router.include_router(consignments.router)
api_router.include_router(money.router)
api_router.include_router(payouts.router)
api_router.include_router(reconciliation.router)
api_router.include_router(providers.router)
api_router.include_router(couriers.router)
# Unauthenticated by necessity: a provider callback carries no session. The
# Steadfast route stores what it receives and processes nothing, because no
# verified signature contract exists (STEADFAST_WEBHOOK_CONTRACT_REQUIRED).
api_router.include_router(courier_webhooks.router)
api_router.include_router(billing.router)
api_router.include_router(analytics.router)
api_router.include_router(rto.router)
api_router.include_router(insights.router)
api_router.include_router(team.router)
api_router.include_router(account.account_router)
api_router.include_router(account.export_router)
# Platform administration. Authenticated by X-Admin-Token, never by a
# seller session, and excluded from the public schema.
api_router.include_router(admin.router)

__all__ = ["api_router"]
