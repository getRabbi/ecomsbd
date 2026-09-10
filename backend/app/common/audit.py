"""Append-only audit log.

Master spec sections 44, 47, 103 and 134: every sensitive or destructive action
records who did it, to what, and why. Manual financial corrections additionally
require a reason code and a note before they are accepted.

The table is append-only by contract: there is no update or delete path in the
application, and the writer helper is the only supported way in.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.core.context import ActorType, current_context
from app.core.ids import new_id
from app.core.redaction import redact_value
from app.db.base import Base
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = ["AuditAction", "AuditLog", "record_audit"]


class AuditAction(StrEnum):
    """Audited action names.

    Values are stored, so they are append-only too: renaming one would rewrite
    the meaning of historical rows.
    """

    # Authentication
    OTP_REQUESTED = "auth.otp_requested"
    OTP_VERIFIED = "auth.otp_verified"
    OTP_FAILED = "auth.otp_failed"
    SESSION_CREATED = "auth.session_created"
    SESSION_REFRESHED = "auth.session_refreshed"
    SESSION_REVOKED = "auth.session_revoked"
    REFRESH_TOKEN_REUSE_DETECTED = "auth.refresh_token_reuse_detected"

    # Tenancy
    TENANT_CREATED = "tenant.created"
    TENANT_UPDATED = "tenant.updated"
    TENANT_MEMBER_ADDED = "tenant.member_added"
    TENANT_MEMBER_ROLE_CHANGED = "tenant.member_role_changed"
    TENANT_MEMBER_REMOVED = "tenant.member_removed"

    # Security
    CROSS_TENANT_ACCESS_BLOCKED = "security.cross_tenant_access_blocked"
    RATE_LIMIT_TRIPPED = "security.rate_limit_tripped"

    # Entitlements and flags
    ENTITLEMENT_DENIED = "billing.entitlement_denied"
    FEATURE_FLAG_CHANGED = "ops.feature_flag_changed"

    # Commerce core (phase B)
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    STOCK_ADJUSTED = "inventory.stock_adjusted"
    STOCK_RECALCULATED = "inventory.stock_recalculated"
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    CUSTOMER_BLOCKED = "customer.blocked"
    ORDER_CREATED = "order.created"
    ORDER_UPDATED = "order.updated"
    ORDER_STATUS_CHANGED = "order.status_changed"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_DUPLICATE_WARNED = "order.duplicate_warned"
    IMPORT_CREATED = "import.created"
    IMPORT_COMMITTED = "import.committed"
    SYNC_MUTATION_APPLIED = "sync.mutation_applied"
    SYNC_CONFLICT_DETECTED = "sync.conflict_detected"

    # Money core (phase D)
    CONSIGNMENT_DISPATCHED = "consignment.dispatched"
    CONSIGNMENT_STATUS_CHANGED = "consignment.status_changed"
    RECEIVABLE_WRITTEN_OFF = "money.receivable_written_off"
    RECEIVABLE_DISPUTED = "money.receivable_disputed"
    PAYOUT_RECORDED = "money.payout_recorded"
    PAYOUT_IMPORTED = "money.payout_imported"
    PAYOUT_RECONCILED = "money.payout_reconciled"
    RECONCILIATION_CASE_OPENED = "money.case_opened"
    RECONCILIATION_CASE_RESOLVED = "money.case_resolved"

    # Profit and alerts (phase E)
    CHARGE_SUPERSEDED = "profit.charge_superseded"
    PROFIT_SNAPSHOT_REVISED = "profit.snapshot_revised"
    EXPENSE_RECORDED = "profit.expense_recorded"
    EXPENSE_ALLOCATED = "profit.expense_allocated"
    NOTIFICATION_SENT = "alerts.notification_sent"

    # Billing and subscriptions (phase F)
    BILLING_PURCHASE_VERIFIED = "billing.purchase_verified"
    BILLING_PURCHASE_REFUSED = "billing.purchase_refused"
    BILLING_REPLAY_BLOCKED = "billing.replay_blocked"
    BILLING_CHECKOUT_STARTED = "billing.checkout_started"
    BILLING_WEBHOOK_RECEIVED = "billing.webhook_received"
    BILLING_WEBHOOK_REJECTED = "billing.webhook_rejected"
    BILLING_MANUAL_GRANT = "billing.manual_grant"
    BILLING_RECONCILED = "billing.reconciled"
    SUBSCRIPTION_STATE_CHANGED = "billing.subscription_state_changed"
    SUBSCRIPTION_CANCELLED = "billing.subscription_cancelled"

    # Platform admin and support (phase F, sections 44, 102, 103)
    ADMIN_AUTHENTICATED = "admin.authenticated"
    ADMIN_ACCESS_DENIED = "admin.access_denied"
    ADMIN_TENANT_VIEWED = "admin.tenant_viewed"
    ADMIN_PII_REVEALED = "admin.pii_revealed"
    ADMIN_SUPPORT_BUNDLE_EXPORTED = "admin.support_bundle_exported"
    SUPPORT_CASE_OPENED = "support.case_opened"
    SUPPORT_CASE_UPDATED = "support.case_updated"

    # Privacy, exports and sessions (phase F, sections 99, 100)
    EXPORT_REQUESTED = "data.export_requested"
    EXPORT_DOWNLOADED = "data.export_downloaded"
    EXPORT_FAILED = "data.export_failed"
    ACCOUNT_DELETION_REQUESTED = "privacy.account_deletion_requested"
    ACCOUNT_DELETION_CANCELLED = "privacy.account_deletion_cancelled"
    ACCOUNT_DELETION_EXECUTED = "privacy.account_deletion_executed"
    DEVICE_REVOKED = "auth.device_revoked"

    # Operations (phase F, sections 45, 49)
    PROVIDER_HEALTH_CHANGED = "ops.provider_health_changed"
    NOTIFICATION_DISPATCH_FAILED = "ops.notification_dispatch_failed"

    # Reserved for later phases (sections 103, 134)
    COURIER_CREDENTIAL_SAVED = "courier.credential_saved"
    COURIER_CREDENTIAL_REVOKED = "courier.credential_revoked"
    MANUAL_FINANCIAL_CORRECTION = "money.manual_correction"
    RECONCILIATION_MANUAL_MATCH = "money.manual_match"
    RECONCILIATION_UNMATCHED = "money.unmatched"
    DATA_EXPORTED = "data.exported"
    PHONE_REVEALED = "privacy.phone_revealed"
    ADMIN_REPAIR_ACTION = "admin.repair_action"


class AuditLog(Base):
    """One audited action. Append-only."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        sa.Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),
        sa.Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    # Nullable: platform-level actions (admin, system jobs) have no tenant.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)

    actor_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    actor_label: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    action: Mapped[str] = mapped_column(sa.String(80), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)

    #: Required for manual financial corrections (master spec section 134).
    reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: Redacted before storage; an audit row is not a place to leak a secret.
    context: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)

    trace_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    #: Hashed, never the raw address, so the log is not a location dataset.
    client_ip_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )


async def record_audit(
    session: AsyncSession,
    action: AuditAction | str,
    *,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    reason: str | None = None,
    context: dict[str, Any] | None = None,
    tenant_id: uuid.UUID | None = None,
    actor_type: ActorType | None = None,
    actor_id: uuid.UUID | None = None,
    client_ip_hash: str | None = None,
) -> AuditLog:
    """Append an audit entry inside the caller's transaction.

    Sharing the caller's transaction means an audited action and its audit row
    commit or roll back together: there is no "it happened but was not logged".
    """
    ctx = current_context()
    entry = AuditLog(
        tenant_id=tenant_id if tenant_id is not None else ctx.tenant_id,
        actor_type=str(actor_type or ctx.actor_type),
        actor_id=actor_id if actor_id is not None else ctx.user_id,
        action=str(action),
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        reason=reason,
        context=redact_value(context or {}),
        trace_id=ctx.trace_id,
        request_id=ctx.request_id,
        client_ip_hash=client_ip_hash,
    )
    session.add(entry)
    return entry
