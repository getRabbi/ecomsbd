"""Autopilot (V4 Phase 4): the shop's switch, its checklist and the Home card.

* ``GET /v1/autopilot`` — the switch, the shop-gate checklist (one line per
  condition, re-checked on every run) and whether the Autopilot workflow is on.
* ``PUT /v1/autopilot`` — turn on (needs every line; ``409 AUTOPILOT_NOT_READY``
  lists the failing ones) or off, record consent, accept manual-queue mode.
* ``POST /v1/autopilot/pause`` — "Pause 1 hour": nothing converts and queued
  IVR calls hold; ``POST /v1/autopilot/resume`` ends it early.
* ``GET /v1/autopilot/today`` — today's count, confirmed, cancelled, waiting,
  and the last five decisions with their reason.

Every route needs the ``autopilot_enabled`` flag (the global kill switch);
off, they answer ``FEATURE_DISABLED`` and clients hide Autopilot.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.autopilot import service
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.core.errors import AppError, ErrorCode
from app.tenants.roles import Permission

router = APIRouter(prefix="/autopilot", tags=["autopilot"])


class AutopilotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool | None = None
    manual_queue: bool | None = None
    #: The seller read what Autopilot does and agreed (recorded once).
    consent: bool = False


async def _require_flag(db: DbSession, principal: TenantPrincipal) -> None:
    if not await FeatureFlagService(db).is_enabled(
        FlagKey.AUTOPILOT_ENABLED, tenant_id=principal.require_tenant()
    ):
        raise AppError("Autopilot is not enabled", code=ErrorCode.FEATURE_DISABLED)


@router.get(
    "",
    summary="Autopilot switch and checklist",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def get_autopilot(db: DbSession, principal: TenantPrincipal) -> dict[str, Any]:
    await _require_flag(db, principal)
    return await service.status_view(db, principal.require_tenant())


@router.put(
    "",
    summary="Turn Autopilot on or off",
    dependencies=[
        Depends(require_permission(Permission.SETTINGS_MANAGE)),
        Depends(require_permission(Permission.AUTOMATION_MANAGE)),
    ],
)
async def update_autopilot(
    payload: AutopilotUpdate, db: DbSession, principal: TenantPrincipal
) -> dict[str, Any]:
    await _require_flag(db, principal)
    return await service.update(
        db,
        principal.require_tenant(),
        principal.user_id,
        on=payload.on,
        manual_queue=payload.manual_queue,
        consent=payload.consent,
    )


@router.post(
    "/pause",
    summary="Pause Autopilot for one hour",
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
)
async def pause_autopilot(db: DbSession, principal: TenantPrincipal) -> dict[str, Any]:
    await _require_flag(db, principal)
    return await service.pause(db, principal.require_tenant())


@router.post(
    "/resume",
    summary="End the pause",
    dependencies=[Depends(require_permission(Permission.SETTINGS_MANAGE))],
)
async def resume_autopilot(db: DbSession, principal: TenantPrincipal) -> dict[str, Any]:
    await _require_flag(db, principal)
    return await service.resume(db, principal.require_tenant())


@router.get(
    "/today",
    summary="Today's Autopilot orders and the last decisions",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def autopilot_today(db: DbSession, principal: TenantPrincipal) -> dict[str, Any]:
    await _require_flag(db, principal)
    return await service.today(db, principal.require_tenant())


__all__ = ["router"]
