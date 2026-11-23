"""``autopilot_decisions``: what Autopilot did with each ready chat draft.

One row per draft, written in the same transaction as the order it made (or
the hold), so the Home card, the daily cap and the pilot's G4 check all read
the same record. Ids and codes only: no phone, name or message text.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID

__all__ = ["AutopilotDecision", "AutopilotOutcome"]


class AutopilotOutcome(StrEnum):
    #: The draft became an order (its confirmation follows in the workflow).
    CONVERTED = "CONVERTED"
    #: A gate failed: the draft stays with the seller, with ``reason_code``.
    HELD = "HELD"


class AutopilotDecision(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "autopilot_decisions"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "draft_id", name="uq_autopilot_decisions_draft"),
        sa.Index("ix_autopilot_decisions_tenant_created", "tenant_id", "created_at"),
        sa.CheckConstraint(
            "(outcome = 'CONVERTED' AND order_id IS NOT NULL)"
            " OR (outcome = 'HELD' AND order_id IS NULL AND reason_code IS NOT NULL)",
            name="ck_autopilot_decisions_outcome",
        ),
    )

    #: No FK: a closed draft is purged after 30 days, the decision stays.
    draft_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    outcome: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True
    )
    #: The workflow run that asked (no FK: runs have their own retention).
    execution_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
