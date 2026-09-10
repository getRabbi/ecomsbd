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
    "NotificationKind",
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
    )

    #: Null for shop-wide notifications, which is all of them in V1: there is
    #: no team UI yet, so everything addresses the shop rather than a person.
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    severity: Mapped[str] = mapped_column(sa.String(12), nullable=False)

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
    def has_deep_link(self) -> bool:
        return self.entity_type is not None and self.entity_id is not None
