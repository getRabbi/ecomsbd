"""The shop's confirmation-call intro, for the seller (V4 WP3.3).

``GET /v1/voice/intro`` says what the shop has: the Bangla script for its
current name, the newest recording to listen to and the one calls play.
Playing a recording (``GET …/{version}/audio``) is the preview that allows
turning it on; ``turn-on`` refuses a recording nobody played. ``request`` asks
the TTS port for one (``TTS_PROVIDER_REQUIRED`` until a provider exists: the
ecomsbd team records it and uploads it in the admin console).

All routes need ``settings.manage`` and the ``voice_confirmation_enabled``
flag; with the flag off they answer ``FEATURE_DISABLED`` and clients hide the
card.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.core.errors import AppError, ErrorCode
from app.tenants.models import Tenant
from app.tenants.roles import Permission
from app.voice import intro
from app.voice.intro import IntroAudioService, intro_script
from app.voice.models import IntroState, VoiceIntroAudio
from app.voice.tts import TtsAvailability

router = APIRouter(
    prefix="/voice",
    tags=["voice"],
    dependencies=[Depends(require_permission(Permission.SETTINGS_MANAGE))],
)


class IntroVersionResponse(BaseModel):
    version: int
    state: str
    source: str
    duration_ms: int | None
    previewed: bool
    created_at: datetime
    approved_at: datetime | None


class IntroStatusResponse(BaseModel):
    shop_name: str
    #: What the recording says (and what the operator reads out).
    script: str
    #: NONE (nothing for the current name), READY (one to listen to and turn
    #: on) or ON (calls play it).
    status: str
    latest: IntroVersionResponse | None
    live: IntroVersionResponse | None
    #: Whether a recording can be made automatically; false → the team records it.
    tts_available: bool
    #: Whether calls run for the shop right now, and why not.
    calls_on: bool
    calls_off_reason: str | None


async def _require_flag(db: DbSession, principal: TenantPrincipal) -> None:
    if not await FeatureFlagService(db).is_enabled(
        FlagKey.VOICE_CONFIRMATION_ENABLED, tenant_id=principal.require_tenant()
    ):
        raise AppError("Confirmation calls are not enabled", code=ErrorCode.FEATURE_DISABLED)


def _version(row: VoiceIntroAudio | None) -> IntroVersionResponse | None:
    if row is None:
        return None
    return IntroVersionResponse(
        version=row.version,
        state=row.state,
        source=row.source,
        duration_ms=row.duration_ms,
        previewed=row.previewed_at is not None,
        created_at=row.created_at,
        approved_at=row.approved_at,
    )


async def _status(db: DbSession, principal: TenantPrincipal) -> IntroStatusResponse:
    from app.confirmations.calls import voice_gate

    tenant_id = principal.require_tenant()
    tenant = await db.get(Tenant, tenant_id)
    name = tenant.name if tenant is not None else ""
    rows = [
        row
        for row in await IntroAudioService(db).versions(tenant_id)
        if row.state != IntroState.RETIRED and row.shop_name == name
    ]
    live = next((row for row in rows if row.state == IntroState.ON), None)
    latest = rows[0] if rows else None
    gate = await voice_gate(db, tenant_id)
    return IntroStatusResponse(
        shop_name=name,
        script=intro_script(name),
        status="ON" if live is not None else ("READY" if latest is not None else "NONE"),
        latest=_version(latest),
        live=_version(live),
        tts_available=intro.tts_client().availability() is TtsAvailability.AVAILABLE,
        calls_on=gate.on,
        calls_off_reason=gate.reason,
    )


@router.get("/intro", response_model=IntroStatusResponse, summary="The shop's call intro")
async def get_intro(db: DbSession, principal: TenantPrincipal) -> IntroStatusResponse:
    await _require_flag(db, principal)
    return await _status(db, principal)


@router.get(
    "/intro/{version}/audio",
    summary="Play a recording (the preview that allows turning it on)",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}, "audio/mpeg": {}}}},
)
async def play_intro(version: int, db: DbSession, principal: TenantPrincipal) -> Response:
    await _require_flag(db, principal)
    row, content = await IntroAudioService(db).play(
        principal.require_tenant(), version, user_id=principal.user_id
    )
    return Response(
        content=content,
        media_type=row.content_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/intro/{version}/turn-on",
    response_model=IntroStatusResponse,
    summary="Calls play this recording from now on",
)
async def turn_on_intro(
    version: int, db: DbSession, principal: TenantPrincipal
) -> IntroStatusResponse:
    await _require_flag(db, principal)
    await IntroAudioService(db).turn_on(
        principal.require_tenant(), version, user_id=principal.user_id
    )
    return await _status(db, principal)


@router.post("/intro/turn-off", response_model=IntroStatusResponse, summary="Stop calls")
async def turn_off_intro(db: DbSession, principal: TenantPrincipal) -> IntroStatusResponse:
    await _require_flag(db, principal)
    await IntroAudioService(db).turn_off(principal.require_tenant())
    return await _status(db, principal)


@router.post(
    "/intro/request",
    response_model=IntroStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Make the recording automatically (needs a TTS provider)",
)
async def request_intro(db: DbSession, principal: TenantPrincipal) -> IntroStatusResponse:
    await _require_flag(db, principal)
    await IntroAudioService(db).request(principal.require_tenant())
    return await _status(db, principal)
