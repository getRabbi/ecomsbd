"""The internal admin/ops console, server side.

Master spec section 44. This module reads across tenants — that is the point of
an ops console — which makes two things non-negotiable:

*   **PII is masked by default** (section 101). Every phone number this module
    returns has already been through :func:`~app.core.redaction.mask_phone`.
    There is exactly one method that returns a full number, it requires a
    reason, and it writes an audit entry before it returns.
*   **No secret is ever returned.** Provider account health is reported as
    state and counters. A courier API key, a Play service account and a bKash
    app secret are not readable through any admin route, by construction: the
    console never loads them.

The session used here is a *system* session, deliberately unscoped. Every query
therefore names its tenant explicitly, and the one that does not — the failure
queues — is aggregating across tenants on purpose.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import AdminPrincipal
from app.admin.models import (
    AdminPermission,
    RepairActionRecord,
    SupportCase,
    SupportCaseSeverity,
    SupportCaseStatus,
    SupportCaseType,
)
from app.auth.models import Device
from app.billing.models import BillingTransaction, BillingWebhookEvent, WebhookProcessingState
from app.common.audit import AuditAction, AuditLog, record_audit
from app.common.outbox import OutboxEvent, OutboxStatus
from app.common.provider_health import ProviderHealthService
from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import business_date, utc_now
from app.core.errors import NotFoundError, ValidationError
from app.core.redaction import redact_value
from app.customers.models import Customer
from app.customers.service import CUSTOMER_PHONE_CONTEXT
from app.entitlements.models import Subscription
from app.entitlements.service import EntitlementResolver
from app.notifications.models import Notification
from app.orders.models import Order
from app.payouts.models import Payout, PayoutSourceFile
from app.reconciliation.models import CaseStatus, ReconciliationCase
from app.sync.models import MutationStatus, SyncMutation
from app.tenants.models import Tenant, TenantUser
from app.users.models import User

__all__ = ["AdminService", "OpsCounts", "TenantSummary"]


@dataclass(frozen=True, slots=True)
class TenantSummary:
    """A shop, as the console lists it. Never carries a full phone number."""

    tenant_id: uuid.UUID
    name: str
    status: str
    business_category: str
    created_at: datetime
    plan: str
    subscription_status: str
    owner_masked_phone: str | None
    order_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "status": self.status,
            "business_category": self.business_category,
            "created_at": self.created_at.isoformat(),
            "plan": self.plan,
            "subscription_status": self.subscription_status,
            "owner_masked_phone": self.owner_masked_phone,
            "order_count": self.order_count,
        }


@dataclass(frozen=True, slots=True)
class OpsCounts:
    """The failure queues section 44 asks the console to surface."""

    booking_unknown: int = 0
    reconciliation_cases_open: int = 0
    payout_imports_unparsed: int = 0
    billing_webhook_failures: int = 0
    billing_verification_failures: int = 0
    outbox_dead_letters: int = 0
    sync_conflicts: int = 0
    notifications_unsent: int = 0
    admin_repairs_today: int = 0
    extra: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, int]:
        data = {
            "booking_unknown": self.booking_unknown,
            "reconciliation_cases_open": self.reconciliation_cases_open,
            "payout_imports_unparsed": self.payout_imports_unparsed,
            "billing_webhook_failures": self.billing_webhook_failures,
            "billing_verification_failures": self.billing_verification_failures,
            "outbox_dead_letters": self.outbox_dead_letters,
            "sync_conflicts": self.sync_conflicts,
            "notifications_unsent": self.notifications_unsent,
            "admin_repairs_today": self.admin_repairs_today,
        }
        data.update(self.extra)
        return data


class AdminService:
    """Read and support operations for the ops console."""

    def __init__(self, session: AsyncSession, *, admin: AdminPrincipal) -> None:
        self._db = session
        self._admin = admin

    # ===================================================================== #
    # Tenants
    # ===================================================================== #

    async def search_tenants(self, query: str | None, *, limit: int = 25) -> list[TenantSummary]:
        """Find shops by name, id, or the last digits of the owner's phone.

        Searching by *full* phone number is deliberately not offered: it would
        turn the console into a lookup tool for "does this person use ecomsbd?",
        which is not a support need. Last-four is enough to confirm a caller.
        """
        statement = sa.select(Tenant).order_by(Tenant.created_at.desc()).limit(limit)
        term = (query or "").strip()

        if term:
            filters: list[Any] = [Tenant.name.ilike(f"%{term}%")]
            with contextlib.suppress(ValueError):
                filters.append(Tenant.id == uuid.UUID(term))
            if term.isdigit() and len(term) >= 4:
                last4 = term[-4:]
                filters.append(
                    Tenant.id.in_(
                        sa.select(TenantUser.tenant_id)
                        .join(User, User.id == TenantUser.user_id)
                        .where(User.phone_last4 == last4)
                    )
                )
            statement = statement.where(sa.or_(*filters))

        tenants = list((await self._db.execute(statement)).scalars().all())
        return [await self._summarise(tenant) for tenant in tenants]

    async def _summarise(self, tenant: Tenant) -> TenantSummary:
        subscription = await self._subscription(tenant.id)
        resolved = EntitlementResolver.resolve(subscription, at=utc_now())
        owner = (
            await self._db.execute(
                sa.select(User)
                .join(TenantUser, TenantUser.user_id == User.id)
                .where(TenantUser.tenant_id == tenant.id, TenantUser.role == "OWNER")
                .limit(1)
            )
        ).scalar_one_or_none()

        order_count = (
            await self._db.execute(
                sa.select(sa.func.count()).select_from(Order).where(Order.tenant_id == tenant.id)
            )
        ).scalar_one()

        return TenantSummary(
            tenant_id=tenant.id,
            name=tenant.name,
            status=tenant.status,
            business_category=tenant.business_category,
            created_at=tenant.created_at,
            plan=str(resolved.plan.code),
            subscription_status=resolved.status,
            owner_masked_phone=owner.masked_phone if owner else None,
            order_count=int(order_count),
        )

    async def tenant_detail(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """One shop's operational picture, masked.

        Viewing is itself audited: section 44 treats looking at a seller's shop
        as a sensitive action, because it is.
        """
        self._admin.require(AdminPermission.TENANT_READ)
        tenant = await self._db.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError("No such shop")

        summary = await self._summarise(tenant)
        subscription = await self._subscription(tenant_id)
        resolved = EntitlementResolver.resolve(subscription, at=utc_now())
        health = await ProviderHealthService(self._db).snapshot(tenant_id=tenant_id)

        members = (
            (
                await self._db.execute(
                    sa.select(TenantUser, User)
                    .join(User, User.id == TenantUser.user_id)
                    .where(TenantUser.tenant_id == tenant_id)
                )
            )
            .tuples()
            .all()
        )

        await record_audit(
            self._db,
            AuditAction.ADMIN_TENANT_VIEWED,
            entity_type="tenant",
            entity_id=tenant_id,
            context={"admin": self._admin.label},
        )

        return {
            "tenant": summary.as_dict(),
            "subscription": _subscription_view(subscription),
            "entitlements": {str(k): v for k, v in resolved.plan.entitlements.items()},
            "members": [
                {
                    "user_id": str(user.id),
                    "role": membership.role,
                    "is_active": membership.is_active,
                    # Masked. The console never shows a dialable number without
                    # an explicit, audited reveal.
                    "masked_phone": user.masked_phone,
                    "joined_at": membership.joined_at.isoformat(),
                }
                for membership, user in members
            ],
            "provider_health": [view.as_dict() for view in health],
            "counts": await self._tenant_counts(tenant_id),
        }

    async def _tenant_counts(self, tenant_id: uuid.UUID) -> dict[str, int]:
        async def count(model: Any, *conditions: Any) -> int:
            statement = sa.select(sa.func.count()).select_from(model).where(*conditions)
            return int((await self._db.execute(statement)).scalar_one())

        return {
            "orders": await count(Order, Order.tenant_id == tenant_id),
            "customers": await count(Customer, Customer.tenant_id == tenant_id),
            "payouts": await count(Payout, Payout.tenant_id == tenant_id),
            "open_cases": await count(
                ReconciliationCase,
                ReconciliationCase.tenant_id == tenant_id,
                ReconciliationCase.status == str(CaseStatus.OPEN),
            ),
            "booking_unknown": await count(
                Consignment,
                Consignment.tenant_id == tenant_id,
                Consignment.status == str(ConsignmentStatus.BOOKING_UNKNOWN),
            ),
        }

    async def _subscription(self, tenant_id: uuid.UUID) -> Subscription | None:
        return (
            await self._db.execute(
                sa.select(Subscription)
                .where(Subscription.tenant_id == tenant_id)
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def billing_history(
        self, tenant_id: uuid.UUID, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        self._admin.require(AdminPermission.BILLING_READ)
        rows = (
            (
                await self._db.execute(
                    sa.select(BillingTransaction)
                    .where(BillingTransaction.tenant_id == tenant_id)
                    .order_by(BillingTransaction.occurred_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": str(row.id),
                "provider": row.provider,
                "plan": row.plan_code,
                "kind": row.kind,
                "state": row.state,
                "amount_paisa": row.amount_paisa,
                "currency": row.currency,
                "verification_result": row.verification_result,
                "detail": row.verification_detail,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in rows
        ]

    # ===================================================================== #
    # PII reveal (master spec section 101)
    # ===================================================================== #

    async def reveal_customer_phone(
        self, *, tenant_id: uuid.UUID, customer_id: uuid.UUID, reason: str, vault: Any
    ) -> str:
        """Return one customer's full phone number, with a reason, audited.

        Every guard section 101 asks for is here: an explicit action, a stated
        reason, an audit entry written *before* the value is returned, and a
        permission only ``SUPERADMIN`` holds. There is no bulk variant and no
        way to reach this from a listing.
        """
        self._admin.require(AdminPermission.PII_REVEAL)
        if len(reason.strip()) < 8:
            raise ValidationError(
                "Revealing a customer's number requires a specific reason",
                details={"minimum_length": "8"},
            )

        customer = (
            await self._db.execute(
                sa.select(Customer).where(
                    Customer.id == customer_id, Customer.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        if customer is None:
            raise NotFoundError("No such customer in that shop")

        await record_audit(
            self._db,
            AuditAction.ADMIN_PII_REVEALED,
            entity_type="customer",
            entity_id=customer_id,
            reason=reason,
            context={"admin": self._admin.label, "field": "phone", "tenant_id": str(tenant_id)},
            tenant_id=tenant_id,
        )
        # Committed before the value leaves this process: a reveal that is not
        # recorded must not happen at all.
        await self._db.commit()
        return str(vault.decrypt(customer.phone_enc, context=CUSTOMER_PHONE_CONTEXT))

    # ===================================================================== #
    # Ops queues
    # ===================================================================== #

    async def ops_counts(self) -> OpsCounts:
        """The console's front page (section 44)."""
        self._admin.require(AdminPermission.OPS_READ)

        async def count(model: Any, *conditions: Any) -> int:
            statement = sa.select(sa.func.count()).select_from(model).where(*conditions)
            return int((await self._db.execute(statement)).scalar_one())

        today_start, _ = _today_bounds()
        return OpsCounts(
            booking_unknown=await count(
                Consignment, Consignment.status == str(ConsignmentStatus.BOOKING_UNKNOWN)
            ),
            reconciliation_cases_open=await count(
                ReconciliationCase, ReconciliationCase.status == str(CaseStatus.OPEN)
            ),
            # A statement that was stored but produced no payout lines is
            # the parser failure worth surfacing; the file itself always saves.
            payout_imports_unparsed=await count(
                PayoutSourceFile,
                sa.not_(sa.exists().where(Payout.source_file_id == PayoutSourceFile.id)),
            ),
            billing_webhook_failures=await count(
                BillingWebhookEvent,
                BillingWebhookEvent.state.in_(
                    [str(WebhookProcessingState.FAILED), str(WebhookProcessingState.REJECTED)]
                ),
            ),
            billing_verification_failures=await count(
                BillingTransaction, BillingTransaction.state == "FAILED"
            ),
            outbox_dead_letters=await count(
                OutboxEvent, OutboxEvent.status == str(OutboxStatus.DEAD)
            ),
            sync_conflicts=await count(
                SyncMutation, SyncMutation.status == str(MutationStatus.CONFLICT)
            ),
            notifications_unsent=await count(Notification, Notification.read_at.is_(None)),
            admin_repairs_today=await count(
                RepairActionRecord, RepairActionRecord.created_at >= today_start
            ),
        )

    async def failed_webhooks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self._admin.require(AdminPermission.OPS_READ)
        rows = (
            (
                await self._db.execute(
                    sa.select(BillingWebhookEvent)
                    .where(
                        BillingWebhookEvent.state.in_(
                            [
                                str(WebhookProcessingState.FAILED),
                                str(WebhookProcessingState.REJECTED),
                            ]
                        )
                    )
                    .order_by(BillingWebhookEvent.received_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": str(row.id),
                "provider": row.provider,
                "state": row.state,
                "event_type": row.event_type,
                "dedupe_source": row.dedupe_source,
                "signature_verified": row.signature_verified,
                "delivery_count": row.delivery_count,
                "error": row.error,
                "tenant_id": str(row.tenant_id) if row.tenant_id else None,
                "received_at": row.received_at.isoformat(),
            }
            for row in rows
        ]

    async def audit_trail(
        self,
        *,
        tenant_id: uuid.UUID | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._admin.require(AdminPermission.AUDIT_READ)
        statement = sa.select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        if tenant_id is not None:
            statement = statement.where(AuditLog.tenant_id == tenant_id)
        if action:
            statement = statement.where(AuditLog.action == action)

        rows = (await self._db.execute(statement)).scalars().all()
        return [
            {
                "id": str(row.id),
                "action": row.action,
                "actor_type": row.actor_type,
                "actor_label": row.actor_label,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "reason": row.reason,
                # Already redacted on write; redacted again on read so a row
                # written before a redaction rule existed cannot leak now.
                "context": redact_value(row.context),
                "tenant_id": str(row.tenant_id) if row.tenant_id else None,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    # ===================================================================== #
    # Support cases (master spec section 102)
    # ===================================================================== #

    async def open_case(
        self,
        *,
        subject: str,
        case_type: SupportCaseType,
        severity: SupportCaseSeverity = SupportCaseSeverity.NORMAL,
        tenant_id: uuid.UUID | None = None,
        detail: str | None = None,
        order_id: uuid.UUID | None = None,
        consignment_id: uuid.UUID | None = None,
        payout_id: uuid.UUID | None = None,
        reconciliation_case_id: uuid.UUID | None = None,
        subscription_id: uuid.UUID | None = None,
    ) -> SupportCase:
        self._admin.require(AdminPermission.SUPPORT_CASE_WRITE)
        if not subject.strip():
            raise ValidationError("A support case needs a subject")

        case = SupportCase(
            reference=await self._next_case_reference(),
            tenant_id=tenant_id,
            case_type=str(case_type),
            severity=str(severity),
            status=str(SupportCaseStatus.OPEN),
            subject=subject.strip()[:200],
            detail=detail,
            order_id=order_id,
            consignment_id=consignment_id,
            payout_id=payout_id,
            reconciliation_case_id=reconciliation_case_id,
            subscription_id=subscription_id,
            opened_by_admin_id=self._admin.admin_id,
        )
        self._db.add(case)
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.SUPPORT_CASE_OPENED,
            entity_type="support_case",
            entity_id=case.id,
            context={
                "admin": self._admin.label,
                "reference": case.reference,
                "type": str(case_type),
            },
            tenant_id=tenant_id,
        )
        return case

    async def update_case(
        self,
        case_id: uuid.UUID,
        *,
        status: SupportCaseStatus | None = None,
        severity: SupportCaseSeverity | None = None,
        assigned_to: str | None = None,
        resolution: str | None = None,
    ) -> SupportCase:
        """Move a case on.

        Closing one needs a resolution. A case closed with nothing written down
        teaches nobody anything, and the same problem comes back next month with
        no record of what was done last time — the same rule the reconciliation
        cases follow.
        """
        self._admin.require(AdminPermission.SUPPORT_CASE_WRITE)
        case = await self._db.get(SupportCase, case_id)
        if case is None:
            raise NotFoundError("No such support case")

        if status is not None:
            if status.is_terminal and not (resolution or case.resolution):
                raise ValidationError("Closing a support case requires a resolution")
            case.status = str(status)
            if status.is_terminal:
                case.resolved_at = utc_now()
        if severity is not None:
            case.severity = str(severity)
        if assigned_to is not None:
            case.assigned_to = assigned_to[:120]
        if resolution is not None:
            case.resolution = resolution

        await record_audit(
            self._db,
            AuditAction.SUPPORT_CASE_UPDATED,
            entity_type="support_case",
            entity_id=case.id,
            context={"admin": self._admin.label, "status": case.status},
            tenant_id=case.tenant_id,
        )
        return case

    async def list_cases(
        self,
        *,
        tenant_id: uuid.UUID | None = None,
        status: SupportCaseStatus | None = None,
        limit: int = 50,
    ) -> list[SupportCase]:
        self._admin.require(AdminPermission.SUPPORT_CASE_WRITE)
        statement = sa.select(SupportCase).order_by(SupportCase.created_at.desc()).limit(limit)
        if tenant_id is not None:
            statement = statement.where(SupportCase.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(SupportCase.status == str(status))
        return list((await self._db.execute(statement)).scalars().all())

    async def _next_case_reference(self) -> str:
        today = business_date()
        prefix = f"SC-{today:%Y%m%d}-"
        used = (
            await self._db.execute(
                sa.select(sa.func.count())
                .select_from(SupportCase)
                .where(SupportCase.reference.like(f"{prefix}%"))
            )
        ).scalar_one()
        return f"{prefix}{int(used) + 1:04d}"

    # ===================================================================== #
    # Support bundle (master spec section 102)
    # ===================================================================== #

    async def support_bundle(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """A redacted diagnostic bundle for one shop.

        What section 102 allows: relevant ids, a status timeline, ledger and
        payout references, provider and billing state, app version. What it
        never contains: a plaintext phone number, a courier credential, a
        purchase token, or any provider response body.
        """
        self._admin.require(AdminPermission.SUPPORT_BUNDLE_EXPORT)
        tenant = await self._db.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError("No such shop")

        subscription = await self._subscription(tenant_id)
        health = await ProviderHealthService(self._db).snapshot(tenant_id=tenant_id)

        recent_orders = (
            (
                await self._db.execute(
                    sa.select(Order)
                    .where(Order.tenant_id == tenant_id)
                    .order_by(Order.created_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        recent_cases = (
            (
                await self._db.execute(
                    sa.select(ReconciliationCase)
                    .where(ReconciliationCase.tenant_id == tenant_id)
                    .order_by(ReconciliationCase.created_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        # App version and platform, per section 102's bundle contents. The
        # push token is deliberately not read: it is a credential.
        device_rows = (
            (
                await self._db.execute(
                    sa.select(Device)
                    .join(TenantUser, TenantUser.user_id == Device.user_id)
                    .where(TenantUser.tenant_id == tenant_id)
                    .order_by(Device.last_seen_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        devices = [
            {
                "platform": device.platform,
                "app_version": device.app_version,
                "os_version": device.os_version,
                "last_seen_at": device.last_seen_at.isoformat(),
                "revoked": device.revoked_at is not None,
            }
            for device in device_rows
        ]

        bundle = {
            "generated_at": utc_now().isoformat(),
            "tenant": {
                "id": str(tenant.id),
                "name": tenant.name,
                "status": tenant.status,
                "timezone": tenant.timezone,
                "created_at": tenant.created_at.isoformat(),
            },
            "subscription": _subscription_view(subscription),
            "provider_health": [view.as_dict() for view in health],
            "recent_orders": [
                {
                    "id": str(order.id),
                    "order_number": order.order_number,
                    "status": order.status,
                    "business_date": order.business_date.isoformat(),
                    "cod_amount_paisa": order.cod_amount_paisa,
                    # Masked, always. Support does not need the number to read
                    # a timeline, and section 33 forbids it in a diagnostic.
                    # Already stored masked. Re-masking would corrupt it.
                    "customer_masked_phone": order.customer_phone_masked,
                }
                for order in recent_orders
            ],
            "reconciliation_cases": [
                {
                    "id": str(case.id),
                    "kind": case.kind,
                    "status": case.status,
                    "summary": case.summary,
                    "amount_paisa": case.amount_paisa,
                    "opened_at": case.opened_at.isoformat(),
                }
                for case in recent_cases
            ],
            "counts": await self._tenant_counts(tenant_id),
            "devices": devices,
        }

        await record_audit(
            self._db,
            AuditAction.ADMIN_SUPPORT_BUNDLE_EXPORTED,
            entity_type="tenant",
            entity_id=tenant_id,
            context={"admin": self._admin.label},
            tenant_id=tenant_id,
        )
        return redact_value(bundle)


def _subscription_view(subscription: Subscription | None) -> dict[str, Any] | None:
    if subscription is None:
        return None
    reference = subscription.provider_reference
    return {
        "id": str(subscription.id),
        "plan": subscription.plan_code,
        "status": subscription.status,
        "provider": subscription.source,
        "distribution_channel": subscription.distribution_channel,
        "current_period_end": subscription.current_period_end.isoformat()
        if subscription.current_period_end
        else None,
        "grace_until": subscription.grace_until.isoformat() if subscription.grace_until else None,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "verified_at": subscription.verified_at.isoformat() if subscription.verified_at else None,
        "last_synced_at": subscription.last_synced_at.isoformat()
        if subscription.last_synced_at
        else None,
        "status_reason": subscription.status_reason,
        # A suffix, never the reference: a bKash agreement id is actionable.
        "provider_reference_suffix": reference[-4:] if reference else None,
        "grant_reason": subscription.grant_reason,
    }


def _today_bounds() -> tuple[datetime, datetime]:
    from app.core.clock import business_day_bounds

    return business_day_bounds(business_date())
