import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime


class AutomationRule(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_rules"
    name: Mapped[str] = mapped_column(sa.String(100))
    trigger: Mapped[str] = mapped_column(sa.String(80))
    conditions: Mapped[list] = mapped_column(JSONColumn)
    action: Mapped[str] = mapped_column(sa.String(32))
    config: Mapped[dict] = mapped_column(JSONColumn)
    enabled: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID)


class AutomationExecution(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_executions"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "rule_id", "event_id"),
        sa.Index("ix_automation_due", "status", "next_attempt_at"),
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("automation_rules.id"))
    event_id: Mapped[uuid.UUID] = mapped_column(GUID)
    rule_version: Mapped[int] = mapped_column()
    snapshot: Mapped[dict] = mapped_column(JSONColumn)
    status: Mapped[str] = mapped_column(sa.String(24), default="PENDING")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_error: Mapped[str | None] = mapped_column(sa.String(80))
    result_id: Mapped[str | None] = mapped_column(sa.String(80))


class AutomationAttempt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_attempts"
    execution_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("automation_executions.id"))
    status: Mapped[str] = mapped_column(sa.String(24))
    error: Mapped[str | None] = mapped_column(sa.String(80))


class AutomationTask(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_tasks"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "execution_id"),)
    execution_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("automation_executions.id"))
    order_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("orders.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("customers.id"))
    text_en: Mapped[str] = mapped_column(sa.String(1000))
    text_bn: Mapped[str] = mapped_column(sa.String(1000))
    due_at: Mapped[datetime] = mapped_column(TZDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
