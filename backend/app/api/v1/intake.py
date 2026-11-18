"""Intake rules for devices and the seller's per-app choices (V4 WP1.2).

* ``GET /v1/intake/rules`` — ingest key. The active rule set with this shop's
  overrides applied. Strong ETag; a matching ``If-None-Match`` is a 304, so a
  device refreshing daily costs one tiny response.
* ``GET/PATCH /v1/intake/settings`` — seller session (``settings.manage``):
  which apps the shop's linked phones read. ``covered_packages`` is shown but
  only the server sets it.

All behind ``intake_device_enabled`` (the ingest-key dependency checks it too).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.common.audit import AuditAction, record_audit
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.core.errors import AppError, ErrorCode
from app.device_links.auth import IngestPrincipal, require_ingest_key
from app.device_links.models import INTAKE_CAPTURE
from app.intake import rules
from app.intake.models import IntakeRuleSet, ShopIntakeSettings
from app.tenants.roles import Permission

router = APIRouter(prefix="/intake", tags=["intake"])

_MANAGE = [Depends(require_permission(Permission.SETTINGS_MANAGE))]
IngestCapture = Annotated[IngestPrincipal, Depends(require_ingest_key(INTAKE_CAPTURE))]


class IntakeRulesResponse(BaseModel):
    """Documentation of the 200 body; the bytes are served canonically."""

    version: int
    schema_version: int
    rules: dict[str, Any]
    district_lists: dict[str, dict[str, list[str]]]
    enabled_packages: list[str]
    covered_packages: list[str]
    refresh_after_seconds: int


class PackageSettingResponse(BaseModel):
    package: str
    label: str
    default_enabled: bool
    toggle: bool | None
    covered: bool
    enabled: bool


class IntakeSettingsResponse(BaseModel):
    rules_version: int
    packages: list[PackageSettingResponse]


class IntakeSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ``true``/``false`` = the seller's choice; ``null`` = back to the default.
    package_toggles: dict[str, bool | None] = Field(min_length=1, max_length=20)


async def _require_flag(db: DbSession, principal: TenantPrincipal) -> None:
    if not await FeatureFlagService(db).is_enabled(
        FlagKey.INTAKE_DEVICE_ENABLED, tenant_id=principal.require_tenant()
    ):
        raise AppError("Linked devices are not enabled", code=ErrorCode.FEATURE_DISABLED)


def _settings(rule_set: IntakeRuleSet, row: ShopIntakeSettings | None) -> IntakeSettingsResponse:
    return IntakeSettingsResponse(
        rules_version=rule_set.version,
        packages=[
            PackageSettingResponse(
                package=state.package,
                label=state.label,
                default_enabled=state.default_enabled,
                toggle=state.toggle,
                covered=state.covered,
                enabled=state.enabled,
            )
            for state in rules.package_states(rule_set.payload, row)
        ],
    )


@router.get(
    "/rules",
    response_model=IntakeRulesResponse,
    summary="Detection rules for this device",
    responses={304: {"description": "Unchanged since the ETag in If-None-Match"}},
)
async def get_device_rules(
    device: IngestCapture,
    db: DbSession,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    rule_set = await rules.active_rule_set(db)
    result = rules.device_rules(rule_set, await rules.shop_settings(db, device.tenant_id))
    headers = {"ETag": result.etag, "Cache-Control": "private, no-cache"}
    if rules.etag_matches(if_none_match, result.etag):
        return Response(status_code=304, headers=headers)
    return Response(content=result.body, media_type="application/json", headers=headers)


@router.get(
    "/settings",
    response_model=IntakeSettingsResponse,
    summary="Which apps the shop's phones read",
    dependencies=_MANAGE,
)
async def get_intake_settings(db: DbSession, principal: TenantPrincipal) -> IntakeSettingsResponse:
    await _require_flag(db, principal)
    rule_set = await rules.active_rule_set(db)
    return _settings(rule_set, await rules.shop_settings(db, principal.require_tenant()))


@router.patch(
    "/settings",
    response_model=IntakeSettingsResponse,
    summary="Turn reading an app on or off",
    dependencies=_MANAGE,
)
async def update_intake_settings(
    payload: IntakeSettingsPatch, db: DbSession, principal: TenantPrincipal
) -> IntakeSettingsResponse:
    await _require_flag(db, principal)
    tenant_id = principal.require_tenant()
    rule_set = await rules.active_rule_set(db)
    row = await rules.update_toggles(
        db,
        tenant_id,
        payload.package_toggles,
        known_packages=[rule["package"] for rule in rule_set.payload["packages"]],
    )
    await record_audit(
        db,
        AuditAction.INTAKE_SETTINGS_UPDATED,
        entity_type="shop_intake_settings",
        entity_id=row.id,
        context={"packages": sorted(payload.package_toggles)},
        tenant_id=tenant_id,
    )
    return _settings(rule_set, row)


__all__ = ["router"]
