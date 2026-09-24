"""The in-app notification centre.

Master spec section 94: *"Push is not enough."* A seller who misses a push, or
has notifications turned off, still has to be able to find out that ৳8,950 of
delivered parcels has not been paid for. So everything lives in a table they
can open, and push — when there is a push provider — is a second copy of it
rather than the only copy.

Section 95 governs what gets created at all. Its rule is short: notify only
when the seller has to *do* something, material money moved, or a threshold was
crossed. A parcel moving from IN_TRANSIT to OUT_FOR_DELIVERY is none of those,
and a product that notifies about it teaches sellers to ignore it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "Notification",
    "NotificationCategory",
    "NotificationKind",
    "NotificationState",
    "Severity",
]


class Severity(StrEnum):
    """How loud a notification is (master spec section 94)."""

    #: Worth knowing. The Friday summary.
    INFO = "INFO"
    #: The seller has to do something.
    ACTION = "ACTION"
    #: Money is at risk.
    WARNING = "WARNING"
    #: Money is being lost right now.
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @property
    def deserves_push(self) -> bool:
        """Whether this would justify interrupting the seller.

        Section 95: push only when action is required, material money changed,
        or a threshold was crossed. ``INFO`` is none of those — it belongs in
        the centre, waiting to be read.
        """
        return self is not Severity.INFO


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.ACTION: 1,
    Severity.WARNING: 2,
    Severity.CRITICAL: 3,
}


class NotificationKind(StrEnum):
    """What a notification is about.

    The four alerts of master spec section 23, plus the weekly summary and the
    bundle that section 95 asks for in place of many small pushes.
    """

    #: 🔴 Delivered parcels whose money has not arrived.
    DELIVERED_BUT_UNPAID = "DELIVERED_BUT_UNPAID"
    #: 🟠 Payments that came up short.
    UNDERPAID = "UNDERPAID"
    #: 🟡 Parcels that have been in transit far too long.
    STALE_IN_TRANSIT = "STALE_IN_TRANSIT"
    #: 🔵 Returns whose stock was never put back.
    RETURNED_NOT_RESTOCKED = "RETURNED_NOT_RESTOCKED"
    #: More returns than usual, which is worth looking at before it becomes a
    #: month of losses.
    RETURN_SPIKE = "RETURN_SPIKE"
    #: Friday, 18:00 Asia/Dhaka.
    WEEKLY_SUMMARY = "WEEKLY_SUMMARY"
    #: Section 95's "5 parcels need attention" instead of five notifications.
    BUNDLE = "BUNDLE"

    # --- V2.2 smart alerts (app.notifications.smart) ----------------------
    # The four case-derived kinds above stay for history; the V2.2 engine
    # raises these instead, so one condition is never announced twice.

    #: COD a courier holds past the Receivables overdue rule.
    PAYOUT_OVERDUE = "PAYOUT_OVERDUE"
    #: Open Reconciliation V2 cases: missing COD, amount or charge mismatch.
    RECONCILIATION_DISCREPANCY = "RECONCILIATION_DISCREPANCY"
    #: Parcels with no courier movement for far longer than normal.
    COURIER_STATUS_STUCK = "COURIER_STATUS_STUCK"
    #: RTO rate high or rising, by the canonical RTO definition.
    HIGH_RTO = "HIGH_RTO"
    #: Stock at or below the seller's own threshold.
    LOW_STOCK = "LOW_STOCK"
    #: Delivered parcels whose fully measured profit is negative.
    NEGATIVE_MARGIN = "NEGATIVE_MARGIN"
    #: An import that failed, or finished with many rejected rows.
    IMPORT_FAILURE = "IMPORT_FAILURE"

    # --- V3.5 procurement ------------------------------------------------
    #: Purchase orders past their expected date with goods still to come.
    PURCHASE_ORDER_OVERDUE = "PURCHASE_ORDER_OVERDUE"
    #: Purchase orders partly received days ago, the rest still outstanding.
    PARTIAL_RECEIPT_PENDING = "PARTIAL_RECEIPT_PENDING"
    #: Supplier payments past their due date.
    SUPPLIER_PAYMENT_OVERDUE = "SUPPLIER_PAYMENT_OVERDUE"
    #: A courier account whose credentials need re-entering.
    COURIER_ACCOUNT_PROBLEM = "COURIER_ACCOUNT_PROBLEM"
    FOLLOW_UP_DUE = "FOLLOW_UP_DUE"
    AUTOMATION = "AUTOMATION"


class NotificationCategory(StrEnum):
    """What a seller switches on and off, and who in the team receives it."""

    MONEY = "MONEY"
    RECONCILIATION = "RECONCILIATION"
    COURIER = "COURIER"
    RETURNS = "RETURNS"
    INVENTORY = "INVENTORY"
    IMPORTS = "IMPORTS"
    CRM = "CRM"
    #: The Friday summary and bundles.
    SUMMARY = "SUMMARY"


class NotificationState(StrEnum):
    """Where one notification is in its life. Derived, never stored."""

    NEW = "NEW"
    READ = "READ"
    #: The condition it described has cleared. The row is kept as history.
    RESOLVED = "RESOLVED"


class Notification(Base, TenantOwned, PrimaryKeyMixin):
    """One thing the seller should see."""

    __tablename__ = "notifications"
    __table_args__ = (
        # Section 94: "deduplicate repetitive warnings". The same alert, about
        # the same thing, on the same day, is one row. Without this a daily
        # scan would produce a new "7 parcels unpaid" every morning until the
        # seller stopped opening the app.
        sa.UniqueConstraint(
            "tenant_id",
            "kind",
            "dedupe_key",
            name="uq_notifications_tenant_kind_dedupe",
        ),
        sa.Index("ix_notifications_tenant_unread", "tenant_id", "read_at"),
        sa.Index("ix_notifications_tenant_created", "tenant_id", "created_at"),
        # The smart-alert lifecycle looks up "the open alert about this".
        sa.Index("ix_notifications_tenant_kind_subject", "tenant_id", "kind", "subject_key"),
    )

    #: Null for shop-wide notifications. Set when one person is the audience —
    #: the member whose import failed — and then only they see it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    severity: Mapped[str] = mapped_column(sa.String(12), nullable=False)

    #: :class:`NotificationCategory`. Empty on rows older than V2.2, which the
    #: migration backfilled from their kind.
    category: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="")
    #: The permission a member needs to receive it, decided when it was
    #: raised. Empty means every active member (the pre-V2.2 behaviour).
    audience: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    #: The condition this alert is about, without any time window: the
    #: lifecycle's identity for "the same alert again". Empty for one-off rows.
    subject_key: Mapped[str] = mapped_column(sa.String(120), nullable=False, default="")
    #: Set when the condition cleared. The row stays, as history.
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Seller-facing, already written. The UI shows these as they are, so
    #: support and the seller read the same words.
    title: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    body: Mapped[str] = mapped_column(sa.String(600), nullable=False)

    #: What tapping it should open. Section 94: a notification must deep-link
    #: to the exact order, payout or case — one that lands on a dashboard makes
    #: the seller hunt for what it was about.
    entity_type: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    #: What makes this notification the same as another. Usually a kind plus a
    #: business date, so today's alert replaces nothing and tomorrow's is new.
    dedupe_key: Mapped[str] = mapped_column(sa.String(120), nullable=False, default="")

    #: The money the notification is about, so the list can be ordered by what
    #: it costs rather than by when it arrived.
    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: How many things it covers, for a bundle.
    item_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    #: The Asia/Dhaka day it belongs to.
    business_date: Mapped[date] = mapped_column(sa.Date, nullable=False)

    read_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Everything the screen needs without re-deriving it — the Friday
    #: summary's figures live here.
    payload: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    #: No standalone index: every read is tenant-scoped, so
    #: ``ix_notifications_tenant_created`` already covers ordering by arrival.
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    @property
    def notification_kind(self) -> NotificationKind:
        return NotificationKind(self.kind)

    @property
    def notification_severity(self) -> Severity:
        return Severity(self.severity)

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    @property
    def state(self) -> NotificationState:
        if self.resolved_at is not None:
            return NotificationState.RESOLVED
        return NotificationState.READ if self.read_at is not None else NotificationState.NEW

    @property
    def has_deep_link(self) -> bool:
        return self.entity_type is not None and self.entity_id is not None
