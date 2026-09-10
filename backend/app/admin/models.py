"""Platform administration and support.

Master spec sections 44, 102 and 103.

The rule that shapes every table here is section 44's last two lines: *"Admin
cannot casually browse customer PII"* and *"every sensitive admin action is
audited."* So:

*   an admin is a **separate identity** from any seller account — a shop owner
    who is also an operator holds two credentials, and losing the seller one
    grants nothing here;
*   an admin token is stored hashed, exactly like a refresh token;
*   a repair action is a *record*, not just a log line: it carries the reason,
    the actor, an idempotency key and its outcome, because a repair that ran
    twice is the second most expensive kind of support incident.

There is no "edit database row" capability anywhere in this module. Every
mutation is a named action with its own authorisation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = [
    "ADMIN_PERMISSIONS",
    "AdminPermission",
    "PlatformAdmin",
    "PlatformAdminRole",
    "RepairActionRecord",
    "RepairOutcome",
    "SupportCase",
    "SupportCaseSeverity",
    "SupportCaseStatus",
    "SupportCaseType",
    "admin_permissions_for",
]


class PlatformAdminRole(StrEnum):
    """What an operator is allowed to do.

    Three levels, because the interesting boundary is not "admin or not" but
    "can this person move money and unmask a customer?"
    """

    #: Read the console, open and update support cases. No repairs, no reveals.
    SUPPORT = "SUPPORT"
    #: Everything SUPPORT can do, plus repairs, feature flags and provider
    #: capability switches.
    OPS = "OPS"
    #: Everything, including support credit and an audited PII reveal.
    SUPERADMIN = "SUPERADMIN"


class AdminPermission(StrEnum):
    """Granular admin capabilities. Endpoints depend on these, not on a role."""

    TENANT_READ = "admin.tenant_read"
    BILLING_READ = "admin.billing_read"
    OPS_READ = "admin.ops_read"
    AUDIT_READ = "admin.audit_read"

    SUPPORT_CASE_WRITE = "admin.support_case_write"
    SUPPORT_BUNDLE_EXPORT = "admin.support_bundle_export"

    REPAIR_RUN = "admin.repair_run"
    FEATURE_FLAG_WRITE = "admin.feature_flag_write"

    #: Issue plan credit. Separated from REPAIR_RUN because it is the one admin
    #: action that gives away revenue.
    BILLING_GRANT = "admin.billing_grant"
    #: Reveal a masked phone number, with a reason and an audit entry.
    PII_REVEAL = "admin.pii_reveal"


_MATRIX: dict[PlatformAdminRole, frozenset[AdminPermission]] = {
    PlatformAdminRole.SUPPORT: frozenset(
        {
            AdminPermission.TENANT_READ,
            AdminPermission.BILLING_READ,
            AdminPermission.OPS_READ,
            AdminPermission.SUPPORT_CASE_WRITE,
        }
    ),
    PlatformAdminRole.OPS: frozenset(
        {
            AdminPermission.TENANT_READ,
            AdminPermission.BILLING_READ,
            AdminPermission.OPS_READ,
            AdminPermission.AUDIT_READ,
            AdminPermission.SUPPORT_CASE_WRITE,
            AdminPermission.SUPPORT_BUNDLE_EXPORT,
            AdminPermission.REPAIR_RUN,
            AdminPermission.FEATURE_FLAG_WRITE,
        }
    ),
    PlatformAdminRole.SUPERADMIN: frozenset(AdminPermission),
}

#: Exposed for the console and for tests that assert the boundary.
ADMIN_PERMISSIONS = _MATRIX


def admin_permissions_for(role: PlatformAdminRole | str) -> frozenset[AdminPermission]:
    try:
        return _MATRIX[PlatformAdminRole(role)]
    except ValueError:
        return frozenset()


class PlatformAdmin(Base, PrimaryKeyMixin, TimestampMixin):
    """An operator credential.

    Deliberately **not** tenant-owned and deliberately not a role on
    ``tenant_users``: platform administration is a different authority from shop
    membership, and conflating them is how a compromised seller account becomes
    a platform compromise.

    ``user_id`` is optional and informational — it records which person holds
    the credential when that is known. It grants nothing on its own.
    """

    __tablename__ = "platform_admins"
    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="uq_platform_admins_token_hash"),
        sa.Index("ix_platform_admins_active", "is_active"),
    )

    label: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    role: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default=PlatformAdminRole.SUPPORT
    )

    #: Keyed hash. The token itself is shown once, at creation, and never stored.
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    def is_usable(self, *, at: datetime | None = None) -> bool:
        moment = at or utc_now()
        if not self.is_active or self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > moment


# --------------------------------------------------------------------------- #
# Support cases (master spec section 102)
# --------------------------------------------------------------------------- #


class SupportCaseType(StrEnum):
    BILLING = "BILLING"
    PAYOUT = "PAYOUT"
    RECONCILIATION = "RECONCILIATION"
    COURIER = "COURIER"
    ORDER = "ORDER"
    DATA = "DATA"
    ACCOUNT = "ACCOUNT"
    OTHER = "OTHER"


class SupportCaseSeverity(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    #: The seller cannot operate, or money is wrong.
    CRITICAL = "CRITICAL"


class SupportCaseStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    WAITING_ON_SELLER = "WAITING_ON_SELLER"
    WAITING_ON_PROVIDER = "WAITING_ON_PROVIDER"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"

    @property
    def is_terminal(self) -> bool:
        return self in (SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED)


class SupportCase(Base, PrimaryKeyMixin, TimestampMixin):
    """One support investigation (master spec section 102).

    Platform-scoped rather than tenant-owned: support opens cases *about* a
    tenant, and the console lists cases across every shop. ``tenant_id`` is a
    plain column so that listing works without a tenancy bypass on every read.

    Nothing here holds a credential. Section 102's rule — *"never require a
    seller to send courier passwords over chat"* — means the case links to the
    records support needs and stops there.
    """

    __tablename__ = "support_cases"
    __table_args__ = (
        sa.Index("ix_support_cases_status_created", "status", "created_at"),
        sa.Index("ix_support_cases_tenant_created", "tenant_id", "created_at"),
        sa.Index("ix_support_cases_reference", "reference"),
    )

    #: Human-quotable identifier, e.g. ``SC-20260910-0007``.
    reference: Mapped[str] = mapped_column(sa.String(32), nullable=False)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    case_type: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    severity: Mapped[str] = mapped_column(
        sa.String(12), nullable=False, default=SupportCaseSeverity.NORMAL
    )
    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=SupportCaseStatus.OPEN
    )

    subject: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: What support knows. Redacted on write, like every other free-text field
    #: that a person types while looking at a customer record.
    detail: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    order_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    consignment_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    payout_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    reconciliation_case_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    opened_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    resolution: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)


# --------------------------------------------------------------------------- #
# Repair actions (master spec section 103)
# --------------------------------------------------------------------------- #


class RepairOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    #: The same idempotency key already ran. The stored result is returned.
    REPLAYED = "REPLAYED"
    #: The action is real but cannot run in this deployment — a provider that is
    #: not configured, a capability that does not exist yet.
    UNAVAILABLE = "UNAVAILABLE"


class RepairActionRecord(Base, PrimaryKeyMixin):
    """One invocation of a named repair.

    Platform-scoped. The reason is mandatory at the API boundary and stored
    here, so "who rebuilt this shop's money summary, and why?" is answerable
    from one table rather than by reading application logs.
    """

    __tablename__ = "admin_repair_actions"
    __table_args__ = (
        sa.UniqueConstraint(
            "action", "idempotency_key", name="uq_admin_repair_actions_action_idempotency_key"
        ),
        sa.Index("ix_admin_repair_actions_created", "created_at"),
        sa.Index("ix_admin_repair_actions_tenant", "tenant_id"),
    )

    action: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    #: Caller-supplied, or derived from the action plus its target. Two operators
    #: reacting to the same alert must not run the repair twice.
    idempotency_key: Mapped[str] = mapped_column(sa.String(200), nullable=False)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    target_type: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    target_id: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)

    admin_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    admin_label: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)

    outcome: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: Redacted summary of what changed. Never a provider payload.
    result: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
