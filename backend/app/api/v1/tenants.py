"""Identity and shop endpoints.

Master spec section 39::

    GET   /v1/me
    GET   /v1/tenant
    PATCH /v1/tenant

Shop creation (``POST /v1/tenants``) is the onboarding step that turns a
signed-in phone number into a working shop.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    AuthServiceDep,
    CurrentPrincipal,
    DbSession,
    SettingsDep,
    TenantPrincipal,
    get_vault,
    require_permission,
)
from app.api.v1.auth import _to_session_response
from app.api.v1.schemas import (
    MeResponse,
    SessionResponse,
    ShopCreatePayload,
    ShopUpdatePayload,
    TenantResponse,
    TenantSummaryResponse,
)
from app.core.errors import ConflictError
from app.tenants.models import Tenant
from app.tenants.roles import Permission, permissions_for
from app.tenants.service import TenantService

router = APIRouter(tags=["tenant"])


async def _tenant_service(db: DbSession, settings: SettingsDep) -> TenantService:
    return TenantService(db, vault=get_vault(settings))


TenantServiceDep = Annotated[TenantService, Depends(_tenant_service)]


@router.get("/me", response_model=MeResponse, summary="The signed-in user and their shops")
async def get_me(principal: CurrentPrincipal, auth: AuthServiceDep) -> MeResponse:
    memberships = await auth.list_memberships(principal.user_id)
    active = next((m for m in memberships if m.id == principal.tenant_id), None)
    return MeResponse(
        user_id=principal.user_id,
        display_name=principal.user.display_name,
        masked_phone=principal.user.masked_phone,
        locale=principal.user.locale,
        session_id=principal.session_id,
        tenant_id=principal.tenant_id,
        role=str(principal.role) if principal.role else None,
        permissions=sorted(str(p) for p in permissions_for(principal.role))
        if principal.role
        else [],
        tenants=[
            TenantSummaryResponse(
                id=m.id, name=m.name, role=m.role, onboarding_complete=m.onboarding_complete
            )
            for m in memberships
        ],
        needs_onboarding=active is None or not active.onboarding_complete,
    )


@router.post(
    "/tenants",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a shop and attach the current session to it",
)
async def create_shop(
    payload: ShopCreatePayload,
    principal: CurrentPrincipal,
    tenants: TenantServiceDep,
    auth: AuthServiceDep,
) -> SessionResponse:
    """Create the seller's shop.

    Returns a fresh token pair bound to the new shop, so the client moves
    straight into the dashboard without a second sign-in round trip.
    """
    tenant = await tenants.create_shop(
        owner_user_id=principal.user_id,
        name=payload.name,
        business_category=payload.business_category,
        pickup_contact_name=payload.pickup_contact_name,
        pickup_phone=payload.pickup_phone,
        pickup_address=payload.pickup_address,
        pickup_district=payload.pickup_district,
        pickup_area=payload.pickup_area,
    )
    result = await auth.bind_session_tenant(session_id=principal.session_id, tenant_id=tenant.id)
    return _to_session_response(result)


@router.get("/tenant", response_model=TenantResponse, summary="The active shop")
async def get_tenant(principal: TenantPrincipal, tenants: TenantServiceDep) -> Tenant:
    return await tenants.get(principal.require_tenant())


@router.patch(
    "/tenant",
    response_model=TenantResponse,
    summary="Update the active shop",
    dependencies=[Depends(require_permission(Permission.SETTINGS_MANAGE))],
)
async def update_tenant(
    payload: ShopUpdatePayload,
    principal: TenantPrincipal,
    tenants: TenantServiceDep,
) -> Tenant:
    tenant_id = principal.require_tenant()
    if not payload.model_dump(exclude_unset=True):
        raise ConflictError("No fields to update")
    return await tenants.update(
        tenant_id,
        name=payload.name,
        business_category=payload.business_category,
        pickup_contact_name=payload.pickup_contact_name,
        pickup_phone=payload.pickup_phone,
        pickup_address=payload.pickup_address,
        pickup_district=payload.pickup_district,
        pickup_area=payload.pickup_area,
        onboarding_step=payload.onboarding_step,
    )


__all__ = ["router"]
