"""Automation workflows (V2 rules, extended into multi-step workflows in V3.4).

One engine. A V2 rule is a workflow of one action step; every workflow runs
through the same executions table and the same dispatcher.

* ``AutomationRule`` is the workflow: its name, the editable draft and a pointer
  to the published version new events use.
* ``AutomationWorkflowVersion`` is an immutable published definition. An
  execution records the version it started on and finishes on it, whatever is
  edited or published afterwards.
* ``AutomationExecution`` is one run for one trigger event. ``(rule, event)`` is
  unique, so a replayed event cannot start a second run.
* ``AutomationStepRun`` is the receipt for one step of one run. It is written in
  the same transaction as the step's local side effect, so a retry resumes after
  the last receipt and never repeats a step that already happened.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime


class AutomationRule(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_rules"
    name: Mapped[str] = mapped_column(sa.String(100))
    #: The published trigger; kept here so matching an event is one indexed query.
    trigger: Mapped[str] = mapped_column(sa.String(80))
    #: V2 summary columns. For a V3.4 workflow ``action`` is ``WORKFLOW``.
    conditions: Mapped[list] = mapped_column(JSONColumn)
    action: Mapped[str] = mapped_column(sa.String(32))
    config: Mapped[dict] = mapped_column(JSONColumn)
    enabled: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    #: Whoever last published: the workflow runs with their authority.
    created_by: Mapped[uuid.UUID] = mapped_column(GUID)
    #: The editable draft (V3.4 definition). Never read by the engine.
    draft: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)
    #: Number of the version new events use; ``None`` until first published.
    published_version: Mapped[int | None] = mapped_column(nullable=True)
    recipe_key: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)


class AutomationWorkflowVersion(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_workflow_versions"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "rule_id", "number"),)
    rule_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("automation_rules.id"))
    number: Mapped[int] = mapped_column()
    trigger: Mapped[str] = mapped_column(sa.String(80))
    #: The definition as the seller built it (nested steps).
    definition: Mapped[dict] = mapped_column(JSONColumn)
    #: The compiled plan the engine walks: ``{"entry": id, "steps": {id: step}}``.
    plan: Mapped[dict] = mapped_column(JSONColumn)
    published_by: Mapped[uuid.UUID] = mapped_column(GUID)


class AutomationExecution(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_executions"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "rule_id", "event_id"),
        sa.Index("ix_automation_due", "status", "next_attempt_at"),
        sa.Index("ix_automation_wait_key", "tenant_id", "wait_key"),
        sa.Index("ix_automation_rule_subject", "tenant_id", "rule_id", "subject_key"),
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("automation_rules.id"))
    event_id: Mapped[uuid.UUID] = mapped_column(GUID)
    rule_version: Mapped[int] = mapped_column()
    #: The subject (order, customer, product...) and the facts at trigger time.
    snapshot: Mapped[dict] = mapped_column(JSONColumn)
    #: QUEUED, WAITING, RUNNING (courier booking in flight only), SUCCEEDED,
    #: FAILED, CANCELLED or SKIPPED (entry conditions not met / guard stopped it).
    status: Mapped[str] = mapped_column(sa.String(24), default="QUEUED")
    #: Automatic attempts of the current step; reset when a step succeeds.
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(TZDateTime)
    #: Seller-safe reason code. Never a raw exception message.
    last_error: Mapped[str | None] = mapped_column(sa.String(80))
    result_id: Mapped[str | None] = mapped_column(sa.String(80))
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("automation_workflow_versions.id", name="fk_automation_executions_version"),
        nullable=True,
    )
    trigger: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    #: The step to run next (or the step that failed / is waiting).
    cursor: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    #: Whether a seller retry is offered for a FAILED run.
    retryable: Mapped[bool | None] = mapped_column(nullable=True)
    #: Causation: how many workflow runs led to this one, and which workflows.
    depth: Mapped[int] = mapped_column(default=0)
    ancestry: Mapped[list | None] = mapped_column(JSONColumn, nullable=True)
    parent_execution_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    subject_key: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    #: EVENT (a trigger), TEST (explicitly confirmed manual run).
    source: Mapped[str] = mapped_column(sa.String(16), default="EVENT")
    #: Event wait: what this run waits for, and when the event arrived.
    wait_key: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    wait_satisfied_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class AutomationStepRun(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_step_runs"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "execution_id", "step_id"),)
    execution_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("automation_executions.id"))
    step_id: Mapped[str] = mapped_column(sa.String(40))
    kind: Mapped[str] = mapped_column(sa.String(24))
    #: SUCCEEDED, SKIPPED (a compliance blocker, e.g. no consent), WAITING,
    #: TIMED_OUT, IN_FLIGHT (courier booking sent) or FAILED.
    status: Mapped[str] = mapped_column(sa.String(16))
    result_id: Mapped[str | None] = mapped_column(sa.String(80))
    #: For a branch: THEN or ELSE. For a skip or failure: the reason code.
    outcome: Mapped[str | None] = mapped_column(sa.String(80))
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class AutomationAttempt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_attempts"
    execution_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("automation_executions.id"))
    status: Mapped[str] = mapped_column(sa.String(24))
    error: Mapped[str | None] = mapped_column(sa.String(80))
    step_id: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)


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
