"""Account deletion requests.

Master spec section 100. The workflow it prescribes:

1.  ownership confirmation
2.  cancel the active subscription
3.  revoke sessions and credentials
4.  disable integrations
5.  schedule deletion/anonymisation per policy
6.  preserve only what is legitimately required, minimised
7.  a final audit event

The part that needs stating plainly, because it is where products usually get
this wrong: **financial records are not deleted on request.** A COD ledger entry
is evidence of money that moved between a seller, a courier and a customer.
Deleting it would break the reconciliation invariants the whole product rests
on, and would remove records the seller themselves may need. What happens
instead is *anonymisation*: the person becomes unidentifiable, the money stays
countable. The retention policy is documented in ``docs/PRIVACY.md`` and the
grace period is on this row so the seller can see it.

Nothing is deleted immediately. A cooling-off window exists because "delete my
account" is occasionally an angry Tuesday rather than a decision, and because an
irreversible action taken instantly is how a support queue fills with people
asking for their business back.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = ["DELETION_GRACE_DAYS", "DeletionRequest", "DeletionStatus"]

#: How long a seller has to change their mind. Long enough to survive a bad
#: week; short enough that "I asked a month ago" never happens.
DELETION_GRACE_DAYS = 14


class DeletionStatus(StrEnum):
    REQUESTED = "REQUESTED"
    #: Cooling-off period. Access continues; the seller can cancel.
    SCHEDULED = "SCHEDULED"
    #: Anonymisation is running.
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class DeletionRequest(Base, PrimaryKeyMixin):
    """One shop's account deletion request.

    Platform-scoped, deliberately: the row has to outlive the tenant's own data
    so the audit question "was this account deleted, when, and on whose
    request?" stays answerable afterwards.
    """

    __tablename__ = "deletion_requests"
    __table_args__ = (
        sa.Index("ix_deletion_requests_status_due", "status", "scheduled_for"),
        sa.Index("ix_deletion_requests_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    #: The owner who asked. Verified against an active OWNER membership at
    #: request time, not taken from the payload.
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)

    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=DeletionStatus.REQUESTED
    )
    reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    #: When the cooling-off period ends and anonymisation may run.
    scheduled_for: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: What the workflow actually did, step by step. Support's answer to "is my
    #: data gone?" is this list, not a guess.
    steps: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    def is_due(self, *, at: datetime | None = None) -> bool:
        return self.status == DeletionStatus.SCHEDULED and self.scheduled_for <= (at or utc_now())
