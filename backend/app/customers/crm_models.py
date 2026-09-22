"""Shop-private CRM additions. Notes and activity are append-only."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, TZDateTime


class CustomerActivity(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "customer_activities"
    __table_args__ = (
        sa.Index("ix_crm_activity_customer_page", "tenant_id", "customer_id", "created_at", "id"),
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("customers.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(sa.String(32))
    text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)


class CustomerTag(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "customer_tags"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "name_key"),)
    name: Mapped[str] = mapped_column(sa.String(60))
    name_key: Mapped[str] = mapped_column(sa.String(120))
    archived: Mapped[bool] = mapped_column(sa.Boolean, default=False)


class CustomerTagLink(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "customer_tag_links"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "customer_id", "tag_id"),
        sa.Index("ix_crm_tag_customers", "tenant_id", "tag_id", "customer_id"),
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("customers.id", ondelete="CASCADE")
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("customer_tags.id", ondelete="CASCADE")
    )


class CustomerFollowUp(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "customer_followups"
    __table_args__ = (
        sa.Index("ix_crm_followup_due", "tenant_id", "completed_at", "due_at"),
        sa.Index("ix_crm_followup_customer", "tenant_id", "customer_id", "created_at", "id"),
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("customers.id", ondelete="CASCADE")
    )
    text: Mapped[str] = mapped_column(sa.String(2000))
    due_at: Mapped[datetime] = mapped_column(TZDateTime)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
