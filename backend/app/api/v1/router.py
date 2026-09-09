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
    auth,
    billing,
    customers,
    imports,
    orders,
    products,
    providers,
    sync,
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
api_router.include_router(providers.router)
api_router.include_router(billing.router)

__all__ = ["api_router"]
