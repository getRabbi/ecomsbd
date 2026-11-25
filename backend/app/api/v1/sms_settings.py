"""The shop's SMS switches (launch run 14).

``GET /v1/shop/sms-settings`` and ``PATCH``: the shop's own opt-in for the SMS
confirm link. With the platform flag ``confirmation_sms_link_enabled`` on, a
customer gets a link only from a shop that switched it on here (default off),
because every link is an SMS that costs from the SMS balance.

``available`` is the platform side (flag, a sending SMS transport,
``PUBLIC_WEB_URL``); ``enabled`` is the shop's switch. Clients show the switch
only while it is available. Order-update SMS keep their own switch, the
messaging channel ``SMS`` (``POST /v1/messaging/channels/SMS``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.common.audit import AuditAction, record_audit
from app.confirmations.links import SHOP_LINK_SWITCH, links_available, shop_link_switch
from app.core.errors import AppError, ErrorCode
from app.tenants.models import Tenant
from app.tenants.roles import Permission

router = APIRouter(prefix="/shop", tags=["shop"])


class SmsSwitch(BaseModel):
    available: bool
    enabled: bool


class SmsSettingsResponse(BaseModel):
    confirmation_link: SmsSwitch


class SmsSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_link_enabled: bool


async def _response(db: DbSession, principal: TenantPrincipal) -> SmsSettingsResponse:
    tenant_id = principal.require_tenant()
    return SmsSettingsResponse(
        confirmation_link=SmsSwitch(
            available=await links_available(db, tenant_id),
            enabled=await shop_link_switch(db, tenant_id),
        )
    )


@router.get(
    "/sms-settings",
    response_model=SmsSettingsResponse,
    summary="The shop's SMS switches",
    dependencies=[Depends(require_permission(Permission.SETTINGS_MANAGE))],
)
async def get_sms_settings(db: DbSession, principal: TenantPrincipal) -> SmsSettingsResponse:
    return await _response(db, principal)


@router.patch(
    "/sms-settings",
    response_model=SmsSettingsResponse,
    summary="Switch the SMS confirm link on or off for this shop",
    dependencies=[Depends(require_permission(Permission.SETTINGS_MANAGE))],
)
async def update_sms_settings(
    payload: SmsSettingsPatch, db: DbSession, principal: TenantPrincipal
) -> SmsSettingsResponse:
    tenant_id = principal.require_tenant()
    enabled = payload.confirmation_link_enabled
    if enabled and not await links_available(db, tenant_id):
        raise AppError("The SMS confirm link is not available yet", code=ErrorCode.FEATURE_DISABLED)
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:  # pragma: no cover - the principal's own shop
        raise AppError("Shop not found", code=ErrorCode.NOT_FOUND)
    before = (tenant.settings or {}).get(SHOP_LINK_SWITCH) is True
    if before != enabled:
        # A new dict, so the JSON column is written.
        tenant.settings = {**(tenant.settings or {}), SHOP_LINK_SWITCH: enabled}
        await db.flush()
        await record_audit(
            db,
            AuditAction.SMS_SETTINGS_UPDATED,
            entity_type="tenant",
            entity_id=tenant_id,
            context={"confirmation_link_enabled": enabled},
            tenant_id=tenant_id,
        )
    return await _response(db, principal)


__all__ = ["router"]
