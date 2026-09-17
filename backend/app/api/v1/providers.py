"""Courier provider discovery.

``GET /v1/couriers/providers`` (master spec section 39) returns each provider's
**verified** capability set plus its feature-flag state.

This endpoint is what lets the client obey master spec section 75: the UI reacts
to capability, not provider name. A provider whose ``payouts`` capability is
``unknown`` renders "upload a statement" rather than a button that cannot work,
and a provider with nothing verified is presented as manual-mode only.

The remaining courier endpoints from section 39 (account create, validate,
delete, quote) belong to Phase C, when a real adapter and verified
documentation exist.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, FeatureFlagsDep
from app.api.v1.schemas import ProviderResponse
from app.common.feature_flags import COURIER_PROVIDER_FLAGS
from app.couriers.capabilities import load_all_manifests
from app.couriers.credentials import spec_for

router = APIRouter(prefix="/couriers", tags=["couriers"])


@router.get(
    "/providers",
    response_model=list[ProviderResponse],
    summary="Courier providers and their verified capabilities",
)
async def list_providers(
    principal: CurrentPrincipal,
    flags: FeatureFlagsDep,
) -> list[ProviderResponse]:
    manifests = load_all_manifests()
    responses: list[ProviderResponse] = []

    for provider, manifest in sorted(manifests.items()):
        flag = COURIER_PROVIDER_FLAGS.get(provider)
        enabled = (
            True if flag is None else await flags.is_enabled(flag, tenant_id=principal.tenant_id)
        )
        data = manifest.as_dict()
        spec = spec_for(provider)
        responses.append(
            ProviderResponse(
                provider=data["provider"],
                display_name=data["display_name"],
                verified_at=data["verified_at"],
                capabilities=data["capabilities"],
                manual_fallback=data["manual_fallback"],
                fully_unverified=data["fully_unverified"],
                enabled=enabled,
                documentation_version=data["documentation_version"],
                unknowns=data["unknowns"],
                # ``None`` for a provider with no integration — manual mode has
                # nothing to connect, and the client renders it as such.
                connect_form=spec.as_dict() if spec is not None else None,
            )
        )
    return responses


__all__ = ["router"]
