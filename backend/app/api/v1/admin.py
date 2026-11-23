"""The internal admin/ops console API (master spec section 44).

Mounted under ``/v1/admin`` and **not** part of the seller API. Three
properties hold for every route here:

*   authentication is an ``X-Admin-Token``, resolved against ``platform_admins``
    or a bootstrap token. A seller access token is worth nothing on these
    routes, and an admin token is worth nothing on the seller ones;
*   the session is deliberately unscoped, so every query names its tenant;
*   PII is masked. The single reveal route takes a reason and audits before it
    answers.

These routes are excluded from the public OpenAPI schema. That is not the
security control — authentication is — but publishing an ops surface in the
client-facing schema invites people to try it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import AdminPrincipal, authenticate_admin
from app.admin.models import (
    AdminPermission,
    PlatformAdminRole,
    SupportCaseSeverity,
    SupportCaseStatus,
    SupportCaseType,
)
from app.admin.repair import REPAIR_ACTIONS, RepairRequest, RepairRunner
from app.admin.service import AdminService
from app.api.deps import ProviderRegistryDep, SettingsDep, get_hasher, get_vault
from app.api.v1.admin_schemas import (
    AdminIdentityResponse,
    FunnelResponse,
    FunnelStepResponse,
    IntakeRulesActivatePayload,
    IntakeRulesCreatePayload,
    IntakeRuleSetResponse,
    ManualPaymentApprovePayload,
    ManualPaymentRejectPayload,
    ManualPaymentReviewResponse,
    OpsCountsResponse,
    ProviderHealthResponse,
    ReferralAdminResponse,
    RepairActionResponse,
    RepairRequestPayload,
    RepairResultResponse,
    RevealPhonePayload,
    RevealPhoneResponse,
    SmsProviderConnectionView,
    SmsProviderResponse,
    SmsProviderUpdatePayload,
    SupportCaseCreatePayload,
    SupportCaseResponse,
    SupportCaseUpdatePayload,
    TenantSummaryResponse,
    VoiceCreditsResponse,
    VoiceIntroAdminView,
    VoiceIntrosAdminResponse,
    VoicePackGrantPayload,
    VoicePackGrantResponse,
    VoicePackView,
    VoiceProviderConnectionView,
    VoiceProviderResponse,
    VoiceProviderUpdatePayload,
    VoiceReportResponse,
    VoiceReportRow,
)
from app.billing.manual_payments import ManualPaymentReview
from app.billing.models import ManualPaymentState
from app.billing.providers.registry import PROVIDER_FLAGS
from app.common.audit import AuditAction, record_audit
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.common.provider_health import ProviderHealthService, ProviderKind
from app.core.clock import utc_now
from app.core.errors import ValidationError
from app.db.session import system_session
from app.intake import rules as intake_rules
from app.product_events.service import ProductEventService
from app.referrals.models import ReferralCreditState, ReferralState
from app.referrals.service import list_referrals
from app.voice.manifest import available_voice_manifests
from app.voice.service import VoiceConnectionService, VoiceConnectionUpdate, connection_view

router = APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False)


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #


async def get_admin_db() -> AsyncIterator[AsyncSession]:
    """An unscoped session for the console.

    The reason string reaches the logs, so every admin request is visible as a
    deliberate cross-tenant read rather than an accident.
    """
    async with system_session("admin console") as session:
        yield session


AdminDb = Annotated[AsyncSession, Depends(get_admin_db)]


async def get_admin(
    db: AdminDb,
    settings: SettingsDep,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> AdminPrincipal:
    return await authenticate_admin(
        x_admin_token, session=db, settings=settings, hasher=get_hasher(settings)
    )


CurrentAdmin = Annotated[AdminPrincipal, Depends(get_admin)]


async def get_admin_service(db: AdminDb, admin: CurrentAdmin) -> AdminService:
    return AdminService(db, admin=admin)


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


@router.get("/me", response_model=AdminIdentityResponse, summary="Who this token is")
async def admin_identity(admin: CurrentAdmin) -> AdminIdentityResponse:
    return AdminIdentityResponse(
        label=admin.label,
        role=str(admin.role),
        is_bootstrap=admin.is_bootstrap,
        permissions=sorted(str(p) for p in admin.permissions),
    )


# --------------------------------------------------------------------------- #
# Tenants
# --------------------------------------------------------------------------- #


@router.get("/tenants", response_model=list[TenantSummaryResponse], summary="Search shops")
async def search_tenants(
    admin: CurrentAdmin,
    service: AdminServiceDep,
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[TenantSummaryResponse]:
    admin.require(AdminPermission.TENANT_READ)
    summaries = await service.search_tenants(q, limit=limit)
    return [TenantSummaryResponse.model_validate(s.as_dict()) for s in summaries]


@router.get("/tenants/{tenant_id}", response_model=dict, summary="One shop, masked")
async def tenant_detail(tenant_id: uuid.UUID, service: AdminServiceDep) -> dict[str, Any]:
    return await service.tenant_detail(tenant_id)


@router.get("/tenants/{tenant_id}/billing", response_model=list[dict], summary="Billing history")
async def tenant_billing(
    tenant_id: uuid.UUID,
    service: AdminServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    return await service.billing_history(tenant_id, limit=limit)


@router.get(
    "/tenants/{tenant_id}/support-bundle",
    response_model=dict,
    summary="Redacted diagnostic bundle",
)
async def support_bundle(tenant_id: uuid.UUID, service: AdminServiceDep) -> dict[str, Any]:
    """Master spec section 102.

    Contains ids, a status timeline and provider/billing state. Contains no
    plaintext phone number, no courier credential and no provider response body.
    """
    return await service.support_bundle(tenant_id)


@router.post(
    "/tenants/{tenant_id}/customers/{customer_id}/reveal-phone",
    response_model=RevealPhoneResponse,
    summary="Reveal one customer's number, with a reason",
)
async def reveal_phone(
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    payload: RevealPhonePayload,
    service: AdminServiceDep,
    settings: SettingsDep,
) -> RevealPhoneResponse:
    """Master spec section 101's audited reveal.

    ``SUPERADMIN`` only, one customer at a time, reason required, audit written
    and committed before the number is returned. There is no bulk variant.
    """
    phone = await service.reveal_customer_phone(
        tenant_id=tenant_id,
        customer_id=customer_id,
        reason=payload.reason,
        vault=get_vault(settings),
    )
    return RevealPhoneResponse(
        phone=phone,
        reason=payload.reason,
        expires_in_seconds=settings.admin_reveal_ttl_seconds,
    )


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


@router.get("/ops/counts", response_model=OpsCountsResponse, summary="Failure queues")
async def ops_counts(service: AdminServiceDep) -> OpsCountsResponse:
    counts = await service.ops_counts()
    return OpsCountsResponse(**counts.as_dict())


_FUNNEL_MAX_DAYS = 366


@router.get(
    "/ops/funnel",
    response_model=FunnelResponse,
    response_model_by_alias=True,
    summary="Product funnel",
)
async def product_funnel(
    admin: CurrentAdmin,
    db: AdminDb,
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
) -> FunnelResponse:
    """Distinct shops that reached each funnel step, Asia/Dhaka days ``[from, to]``."""
    admin.require(AdminPermission.OPS_READ)
    if to_date < from_date or (to_date - from_date).days >= _FUNNEL_MAX_DAYS:
        raise ValidationError("from must be on or before to, at most 366 days apart")
    steps = await ProductEventService(db).funnel(start=from_date, end=to_date)
    return FunnelResponse(
        from_date=from_date,
        to_date=to_date,
        steps=[FunnelStepResponse(name=step.name, shops=step.shops) for step in steps],
    )


@router.get("/ops/webhooks", response_model=list[dict], summary="Failed billing webhooks")
async def failed_webhooks(
    service: AdminServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    return await service.failed_webhooks(limit=limit)


@router.get(
    "/ops/provider-health",
    response_model=list[ProviderHealthResponse],
    summary="Provider health",
)
async def provider_health(
    admin: CurrentAdmin,
    db: AdminDb,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[ProviderHealthResponse]:
    """Section 49's provider dashboard.

    Reports state, counters and latency. It never reports a credential, because
    it never loads one.
    """
    admin.require(AdminPermission.OPS_READ)
    views = await ProviderHealthService(db).snapshot(tenant_id=tenant_id)
    return [ProviderHealthResponse.model_validate(view.as_dict()) for view in views]


@router.get("/ops/audit", response_model=list[dict], summary="Audit trail")
async def audit_trail(
    service: AdminServiceDep,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return await service.audit_trail(tenant_id=tenant_id, action=action, limit=limit)


# --------------------------------------------------------------------------- #
# Feature flags
# --------------------------------------------------------------------------- #


@router.get("/flags", response_model=list[dict], summary="Feature flags and their state")
async def list_flags(
    admin: CurrentAdmin,
    db: AdminDb,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[dict[str, Any]]:
    """Every known flag with its resolved value.

    Resolved rather than raw: what an operator needs during an incident is
    "is this on for this shop right now?", which a percentage rollout makes
    different from what the global row says.
    """
    admin.require(AdminPermission.OPS_READ)
    flags = FeatureFlagService(db)
    return [
        {
            "key": str(key),
            "enabled": await flags.is_enabled(key, tenant_id=tenant_id),
            "payload": await flags.payload(key, tenant_id=tenant_id),
            "gates_billing_provider": key in set(PROVIDER_FLAGS.values()),
        }
        for key in FlagKey
    ]


# --------------------------------------------------------------------------- #
# Intake rules (V4 WP1.2)
# --------------------------------------------------------------------------- #


@router.get(
    "/intake-rules", response_model=list[IntakeRuleSetResponse], summary="Intake rule versions"
)
async def list_intake_rules(
    admin: CurrentAdmin,
    db: AdminDb,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[IntakeRuleSetResponse]:
    """Newest first, with payloads: devices of every shop follow the active one."""
    admin.require(AdminPermission.OPS_READ)
    rows = await intake_rules.list_versions(db, limit=limit)
    return [IntakeRuleSetResponse.model_validate(row) for row in rows]


@router.post(
    "/intake-rules",
    response_model=IntakeRuleSetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Publish an intake rules version (inactive)",
)
async def create_intake_rules(
    payload: IntakeRulesCreatePayload, admin: CurrentAdmin, db: AdminDb
) -> IntakeRuleSetResponse:
    """Rule L5: a Meta/WhatsApp UI change is fixed by a new version, not a release.

    Nothing changes on any device until the version is activated.
    """
    admin.require(AdminPermission.FEATURE_FLAG_WRITE)
    row = await intake_rules.create_version(
        db, payload.payload, created_by=admin.label, note=payload.note
    )
    await record_audit(
        db,
        AuditAction.ADMIN_INTAKE_RULES_CREATED,
        entity_type="intake_rule_sets",
        entity_id=row.id,
        context={"version": row.version, "admin": admin.label},
    )
    return IntakeRuleSetResponse.model_validate(row)


@router.post(
    "/intake-rules/{version}/activate",
    response_model=IntakeRuleSetResponse,
    summary="Make one intake rules version the active one",
)
async def activate_intake_rules(
    version: int, payload: IntakeRulesActivatePayload, admin: CurrentAdmin, db: AdminDb
) -> IntakeRuleSetResponse:
    """Exactly one version stays active; activating an older one is the rollback."""
    admin.require(AdminPermission.FEATURE_FLAG_WRITE)
    result = await intake_rules.activate_version(db, version, activated_by=admin.label)
    if result.changed:
        await record_audit(
            db,
            AuditAction.ADMIN_INTAKE_RULES_ACTIVATED,
            entity_type="intake_rule_sets",
            entity_id=result.rule_set.id,
            reason=payload.reason,
            context={
                "version": version,
                "previous_version": result.previous_version,
                "admin": admin.label,
            },
        )
    return IntakeRuleSetResponse.model_validate(result.rule_set)


# --------------------------------------------------------------------------- #
# Voice provider (V4 WP3.1)
# --------------------------------------------------------------------------- #


def _voice_service(db: AsyncSession, settings: Any) -> VoiceConnectionService:
    return VoiceConnectionService(db, get_vault(settings), available_voice_manifests(settings))


async def _voice_response(
    db: AsyncSession, provider: str, manifest: Any, connection: Any
) -> VoiceProviderResponse:
    views = [
        view
        for view in await ProviderHealthService(db).snapshot()
        if view.kind == str(ProviderKind.VOICE) and view.provider == provider
    ]
    view = connection_view(connection)
    return VoiceProviderResponse(
        provider=provider,
        manifest=manifest.as_dict() if manifest is not None else None,
        connection=VoiceProviderConnectionView.model_validate(view) if view else None,
        health=[ProviderHealthResponse.model_validate(item.as_dict()) for item in views],
    )


@router.get(
    "/voice/providers", response_model=list[VoiceProviderResponse], summary="Voice providers"
)
async def list_voice_providers(
    admin: CurrentAdmin, db: AdminDb, settings: SettingsDep
) -> list[VoiceProviderResponse]:
    """Every provider with a verified manifest, and any stored account. Never a secret."""
    admin.require(AdminPermission.OPS_READ)
    manifests = available_voice_manifests(settings)
    service = _voice_service(db, settings)
    connections = {row.provider: row for row in await service.list()}
    return [
        await _voice_response(db, provider, manifests.get(provider), connections.get(provider))
        for provider in sorted(set(manifests) | set(connections))
    ]


@router.put(
    "/voice/providers/{provider}",
    response_model=VoiceProviderResponse,
    summary="Connect, rotate or switch the platform voice provider",
)
async def update_voice_provider(
    provider: str,
    payload: VoiceProviderUpdatePayload,
    admin: CurrentAdmin,
    db: AdminDb,
    settings: SettingsDep,
) -> VoiceProviderResponse:
    """Only a provider whose manifest exists (a verified contract) can be connected."""
    admin.require(AdminPermission.PROVIDER_CREDENTIALS_WRITE)
    service = _voice_service(db, settings)
    row = await service.update(
        provider,
        VoiceConnectionUpdate(
            credentials=payload.credentials,
            callback_secret=payload.callback_secret,
            caller_id=payload.caller_id,
            enabled=payload.enabled,
            reason=payload.reason,
        ),
        actor=admin.label,
    )
    return await _voice_response(db, provider, service.manifest(provider), row)


# --------------------------------------------------------------------------- #
# SMS gateway (V4 WP7.1)
# --------------------------------------------------------------------------- #


async def _sms_response(db: AsyncSession, provider: str, row: Any) -> SmsProviderResponse:
    from app.common.feature_flags import FeatureFlagService
    from app.sms_providers.service import PROVIDER_FLAGS, connection_view, sms_manifests

    manifest = sms_manifests().get(provider)
    flag = PROVIDER_FLAGS.get(provider)
    view = connection_view(row)
    return SmsProviderResponse(
        provider=provider,
        manifest=manifest.as_dict() if manifest is not None else None,
        connection=SmsProviderConnectionView.model_validate(view) if view else None,
        flag_enabled=bool(flag) and await FeatureFlagService(db).is_enabled(str(flag)),
    )


@router.get("/sms/providers", response_model=list[SmsProviderResponse], summary="SMS gateways")
async def list_sms_providers(
    admin: CurrentAdmin, db: AdminDb, settings: SettingsDep
) -> list[SmsProviderResponse]:
    """Every gateway with a verified manifest, and any stored account. Never a secret."""
    from app.sms_providers.service import SmsProviderService, sms_manifests

    admin.require(AdminPermission.OPS_READ)
    rows = {row.provider: row for row in await SmsProviderService(db, get_vault(settings)).list()}
    return [
        await _sms_response(db, provider, rows.get(provider))
        for provider in sorted(set(sms_manifests()) | set(rows))
    ]


@router.put(
    "/sms/providers/{provider}",
    response_model=SmsProviderResponse,
    summary="Connect, rotate or switch the platform SMS gateway",
)
async def update_sms_provider(
    provider: str,
    payload: SmsProviderUpdatePayload,
    admin: CurrentAdmin,
    db: AdminDb,
    settings: SettingsDep,
) -> SmsProviderResponse:
    """Only a provider whose manifest exists (a verified contract) can be connected."""
    from app.sms_providers.service import SmsAccountUpdate, SmsProviderService

    admin.require(AdminPermission.PROVIDER_CREDENTIALS_WRITE)
    row = await SmsProviderService(db, get_vault(settings)).update(
        provider,
        SmsAccountUpdate(
            credentials=payload.credentials,
            sender_id=payload.sender_id,
            enabled=payload.enabled,
            reason=payload.reason,
        ),
        actor=admin.label,
    )
    return await _sms_response(db, provider, row)


async def _voice_intros(db: AsyncSession, tenant_id: uuid.UUID) -> VoiceIntrosAdminResponse:
    from app.core.errors import NotFoundError
    from app.tenants.models import Tenant
    from app.voice.intro import IntroAudioService, intro_script

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        raise NotFoundError("Shop not found")
    rows = await IntroAudioService(db).versions(tenant_id)
    return VoiceIntrosAdminResponse(
        tenant_id=tenant_id,
        shop_name=tenant.name,
        script=intro_script(tenant.name),
        versions=[VoiceIntroAdminView.model_validate(_intro_view(row)) for row in rows],
    )


def _intro_view(row: Any) -> dict[str, Any]:
    fields = set(VoiceIntroAdminView.model_fields) - {"uploaded_to"}
    return {
        **{name: getattr(row, name) for name in fields},
        "uploaded_to": sorted(row.provider_refs or {}),
    }


@router.get(
    "/voice/intros/{tenant_id}",
    response_model=VoiceIntrosAdminResponse,
    summary="A shop's call intro recordings and the script to record",
)
async def list_voice_intros(
    tenant_id: uuid.UUID, admin: CurrentAdmin, db: AdminDb
) -> VoiceIntrosAdminResponse:
    admin.require(AdminPermission.OPS_READ)
    return await _voice_intros(db, tenant_id)


@router.put(
    "/voice/intros/{tenant_id}",
    response_model=VoiceIntrosAdminResponse,
    summary="Upload a shop's call intro (WAV or MP3, ≤ 2 MB)",
)
async def upload_voice_intro(
    tenant_id: uuid.UUID,
    admin: CurrentAdmin,
    db: AdminDb,
    file: Annotated[UploadFile, File()],
) -> VoiceIntrosAdminResponse:
    """The operator's recording of the script for the shop's current name.

    Stored as a new ``READY`` version; calls play it only after a seller of
    the shop has listened to it and turned it on.
    """
    from app.voice.intro import MAX_AUDIO_BYTES, IntroAudioService
    from app.voice.models import IntroSource

    admin.require(AdminPermission.VOICE_AUDIO_WRITE)
    # One byte over the limit is enough to refuse it (VOICE_AUDIO_INVALID).
    content = await file.read(MAX_AUDIO_BYTES + 1)
    row = await IntroAudioService(db).add(
        tenant_id, content, source=IntroSource.UPLOAD, created_by=admin.label
    )
    await record_audit(
        db,
        AuditAction.ADMIN_VOICE_INTRO_UPLOADED,
        entity_type="voice_intro_audio",
        entity_id=row.id,
        tenant_id=tenant_id,
        context={
            "version": row.version,
            "content_type": row.content_type,
            "byte_size": row.byte_size,
            "sha256": row.sha256,
            "admin": admin.label,
        },
    )
    return await _voice_intros(db, tenant_id)


# --------------------------------------------------------------------------- #
# Voice call credits (V4 WP3.4)
# --------------------------------------------------------------------------- #


async def _shop_or_404(db: AsyncSession, tenant_id: uuid.UUID) -> Any:
    from app.core.errors import NotFoundError
    from app.tenants.models import Tenant

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        raise NotFoundError("Shop not found")
    return tenant


@router.get(
    "/voice/credits/{tenant_id}",
    response_model=VoiceCreditsResponse,
    summary="A shop's voice call allowance and packs",
)
async def get_voice_credits(
    tenant_id: uuid.UUID, admin: CurrentAdmin, db: AdminDb, settings: SettingsDep
) -> VoiceCreditsResponse:
    from app.voice.credits import VoiceCredits

    admin.require(AdminPermission.BILLING_READ)
    await _shop_or_404(db, tenant_id)
    balance = await VoiceCredits(db, settings=settings).balance(tenant_id, now=utc_now())
    return VoiceCreditsResponse(
        tenant_id=tenant_id,
        free_launch=balance.free_launch,
        has_credit=balance.has_credit,
        allowance_period=balance.allowance_period,
        allowance_limit=balance.allowance_limit,
        allowance_used=balance.allowance_used,
        allowance_remaining=balance.allowance_remaining,
        pack_calls_remaining=balance.pack_calls_remaining,
        packs=[VoicePackView.model_validate(pack) for pack in balance.packs],
        shop_today=balance.shop_today,
        shop_cap=balance.shop_cap,
        global_today=balance.global_today,
        global_cap=balance.global_cap,
    )


@router.post(
    "/voice/credits/{tenant_id}/packs",
    response_model=VoicePackGrantResponse,
    summary="Grant a shop a voice call pack (90 days)",
)
async def grant_voice_pack(
    tenant_id: uuid.UUID,
    payload: VoicePackGrantPayload,
    admin: CurrentAdmin,
    db: AdminDb,
    settings: SettingsDep,
) -> VoicePackGrantResponse:
    """Calls on top of the plan's monthly allowance, spent after it, oldest
    pack first. A repeated idempotency key returns the first pack."""
    from app.voice.credits import VoiceCredits

    admin.require(AdminPermission.BILLING_GRANT)
    await _shop_or_404(db, tenant_id)
    pack, created = await VoiceCredits(db, settings=settings).grant_pack(
        tenant_id,
        calls=payload.calls,
        grant_key=payload.idempotency_key,
        reason=payload.reason,
        granted_by=admin.label,
        payment_submission_id=payload.payment_submission_id,
    )
    if created:
        await record_audit(
            db,
            AuditAction.ADMIN_VOICE_PACK_GRANTED,
            entity_type="voice_credit_pack",
            entity_id=pack.id,
            tenant_id=tenant_id,
            context={
                "calls": pack.calls,
                "source": pack.source,
                "expires_at": pack.expires_at.isoformat(),
                "payment_submission_id": (
                    str(pack.payment_submission_id) if pack.payment_submission_id else None
                ),
                "admin": admin.label,
            },
        )
    return VoicePackGrantResponse(pack=VoicePackView.model_validate(pack), created=created)


@router.get(
    "/voice/report",
    response_model=VoiceReportResponse,
    summary="IVR calls, answer rate, confirm rate and cost per shop per day",
)
async def voice_call_report(
    admin: CurrentAdmin,
    db: AdminDb,
    settings: SettingsDep,
    day_from: Annotated[date, Query(alias="from")],
    day_to: Annotated[date, Query(alias="to")],
    tenant_id: uuid.UUID | None = None,
) -> VoiceReportResponse:
    """Asia/Dhaka days, at most 31. Cost is what the provider reported, else
    the manifest's price marked estimated, else unknown."""
    from app.voice.credits import voice_report
    from app.voice.manifest import VoiceFact

    admin.require(AdminPermission.OPS_READ)
    prices = {
        name: (
            manifest.fact(VoiceFact.PRICE_PER_CALL_PAISA),
            manifest.fact(VoiceFact.PRICE_PER_MINUTE_PAISA),
        )
        for name, manifest in available_voice_manifests(settings).items()
    }
    rows = await voice_report(
        db, day_from=day_from, day_to=day_to, tenant_id=tenant_id, prices=prices
    )
    return VoiceReportResponse(
        day_from=day_from,
        day_to=day_to,
        rows=[VoiceReportRow.model_validate(row.as_dict()) for row in rows],
    )


# --------------------------------------------------------------------------- #
# Manual bKash / Nagad payments (V4 WP2.5)
# --------------------------------------------------------------------------- #


def _payment_review(
    admin: AdminPrincipal, db: AsyncSession, settings: Any, registry: Any
) -> ManualPaymentReview:
    return ManualPaymentReview(
        db, settings=settings, registry=registry, admin_id=admin.admin_id, admin_label=admin.label
    )


@router.get(
    "/payments",
    response_model=list[ManualPaymentReviewResponse],
    summary="Sellers' bKash / Nagad TrxID payments",
)
async def list_manual_payments(
    admin: CurrentAdmin,
    db: AdminDb,
    settings: SettingsDep,
    registry: ProviderRegistryDep,
    payment_state: Annotated[ManualPaymentState | None, Query(alias="state")] = None,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ManualPaymentReviewResponse]:
    """``?state=SUBMITTED`` is the work queue, oldest first."""
    admin.require(AdminPermission.BILLING_READ)
    rows = await _payment_review(admin, db, settings, registry).list(
        state=payment_state, tenant_id=tenant_id, limit=limit
    )
    return [ManualPaymentReviewResponse.model_validate(row) for row in rows]


@router.post(
    "/payments/{submission_id}/approve",
    response_model=ManualPaymentReviewResponse,
    summary="Approve a TrxID payment: grant the plan",
)
async def approve_manual_payment(
    submission_id: uuid.UUID,
    payload: ManualPaymentApprovePayload,
    admin: CurrentAdmin,
    db: AdminDb,
    settings: SettingsDep,
    registry: ProviderRegistryDep,
) -> ManualPaymentReviewResponse:
    """Only after the TrxID and amount were found in the receiving wallet.

    Grants through the manual billing provider with the period end fixed now;
    approving again returns the same row and grants nothing more.
    """
    admin.require(AdminPermission.BILLING_GRANT)
    row, _changed = await _payment_review(admin, db, settings, registry).approve(
        submission_id, note=payload.note
    )
    return ManualPaymentReviewResponse.model_validate(row)


@router.post(
    "/payments/{submission_id}/reject",
    response_model=ManualPaymentReviewResponse,
    summary="Reject a TrxID payment",
)
async def reject_manual_payment(
    submission_id: uuid.UUID,
    payload: ManualPaymentRejectPayload,
    admin: CurrentAdmin,
    db: AdminDb,
    settings: SettingsDep,
    registry: ProviderRegistryDep,
) -> ManualPaymentReviewResponse:
    admin.require(AdminPermission.BILLING_GRANT)
    row, _changed = await _payment_review(admin, db, settings, registry).reject(
        submission_id, reason=payload.reason
    )
    return ManualPaymentReviewResponse.model_validate(row)


# --------------------------------------------------------------------------- #
# Referrals (V4 WP7.4)
# --------------------------------------------------------------------------- #


@router.get(
    "/referrals",
    response_model=list[ReferralAdminResponse],
    summary="Shop referrals and their credits",
)
async def list_shop_referrals(
    admin: CurrentAdmin,
    db: AdminDb,
    referral_state: Annotated[ReferralState | None, Query(alias="state")] = None,
    referrer_credit: Annotated[ReferralCreditState | None, Query()] = None,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReferralAdminResponse]:
    """``?referrer_credit=HELD`` is the work queue: referrers whose 30 days were
    not added automatically (a billing-provider subscription, or a shop that is
    not active); grant them by hand with the support-credit repair if right."""
    admin.require(AdminPermission.BILLING_READ)
    rows = await list_referrals(
        db,
        state=referral_state,
        referrer_credit_state=referrer_credit,
        tenant_id=tenant_id,
        limit=limit,
    )
    return [ReferralAdminResponse.model_validate(row) for row in rows]


# --------------------------------------------------------------------------- #
# Support cases
# --------------------------------------------------------------------------- #


@router.get("/support-cases", response_model=list[SupportCaseResponse], summary="Support cases")
async def list_support_cases(
    service: AdminServiceDep,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    case_status: Annotated[SupportCaseStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SupportCaseResponse]:
    cases = await service.list_cases(tenant_id=tenant_id, status=case_status, limit=limit)
    return [SupportCaseResponse.model_validate(case, from_attributes=True) for case in cases]


@router.post(
    "/support-cases",
    response_model=SupportCaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a support case",
)
async def open_support_case(
    payload: SupportCaseCreatePayload, service: AdminServiceDep
) -> SupportCaseResponse:
    case = await service.open_case(
        subject=payload.subject,
        case_type=SupportCaseType(payload.case_type),
        severity=SupportCaseSeverity(payload.severity),
        tenant_id=payload.tenant_id,
        detail=payload.detail,
        order_id=payload.order_id,
        consignment_id=payload.consignment_id,
        payout_id=payload.payout_id,
        reconciliation_case_id=payload.reconciliation_case_id,
        subscription_id=payload.subscription_id,
    )
    return SupportCaseResponse.model_validate(case, from_attributes=True)


@router.patch(
    "/support-cases/{case_id}", response_model=SupportCaseResponse, summary="Update a case"
)
async def update_support_case(
    case_id: uuid.UUID, payload: SupportCaseUpdatePayload, service: AdminServiceDep
) -> SupportCaseResponse:
    case = await service.update_case(
        case_id,
        status=SupportCaseStatus(payload.status) if payload.status else None,
        severity=SupportCaseSeverity(payload.severity) if payload.severity else None,
        assigned_to=payload.assigned_to,
        resolution=payload.resolution,
    )
    return SupportCaseResponse.model_validate(case, from_attributes=True)


# --------------------------------------------------------------------------- #
# Repairs
# --------------------------------------------------------------------------- #


@router.get("/repairs", response_model=list[RepairActionResponse], summary="Available repairs")
async def list_repairs(admin: CurrentAdmin) -> list[RepairActionResponse]:
    """The complete, closed set of repairs (master spec section 103).

    Actions the caller's role cannot run are still listed, marked
    ``permitted: false``, so an operator can see what exists and ask someone
    who can rather than wondering whether the console is broken.
    """
    return [
        RepairActionResponse(
            name=action.name,
            summary=action.summary,
            permission=str(action.permission),
            is_sensitive=action.is_sensitive,
            permitted=admin.can(action.permission),
        )
        for action in sorted(REPAIR_ACTIONS.values(), key=lambda a: a.name)
    ]


@router.post("/repairs/{action}", response_model=RepairResultResponse, summary="Run a repair")
async def run_repair(
    action: str,
    payload: RepairRequestPayload,
    admin: CurrentAdmin,
    db: AdminDb,
    settings: SettingsDep,
    registry: ProviderRegistryDep,
) -> RepairResultResponse:
    """Authorise, deduplicate, run and record one repair.

    A repeated idempotency key returns the first run's result rather than
    running again: two operators reacting to the same alert must not both
    reverse the same settlement.
    """
    runner = RepairRunner(db, settings=settings, admin=admin, registry=registry)
    record, result = await runner.run(
        RepairRequest(
            action=action,
            reason=payload.reason,
            tenant_id=payload.tenant_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            idempotency_key=payload.idempotency_key,
            params=payload.params,
        )
    )
    return RepairResultResponse(
        id=record.id,
        action=record.action,
        outcome=str(result.outcome),
        detail=result.detail,
        data=result.data or {},
        reason=record.reason,
        admin_label=record.admin_label,
        created_at=record.created_at,
    )


def admin_roles() -> list[str]:
    """The platform admin roles, for documentation and tests."""
    return [str(role) for role in PlatformAdminRole]


__all__ = ["admin_roles", "router"]
