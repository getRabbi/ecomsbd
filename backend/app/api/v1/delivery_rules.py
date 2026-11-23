"""Shop delivery rules (V4 WP0.4).

``GET /v1/shop/delivery-rules`` returns the effective rules — the defaults when
the shop has never saved any (``configured: false``), without writing a row.
``PATCH`` changes only the fields sent. Both need ``settings.manage`` and the
``delivery_rules_enabled`` flag; with the flag off they answer
``FEATURE_DISABLED`` and clients hide the screen.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.common.audit import AuditAction, record_audit
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.core.errors import AppError, ConflictError, ErrorCode
from app.delivery_rules.service import DeliveryRules, DeliveryRulesService
from app.tenants.roles import Permission

router = APIRouter(prefix="/shop", tags=["shop"])

_MAX_CHARGE = 1_000_000  # ৳10,000 — far above any real delivery charge
_MAX_COD = 100_000_000  # ৳10,00,000
_HHMM = r"^(?:[01]\d|2[0-3]):[0-5]\d$"


class DeliveryRulesResponse(BaseModel):
    configured: bool
    inside_dhaka_charge_paisa: int
    dhaka_suburb_charge_paisa: int
    outside_dhaka_charge_paisa: int
    default_courier: str | None
    max_auto_cod_paisa: int
    risk_low_bps: int
    trusted_network_bps: int
    trusted_repeat_min: int
    quiet_start: str
    quiet_end: str
    autopilot_daily_cap: int
    call_everyone: bool
    call_nobody: bool


class DeliveryRulesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inside_dhaka_charge_paisa: int | None = Field(default=None, ge=0, le=_MAX_CHARGE)
    dhaka_suburb_charge_paisa: int | None = Field(default=None, ge=0, le=_MAX_CHARGE)
    outside_dhaka_charge_paisa: int | None = Field(default=None, ge=0, le=_MAX_CHARGE)
    default_courier: str | None = Field(default=None, max_length=32)
    max_auto_cod_paisa: int | None = Field(default=None, ge=0, le=_MAX_COD)
    risk_low_bps: int | None = Field(default=None, ge=0, le=10_000)
    trusted_network_bps: int | None = Field(default=None, ge=0, le=10_000)
    trusted_repeat_min: int | None = Field(default=None, ge=0, le=100)
    quiet_start: str | None = Field(default=None, pattern=_HHMM)
    quiet_end: str | None = Field(default=None, pattern=_HHMM)
    autopilot_daily_cap: int | None = Field(default=None, ge=0, le=10_000)
    call_everyone: bool | None = None
    call_nobody: bool | None = None


#: Fields whose ``null`` means "clear it"; for every other field ``null`` is refused.
_NULLABLE = frozenset({"default_courier"})


def _response(rules: DeliveryRules) -> DeliveryRulesResponse:
    return DeliveryRulesResponse(configured=rules.configured, **rules.values)


async def _require_flag(db: DbSession, principal: TenantPrincipal) -> None:
    if not await FeatureFlagService(db).is_enabled(
        FlagKey.DELIVERY_RULES_ENABLED, tenant_id=principal.require_tenant()
    ):
        raise AppError("Delivery rules are not enabled", code=ErrorCode.FEATURE_DISABLED)


@router.get(
    "/delivery-rules",
    response_model=DeliveryRulesResponse,
    summary="The shop's delivery rules",
    dependencies=[Depends(require_permission(Permission.SETTINGS_MANAGE))],
)
async def get_delivery_rules(db: DbSession, principal: TenantPrincipal) -> DeliveryRulesResponse:
    await _require_flag(db, principal)
    return _response(await DeliveryRulesService(db).get(principal.require_tenant()))


@router.patch(
    "/delivery-rules",
    response_model=DeliveryRulesResponse,
    summary="Change the shop's delivery rules",
    dependencies=[Depends(require_permission(Permission.SETTINGS_MANAGE))],
)
async def update_delivery_rules(
    payload: DeliveryRulesPatch, db: DbSession, principal: TenantPrincipal
) -> DeliveryRulesResponse:
    await _require_flag(db, principal)
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    if not changes:
        raise ConflictError("No fields to update")
    for key, value in changes.items():
        if value is None and key not in _NULLABLE:
            raise AppError(
                "This rule cannot be empty",
                code=ErrorCode.VALIDATION_ERROR,
                details={"field": key},
            )
    if isinstance(changes.get("default_courier"), str):
        changes["default_courier"] = changes["default_courier"].strip().lower() or None

    tenant_id = principal.require_tenant()
    rules = await DeliveryRulesService(db).update(tenant_id, changes)
    await record_audit(
        db,
        AuditAction.DELIVERY_RULES_UPDATED,
        entity_type="shop_delivery_rules",
        entity_id=tenant_id,
        context={"fields": sorted(changes)},
        tenant_id=tenant_id,
    )
    return _response(rules)


__all__ = ["router"]
