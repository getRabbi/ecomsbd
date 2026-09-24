"""External risk provider settings and lookups (V3.7).

Provider facts are shown beside, never merged into, the shop's own Risk Check.
Credentials go in and never come back out.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import (
    DbSession,
    Principal,
    SettingsDep,
    get_rate_limiter,
    get_vault,
    require_permission,
)
from app.common.cache import RateLimiter
from app.customers.external_risk import capability
from app.customers.models import Customer
from app.messaging.service import required
from app.risk_providers.service import ExternalRiskService, connection_view
from app.tenants.roles import Permission

router = APIRouter(prefix="/external-risk", tags=["external risk"])
Reader = Annotated[Principal, Depends(require_permission(Permission.CUSTOMER_RISK_VIEW))]
Looker = Annotated[Principal, Depends(require_permission(Permission.EXTERNAL_RISK_LOOKUP))]
Manager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credentials: dict[str, str] | None = None
    config: dict[str, str] | None = None
    cache_ttl_hours: int | None = Field(default=None, ge=1, le=24 * 365)


class StateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class LookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh: bool = False


def _service(
    db: DbSession, principal: Principal, settings: SettingsDep, limiter: RateLimiter
) -> ExternalRiskService:
    return ExternalRiskService(db, principal.require_tenant(), get_vault(settings), limiter)


@router.get("/capability")
async def provider_capability(
    db: DbSession, principal: Reader, settings: SettingsDep, limiter: Limiter
) -> dict[str, Any]:
    catalog = await _service(db, principal, settings, limiter).catalog()
    enabled = [
        p["provider_id"] for p in catalog["providers"] if (p["connection"] or {}).get("enabled")
    ]
    return {**capability(), "status": catalog["status"], "enabled_providers": enabled}


@router.get("/providers")
async def providers(
    db: DbSession, principal: Manager, settings: SettingsDep, limiter: Limiter
) -> dict[str, Any]:
    return await _service(db, principal, settings, limiter).catalog()


@router.put("/providers/{provider_id}")
async def configure_provider(
    provider_id: str,
    body: ProviderInput,
    db: DbSession,
    principal: Manager,
    settings: SettingsDep,
    limiter: Limiter,
) -> dict[str, Any]:
    row = await _service(db, principal, settings, limiter).configure(
        provider_id,
        credentials=body.credentials,
        config=body.config,
        cache_ttl_hours=body.cache_ttl_hours,
    )
    return connection_view(row)


@router.post("/providers/{provider_id}/test")
async def test_provider(
    provider_id: str, db: DbSession, principal: Manager, settings: SettingsDep, limiter: Limiter
) -> dict[str, Any]:
    return await _service(db, principal, settings, limiter).test(provider_id)


@router.patch("/providers/{provider_id}/state")
async def provider_state(
    provider_id: str,
    body: StateInput,
    db: DbSession,
    principal: Manager,
    settings: SettingsDep,
    limiter: Limiter,
) -> dict[str, Any]:
    row = await _service(db, principal, settings, limiter).set_enabled(provider_id, body.enabled)
    return connection_view(row)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_provider(
    provider_id: str, db: DbSession, principal: Manager, settings: SettingsDep, limiter: Limiter
) -> None:
    await _service(db, principal, settings, limiter).remove(provider_id)


@router.get("/customers/{customer_id}")
async def customer_facts(
    customer_id: uuid.UUID,
    db: DbSession,
    principal: Reader,
    settings: SettingsDep,
    limiter: Limiter,
) -> dict[str, Any]:
    """Stored provider facts for a customer. Never calls a provider."""
    await required(db, Customer, customer_id)
    view = await _service(db, principal, settings, limiter).customer_view(customer_id)
    return {**capability(), **view}


@router.post("/customers/{customer_id}/lookup")
async def lookup(
    customer_id: uuid.UUID,
    body: LookupInput,
    db: DbSession,
    principal: Looker,
    settings: SettingsDep,
    limiter: Limiter,
) -> dict[str, Any]:
    """Ask the enabled providers, unless a fresh answer is stored."""
    customer = await required(db, Customer, customer_id)
    view = await _service(db, principal, settings, limiter).lookup(
        customer,
        force=body.refresh,
        trigger="REFRESH" if body.refresh else "MANUAL",
        actor_id=principal.user_id,
    )
    return {**capability(), **view}
