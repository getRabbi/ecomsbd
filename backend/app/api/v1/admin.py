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

from fastapi import APIRouter, Depends, Header, Query, status
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
    RepairActionResponse,
    RepairRequestPayload,
    RepairResultResponse,
    RevealPhonePayload,
    RevealPhoneResponse,
    SupportCaseCreatePayload,
    SupportCaseResponse,
    SupportCaseUpdatePayload,
    TenantSummaryResponse,
)
from app.billing.manual_payments import ManualPaymentReview
from app.billing.models import ManualPaymentState
from app.billing.providers.registry import PROVIDER_FLAGS
from app.common.audit import AuditAction, record_audit
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.common.provider_health import ProviderHealthService
from app.core.errors import ValidationError
from app.db.session import system_session
from app.intake import rules as intake_rules
from app.product_events.service import ProductEventService

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
