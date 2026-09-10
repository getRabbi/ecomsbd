"""Account, privacy and device endpoints.

Master spec sections 89, 99, 100 and 101::

    GET    /v1/account/devices
    POST   /v1/account/devices/{id}/revoke
    POST   /v1/account/logout-others
    GET    /v1/account/privacy
    POST   /v1/account/delete
    POST   /v1/account/delete/cancel
    GET    /v1/exports
    POST   /v1/exports
    GET    /v1/exports/{id}/download

Two properties worth stating:

*   an export link is a bearer credential, so it is issued once, stored hashed
    and expires — and the download still requires the seller's own session, so
    a forwarded link alone opens nothing;
*   deletion is scheduled, not immediate, and the response says exactly what
    will be anonymised and what will be kept.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    EntitlementsDep,
    SettingsDep,
    TenantPrincipal,
    get_hasher,
    require_permission,
)
from app.auth.models import AuthSession, Device, RevocationReason
from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.errors import ForbiddenError, NotFoundError
from app.exports.models import ExportJob, ExportKind
from app.exports.service import ExportService, permission_for
from app.notifications.delivery import NotificationDispatcher, NotificationPreference
from app.notifications.models import NotificationKind
from app.notifications.transport import build_push_transport, build_sms_transport
from app.privacy.models import DELETION_GRACE_DAYS
from app.privacy.service import RETENTION_NOTE, PrivacyService
from app.tenants.roles import Permission

account_router = APIRouter(prefix="/account", tags=["account"])
export_router = APIRouter(prefix="/exports", tags=["exports"])


# --------------------------------------------------------------------------- #
# Devices and sessions (master spec section 89)
# --------------------------------------------------------------------------- #


class DeviceResponse(BaseModel):
    id: uuid.UUID
    platform: str
    app_version: str | None
    os_version: str | None
    model: str | None
    last_seen_at: datetime
    created_at: datetime
    revoked: bool
    #: True for the device making this request, so the UI can say "this device"
    #: instead of inviting the seller to lock themselves out by accident.
    is_current: bool
    #: Whether a push token is registered. Never the token itself.
    push_enabled: bool


class SessionSummaryResponse(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID | None
    created_at: datetime
    last_seen_at: datetime
    is_current: bool


@account_router.get("/devices", response_model=list[DeviceResponse], summary="Signed-in devices")
async def list_devices(principal: CurrentPrincipal, db: DbSession) -> list[DeviceResponse]:
    devices = (
        (
            await db.execute(
                sa.select(Device)
                .where(Device.user_id == principal.user_id)
                .order_by(Device.last_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )
    current_device_id = principal.session.device_id
    return [
        DeviceResponse(
            id=device.id,
            platform=device.platform,
            app_version=device.app_version,
            os_version=device.os_version,
            model=device.model,
            last_seen_at=device.last_seen_at,
            created_at=device.created_at,
            revoked=device.revoked_at is not None,
            is_current=device.id == current_device_id,
            push_enabled=bool(device.push_token),
        )
        for device in devices
    ]


@account_router.get(
    "/sessions", response_model=list[SessionSummaryResponse], summary="Active sessions"
)
async def list_sessions(principal: CurrentPrincipal, db: DbSession) -> list[SessionSummaryResponse]:
    sessions = (
        (
            await db.execute(
                sa.select(AuthSession)
                .where(
                    AuthSession.user_id == principal.user_id,
                    AuthSession.revoked_at.is_(None),
                )
                .order_by(AuthSession.last_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        SessionSummaryResponse(
            id=row.id,
            device_id=row.device_id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            is_current=row.id == principal.session_id,
        )
        for row in sessions
    ]


@account_router.post(
    "/devices/{device_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign a device out",
)
async def revoke_device(
    device_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
) -> None:
    """Revoke one device and every session on it.

    A device the caller does not own is a 404, not a 403: confirming that a
    device id exists is itself information.
    """
    device = (
        await db.execute(
            sa.select(Device).where(Device.id == device_id, Device.user_id == principal.user_id)
        )
    ).scalar_one_or_none()
    if device is None:
        raise NotFoundError("No such device")

    now = utc_now()
    device.revoked_at = now
    # The push token goes with the device. A signed-out phone must stop
    # receiving a shop's money alerts.
    device.push_token = None

    sessions = (
        (
            await db.execute(
                sa.select(AuthSession).where(
                    AuthSession.device_id == device_id,
                    AuthSession.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for session_row in sessions:
        session_row.revoked_at = now
        session_row.revoked_reason = str(RevocationReason.USER_LOGOUT)

    await db.flush()
    await record_audit(
        db,
        AuditAction.DEVICE_REVOKED,
        entity_type="device",
        entity_id=device_id,
        context={"sessions_revoked": len(sessions), "platform": device.platform},
        tenant_id=principal.tenant_id,
    )


@account_router.post(
    "/logout-others",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out every other device",
)
async def logout_others(principal: CurrentPrincipal, db: DbSession) -> None:
    """Revoke every session except this one.

    The obvious control for "I lost my phone", and the reason it excludes the
    current session: a seller doing this from their working device should not
    have to sign back in.
    """
    now = utc_now()
    sessions = (
        (
            await db.execute(
                sa.select(AuthSession).where(
                    AuthSession.user_id == principal.user_id,
                    AuthSession.id != principal.session_id,
                    AuthSession.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for session_row in sessions:
        session_row.revoked_at = now
        session_row.revoked_reason = str(RevocationReason.LOGOUT_ALL)

    await db.flush()
    await record_audit(
        db,
        AuditAction.SESSION_REVOKED,
        entity_type="user",
        entity_id=principal.user_id,
        context={"revoked": len(sessions), "scope": "others"},
        tenant_id=principal.tenant_id,
    )


# --------------------------------------------------------------------------- #
# Notification preferences (master spec section 95)
# --------------------------------------------------------------------------- #


class NotificationPreferenceResponse(BaseModel):
    push_enabled: bool
    sms_enabled: bool
    muted_kinds: list[str]
    routine_tracking_push: bool
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    #: Whether a transport exists at all in this deployment. The client shows
    #: "not available yet" rather than a switch that changes nothing.
    push_transport_available: bool
    sms_transport_available: bool


class NotificationPreferenceUpdate(BaseModel):
    push_enabled: bool | None = None
    sms_enabled: bool | None = None
    muted_kinds: list[str] | None = None
    routine_tracking_push: bool | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)


def _preference_response(
    preference: NotificationPreference, settings: Any
) -> NotificationPreferenceResponse:
    return NotificationPreferenceResponse(
        push_enabled=preference.push_enabled,
        sms_enabled=preference.sms_enabled,
        muted_kinds=list(preference.muted_kinds or []),
        routine_tracking_push=preference.routine_tracking_push,
        quiet_hours_start=preference.quiet_hours_start,
        quiet_hours_end=preference.quiet_hours_end,
        push_transport_available=build_push_transport(settings).is_configured,
        sms_transport_available=build_sms_transport(settings).is_configured,
    )


async def _dispatcher(db: DbSession, settings: Any) -> NotificationDispatcher:
    return NotificationDispatcher(
        db,
        settings=settings,
        push=build_push_transport(settings),
        sms=build_sms_transport(settings),
    )


@account_router.get(
    "/notification-preferences",
    response_model=NotificationPreferenceResponse,
    summary="Notification preferences",
)
async def get_notification_preferences(
    principal: TenantPrincipal, db: DbSession, settings: SettingsDep
) -> NotificationPreferenceResponse:
    dispatcher = await _dispatcher(db, settings)
    preference = await dispatcher.preferences(principal.require_tenant())
    return _preference_response(preference, settings)


@account_router.patch(
    "/notification-preferences",
    response_model=NotificationPreferenceResponse,
    summary="Change notification preferences",
)
async def update_notification_preferences(
    payload: NotificationPreferenceUpdate,
    principal: TenantPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> NotificationPreferenceResponse:
    """Section 95's opt-out, server-side.

    A muted kind is refused delivery in the dispatcher, not hidden in the app —
    a client that ignored the setting would otherwise still be pushed to.
    """
    dispatcher = await _dispatcher(db, settings)
    preference = await dispatcher.preferences(principal.require_tenant())

    if payload.push_enabled is not None:
        preference.push_enabled = payload.push_enabled
    if payload.sms_enabled is not None:
        preference.sms_enabled = payload.sms_enabled
    if payload.routine_tracking_push is not None:
        preference.routine_tracking_push = payload.routine_tracking_push
    if payload.muted_kinds is not None:
        # Unknown kinds are dropped rather than stored: a typo would otherwise
        # sit in the row forever muting nothing.
        known = {str(kind) for kind in NotificationKind}
        preference.muted_kinds = [k for k in payload.muted_kinds if k in known]
    if payload.quiet_hours_start is not None:
        preference.quiet_hours_start = payload.quiet_hours_start
    if payload.quiet_hours_end is not None:
        preference.quiet_hours_end = payload.quiet_hours_end

    await db.flush()
    return _preference_response(preference, settings)


# --------------------------------------------------------------------------- #
# Privacy and deletion (master spec section 100)
# --------------------------------------------------------------------------- #


class PrivacyStatusResponse(BaseModel):
    """What the seller is told before they decide."""

    retention_note: str
    grace_days: int
    deletion_requested: bool
    scheduled_for: datetime | None
    anonymised_on_deletion: list[str]
    retained_after_deletion: list[str]


class DeleteAccountPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=400)
    #: Typed by the seller. A destructive, irreversible action gets a
    #: deliberate confirmation, not a checkbox.
    confirm: str = Field(min_length=1, max_length=200)


class DeletionRequestResponse(BaseModel):
    status: str
    scheduled_for: datetime
    grace_days: int
    retention_note: str


@account_router.get(
    "/privacy", response_model=PrivacyStatusResponse, summary="What deletion would do"
)
async def privacy_status(principal: TenantPrincipal, db: DbSession) -> PrivacyStatusResponse:
    pending = await PrivacyService(db).pending_request(principal.require_tenant())
    return PrivacyStatusResponse(
        retention_note=RETENTION_NOTE,
        grace_days=DELETION_GRACE_DAYS,
        deletion_requested=pending is not None,
        scheduled_for=pending.scheduled_for if pending else None,
        anonymised_on_deletion=[
            "Customer names, phone numbers and addresses",
            "Your own name and contact number",
            "Device push tokens",
            "Shop name",
        ],
        retained_after_deletion=[
            "The financial ledger, in anonymised form",
            "COD receivables and payouts, in anonymised form",
            "Profit snapshots, in anonymised form",
            "The audit record of the deletion itself",
        ],
    )


@account_router.post(
    "/delete",
    response_model=DeletionRequestResponse,
    summary="Request account deletion",
    dependencies=[Depends(require_permission(Permission.SETTINGS_MANAGE))],
)
async def request_deletion(
    payload: DeleteAccountPayload,
    principal: TenantPrincipal,
    db: DbSession,
) -> DeletionRequestResponse:
    """Schedule deletion after a cooling-off period.

    Ownership is re-verified against the membership table inside the service —
    the token's role claim is not enough for the one irreversible action in the
    product.
    """
    request = await PrivacyService(db).request_deletion(
        tenant_id=principal.require_tenant(),
        user_id=principal.user_id,
        reason=payload.reason,
    )
    return DeletionRequestResponse(
        status=request.status,
        scheduled_for=request.scheduled_for,
        grace_days=DELETION_GRACE_DAYS,
        retention_note=RETENTION_NOTE,
    )


@account_router.post(
    "/delete/cancel",
    response_model=DeletionRequestResponse,
    summary="Cancel a scheduled deletion",
    dependencies=[Depends(require_permission(Permission.SETTINGS_MANAGE))],
)
async def cancel_deletion(principal: TenantPrincipal, db: DbSession) -> DeletionRequestResponse:
    request = await PrivacyService(db).cancel_deletion(
        tenant_id=principal.require_tenant(), user_id=principal.user_id
    )
    return DeletionRequestResponse(
        status=request.status,
        scheduled_for=request.scheduled_for,
        grace_days=DELETION_GRACE_DAYS,
        retention_note=RETENTION_NOTE,
    )


# --------------------------------------------------------------------------- #
# Exports (master spec section 99)
# --------------------------------------------------------------------------- #


class ExportRequestPayload(BaseModel):
    kind: ExportKind
    since: date | None = None
    until: date | None = None


class ExportJobResponse(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    row_count: int
    byte_size: int
    filename: str | None
    created_at: datetime
    expires_at: datetime | None
    downloaded_at: datetime | None
    #: Present only in the creation response. Stored hashed thereafter.
    download_token: str | None = None


@export_router.get("", response_model=list[ExportJobResponse], summary="Recent exports")
async def list_exports(
    principal: TenantPrincipal,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ExportJobResponse]:
    rows = (
        (
            await db.execute(
                sa.select(ExportJob)
                .where(ExportJob.tenant_id == principal.require_tenant())
                .order_by(ExportJob.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [_to_export_response(row) for row in rows]


@export_router.post(
    "",
    response_model=ExportJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Export a dataset",
)
async def create_export(
    payload: ExportRequestPayload,
    principal: TenantPrincipal,
    db: DbSession,
    settings: SettingsDep,
    entitlements: EntitlementsDep,
) -> ExportJobResponse:
    """Build an export and return its one-time download token.

    The permission depends on *what* is being exported: section 88 makes
    exporting customer data Owner-only, while an orders or products export is
    available to anyone with the general export permission. Checking it here
    rather than on the route is what lets one endpoint serve both.
    """
    required = permission_for(payload.kind)
    if not principal.can(required):
        raise ForbiddenError(
            f"Your role does not allow {required}",
            details={"required": str(required), "kind": str(payload.kind)},
        )

    service = ExportService(
        db, settings=settings, hasher=get_hasher(settings), entitlements=entitlements
    )
    result = await service.request(
        tenant_id=principal.require_tenant(),
        kind=payload.kind,
        user_id=principal.user_id,
        since=payload.since,
        until=payload.until,
    )
    response = _to_export_response(result.job)
    response.download_token = result.download_token
    return response


@export_router.get(
    "/{export_id}/download", summary="Download a ready export", response_class=Response
)
async def download_export(
    export_id: uuid.UUID,
    token: Annotated[str, Query(min_length=8, max_length=200)],
    principal: TenantPrincipal,
    db: DbSession,
    settings: SettingsDep,
) -> Response:
    """Serve the file.

    Both the session and the token are required. A forwarded link is useless
    without the seller's own sign-in, and a stolen session cannot enumerate
    exports it was never given a token for.
    """
    service = ExportService(db, settings=settings, hasher=get_hasher(settings))
    job, content = await service.download(
        tenant_id=principal.require_tenant(), job_id=export_id, token=token
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{job.filename or "export.csv"}"',
            # Never cached: it is one seller's business data behind a token.
            "Cache-Control": "no-store, private",
        },
    )


def _to_export_response(job: ExportJob) -> ExportJobResponse:
    return ExportJobResponse(
        id=job.id,
        kind=job.kind,
        status=job.status,
        row_count=job.row_count,
        byte_size=job.byte_size,
        filename=job.filename,
        created_at=job.created_at,
        expires_at=job.expires_at,
        downloaded_at=job.downloaded_at,
    )


__all__ = ["account_router", "export_router"]
