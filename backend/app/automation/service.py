"""Automation: validation, publishing and turning events into workflow runs.

The one engine for V2 rules and V3.4 workflows. Runs start (and waiting runs
resume) inside the transaction that wrote the domain event; the dispatcher in
``app.automation.jobs`` does the rest.

Loop protection (V3.4 section 11) lives here, where a run is created:

* **causation**: a run records the run that caused it (``parent_execution_id``),
  how deep the chain is and which workflows are in it (``ancestry``);
* **depth limit**: a chain longer than ``MAX_DEPTH`` stops;
* **self-trigger / cycle**: a workflow that is already in its own ancestry does
  not start again — A→B→A is stopped at the second A;
* **repeat guard**: one workflow runs at most ``REPEAT_LIMIT`` times a day for
  the same subject;
* the plan itself is acyclic (``schemas.compile_plan``).

A stopped run is recorded as ``SKIPPED`` with a seller-safe reason, so the
seller sees why, instead of an automation silently doing nothing.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError as SchemaError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.customer_segments import Segment
from app.automation.facts import Facts, evaluate
from app.automation.models import AutomationExecution, AutomationRule, AutomationWorkflowVersion
from app.automation.schemas import (
    ACTION_NEEDS,
    CONFIGS,
    FIELDS,
    SUBJECT_PROVIDES,
    TRIGGER_SUBJECTS,
    WAIT_EVENTS,
    ActionStep,
    BranchStep,
    RuleInput,
    WaitStep,
    WorkflowDefinition,
    compile_plan,
    dump,
    legacy_definition,
    structural_problems,
    walk,
)
from app.automation.triggers import ALERT_TRIGGERS, read_event
from app.common.outbox import OutboxEvent, OutboxTopic
from app.consignments.models import ConsignmentStatus
from app.core.clock import utc_now
from app.core.errors import ConflictError, ValidationError
from app.couriers.models import CourierAccount
from app.customers.crm_models import CustomerTag
from app.customers.models import CustomerFlag
from app.messaging import service as messaging
from app.messaging.models import Channel
from app.orders.models import OrderChannel, OrderStatus
from app.tenants.models import TenantUser
from app.tenants.roles import Permission, has_permission

#: How deep a chain of workflow-caused runs may go.
MAX_DEPTH = 3
#: Runs of one workflow for one subject in 24 hours.
REPEAT_LIMIT = 5
#: Enabled workflows a shop can have.
MAX_WORKFLOWS = 50

COURIERS = ("steadfast", "pathao", "redx", "manual")
STORE_PROVIDERS = ("SHOPIFY", "WOOCOMMERCE", "CUSTOM_WEBSITE", "API", "NONE")

#: Kept for V2 callers and tests: >0 while a run's step is executing.
automation_depth: ContextVar[int] = ContextVar("automation_depth", default=0)


@dataclass(frozen=True)
class Cause:
    execution_id: uuid.UUID
    rule_id: uuid.UUID
    depth: int
    ancestry: tuple[str, ...] = field(default_factory=tuple)


#: The run whose step is executing, so the events it causes know their parent.
automation_cause: ContextVar[Cause | None] = ContextVar("automation_cause", default=None)

#: Topics that can start or resume a workflow. Anything else is ignored cheaply.
TOPICS = frozenset(
    {
        OutboxTopic.ORDER_CREATED,
        OutboxTopic.ORDER_STATUS_CHANGED,
        OutboxTopic.ORDER_BOOKED,
        OutboxTopic.CONSIGNMENT_STATUS_CHANGED,
        OutboxTopic.COD_SETTLED,
        OutboxTopic.ALERT_RAISED,
        OutboxTopic.INVENTORY_CHANGED,
        OutboxTopic.INTEGRATION_ISSUE_OPENED,
        OutboxTopic.SEGMENT_ENTERED,
        OutboxTopic.CUSTOMER_REPLIED,
        OutboxTopic.FOLLOWUP_COMPLETED,
        OutboxTopic.PURCHASE_ORDER_ORDERED,
        OutboxTopic.PURCHASE_ORDER_RECEIVED,
        OutboxTopic.STOCK_TRANSFER_COMPLETED,
        OutboxTopic.STOCKOUT_PREDICTED,
    }
)

# ---------------------------------------------------------------- values ---

VALUES: dict[str, frozenset[str]] = {
    "status": frozenset(str(v) for v in OrderStatus),
    "channel": frozenset(str(v) for v in OrderChannel),
    "payment_method": frozenset({"COD", "PREPAID"}),
    "courier": frozenset(COURIERS),
    "consignment_status": frozenset({*(str(v) for v in ConsignmentStatus), "NOT_BOOKED"}),
    "external_source": frozenset(STORE_PROVIDERS),
    "customer_segment": frozenset(str(v) for v in Segment),
    "entered_segment": frozenset(str(v) for v in Segment),
    "customer_flag": frozenset(str(v) for v in CustomerFlag),
    "integration_provider": frozenset({"SHOPIFY", "WOOCOMMERCE", "CUSTOM_WEBSITE"}),
    "alert_kind": frozenset(ALERT_TRIGGERS),
}


def _fail(message: str, problems: list[str]) -> ValidationError:
    return ValidationError(message, details={"problems": sorted(set(problems))})


async def _condition_problems(
    db: AsyncSession, group: dict[str, Any], provides: frozenset[str]
) -> list[str]:
    problems: list[str] = []
    conditions = [*(group.get("conditions") or [])]
    for nested in group.get("groups") or []:
        conditions += nested.get("conditions") or []
    for condition in conditions:
        needs, kind, _ = FIELDS[condition["field"]]
        if needs is not None and needs not in provides:
            problems.append(f"CONDITION_NOT_AVAILABLE:{condition['field']}")
            continue
        values = condition.get("value")
        allowed = VALUES.get(condition["field"])
        listed = values if isinstance(values, list) else [values]
        if allowed is not None and values is not None and any(v not in allowed for v in listed):
            problems.append(f"INVALID_VALUE:{condition['field']}")
        if kind == "tag" and values is not None:
            try:
                tag = await db.scalar(
                    sa.select(CustomerTag).where(CustomerTag.id == uuid.UUID(str(values)))
                )
            except ValueError:
                tag = None
            if tag is None or tag.archived:
                problems.append("TAG_NOT_FOUND")
    return problems


async def _action_config(
    db: AsyncSession,
    step: ActionStep,
    *,
    publisher: TenantUser | None,
    enabling: bool,
) -> tuple[dict[str, Any], list[str]]:
    try:
        config = CONFIGS[step.action].model_validate(step.config).model_dump(mode="json")
    except SchemaError:
        return step.config, [f"INVALID_CONFIG:{step.id}"]
    problems: list[str] = []
    if step.action in {"ADD_TAG", "REMOVE_TAG"}:
        tag = await db.scalar(
            sa.select(CustomerTag).where(CustomerTag.id == uuid.UUID(config["tag_id"]))
        )
        if tag is None or tag.archived:
            problems.append("TAG_NOT_FOUND")
    if step.action == "CREATE_FOLLOWUP" and config.get("assignee_id"):
        member = await db.scalar(
            sa.select(TenantUser).where(
                TenantUser.user_id == uuid.UUID(config["assignee_id"]),
                TenantUser.is_active.is_(True),
            )
        )
        if member is None or not has_permission(member.role, Permission.ORDER_WRITE):
            problems.append("ASSIGNEE_NOT_OPERATIONAL")
    if step.action == "SEND_TEMPLATE":
        # Order events only ever send order updates: marketing has its own
        # consent and goes through campaigns, never through a workflow.
        found = next(
            (
                t
                for t in await messaging.templates(db, purpose="TRANSACTIONAL")
                if t["key"] == config["template_key"]
            ),
            None,
        )
        if found is None or found["channel"] != config["channel"]:
            problems.append("TEMPLATE_NOT_TRANSACTIONAL")
        elif enabling:
            channel = await db.scalar(sa.select(Channel).where(Channel.kind == config["channel"]))
            if not messaging.capability(config["channel"])[0] or not channel or not channel.enabled:
                problems.append("CHANNEL_DISABLED")
    if step.action == "CREATE_TEAM_TASK" and config.get("assignee_id"):
        member = await db.scalar(
            sa.select(TenantUser).where(
                TenantUser.user_id == uuid.UUID(config["assignee_id"]),
                TenantUser.is_active.is_(True),
            )
        )
        if member is None:
            problems.append("ASSIGNEE_NOT_ACTIVE")
    if step.action == "CREATE_DRAFT_PO" and (
        publisher is None or not has_permission(publisher.role, Permission.PROCUREMENT_MANAGE)
    ):
        problems.append("PROCUREMENT_PERMISSION_REQUIRED")
    if step.action == "BOOK_COURIER":
        if publisher is None or not has_permission(publisher.role, Permission.ORDER_BOOK):
            problems.append("BOOKING_PERMISSION_REQUIRED")
        account = await db.scalar(
            sa.select(CourierAccount).where(CourierAccount.provider == config["provider"])
        )
        if account is None or account.status != "CONNECTED":
            problems.append("COURIER_NOT_CONNECTED")
    return config, problems


async def validate_definition(
    db: AsyncSession,
    raw: dict[str, Any] | WorkflowDefinition,
    *,
    publisher: TenantUser | None,
    enabling: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Everything that must hold before a definition may run.

    Returns the normalised definition and its compiled plan. Raises a
    ``ValidationError`` whose ``details.problems`` lists seller-safe codes, or
    a ``ConflictError`` with ``blocker`` for a missing prerequisite when enabling.
    """
    try:
        definition = (
            raw if isinstance(raw, WorkflowDefinition) else WorkflowDefinition.model_validate(raw)
        )
    except SchemaError as exc:
        raise ValidationError(
            "Invalid workflow", details={"fields": [list(e["loc"]) for e in exc.errors()][:20]}
        ) from exc
    problems = structural_problems(definition)
    provides = SUBJECT_PROVIDES[TRIGGER_SUBJECTS[definition.trigger]]
    problems += await _condition_problems(db, definition.conditions.model_dump(), provides)
    blockers: list[str] = []
    for step, _ in walk(definition.steps):
        if isinstance(step, ActionStep):
            needs = ACTION_NEEDS[step.action]
            if needs is not None and needs not in provides:
                problems.append(f"ACTION_NOT_AVAILABLE:{step.action}")
                continue
            config, found = await _action_config(db, step, publisher=publisher, enabling=enabling)
            step.config = config
            for code in found:
                (
                    blockers if code in {"CHANNEL_DISABLED", "COURIER_NOT_CONNECTED"} else problems
                ).append(code)
        elif isinstance(step, BranchStep):
            problems += await _condition_problems(db, step.conditions.model_dump(), provides)
        elif isinstance(step, WaitStep) and WAIT_EVENTS[step.event] not in provides:
            problems.append(f"WAIT_NOT_AVAILABLE:{step.event}")
    if problems:
        raise _fail("This workflow cannot be published yet", problems)
    if blockers:
        raise ConflictError(
            "Connect what this workflow needs first",
            details={"blocker": blockers[0], "problems": sorted(set(blockers))},
        )
    return dump(definition), compile_plan(definition)


async def publisher_of(db: AsyncSession, user_id: uuid.UUID) -> TenantUser | None:
    return await db.scalar(
        sa.select(TenantUser).where(TenantUser.user_id == user_id, TenantUser.is_active.is_(True))
    )


def can_manage(member: TenantUser | None) -> bool:
    return member is not None and has_permission(member.role, Permission.AUTOMATION_MANAGE)


async def publish(
    db: AsyncSession,
    rule: AutomationRule,
    raw: dict[str, Any],
    *,
    actor: uuid.UUID,
    enable: bool,
) -> AutomationWorkflowVersion:
    """Freeze a definition as the next version; new events use it from now on.

    Runs already in progress keep the version they started with.
    """
    member = await publisher_of(db, actor)
    definition, plan = await validate_definition(db, raw, publisher=member, enabling=enable)
    number = (
        await db.scalar(
            sa.select(sa.func.max(AutomationWorkflowVersion.number)).where(
                AutomationWorkflowVersion.rule_id == rule.id
            )
        )
        or 0
    ) + 1
    version = AutomationWorkflowVersion(
        rule_id=rule.id,
        number=number,
        trigger=definition["trigger"],
        definition=definition,
        plan=plan,
        published_by=actor,
    )
    db.add(version)
    rule.draft = definition
    rule.trigger = definition["trigger"]
    rule.published_version = number
    rule.created_by = actor
    rule.enabled = enable
    rule.version += 1
    steps = definition["steps"]
    single = (
        len(steps) == 1
        and steps[0]["type"] == "action"
        and not definition["conditions"].get("groups")
    )
    if single and definition["conditions"].get("match", "all") == "all":
        rule.action, rule.config = steps[0]["action"], steps[0].get("config") or {}
        rule.conditions = definition["conditions"].get("conditions") or []
    else:
        rule.action, rule.config, rule.conditions = "WORKFLOW", {}, []
    await db.flush()
    return version


async def validate_rule(db: AsyncSession, body: RuleInput) -> dict:
    """V2 rule endpoints: the rule as a one-step workflow, validated as one."""
    for condition in body.conditions:
        allowed = {
            str(value) for value in (OrderStatus if condition.field == "status" else OrderChannel)
        }
        if condition.field != "cod_amount_paisa" and condition.value not in allowed:
            raise ValidationError("Choose a valid order status or channel")
    try:
        config = CONFIGS[body.action].model_validate(body.config).model_dump(mode="json")
    except SchemaError as exc:
        raise ValidationError(
            "Invalid action configuration", details={"fields": [e["loc"] for e in exc.errors()]}
        ) from exc
    step = ActionStep(type="action", id="s1", action=body.action, config=config)
    config, problems = await _action_config(db, step, publisher=None, enabling=body.enabled)
    problems = [p for p in problems if p != "BOOKING_PERMISSION_REQUIRED"]
    if "CHANNEL_DISABLED" in problems:
        raise ConflictError(
            "Configure the official messaging channel first",
            details={"blocker": "CHANNEL_DISABLED"},
        )
    if "TEMPLATE_NOT_TRANSACTIONAL" in problems:
        raise ValidationError("Choose a configured transactional template")
    if "TAG_NOT_FOUND" in problems:
        raise ValidationError("Choose an active tag")
    if "ASSIGNEE_NOT_OPERATIONAL" in problems:
        raise ValidationError("Choose an active operational member")
    return config


def rule_definition(body: RuleInput, config: dict[str, Any]) -> dict[str, Any]:
    return legacy_definition(
        body.trigger, [c.model_dump(mode="json") for c in body.conditions], body.action, config
    )


# ---------------------------------------------------------------- events ---


async def resume_waiters(db: AsyncSession, tenant_id: uuid.UUID, keys: list[str]) -> int:
    """Wake the runs waiting for this event. They continue on the dispatcher."""
    if not keys:
        return 0
    rows = (
        await db.scalars(
            sa.select(AutomationExecution)
            .where(
                AutomationExecution.tenant_id == tenant_id,
                AutomationExecution.wait_key.in_(keys),
                AutomationExecution.status == "WAITING",
            )
            .limit(200)
        )
    ).all()
    now = utc_now()
    for row in rows:
        row.wait_satisfied_at, row.wait_key, row.next_attempt_at = now, None, now
    if rows:
        # Written now, inside the producer's tenant scope: some producers only
        # commit after the response, when no tenant is in scope any more.
        await db.flush()
    return len(rows)


async def schedule_event(db: AsyncSession, event: OutboxEvent) -> None:
    if event.tenant_id is None or event.topic not in TOPICS:
        return
    # Most shops have no workflows: two indexed probes, then nothing to read.
    listening = await db.scalar(
        sa.select(AutomationRule.id)
        .where(AutomationRule.tenant_id == event.tenant_id, AutomationRule.enabled.is_(True))
        .limit(1)
    ) or await db.scalar(
        sa.select(AutomationExecution.id)
        .where(
            AutomationExecution.tenant_id == event.tenant_id,
            AutomationExecution.status == "WAITING",
            AutomationExecution.wait_key.is_not(None),
        )
        .limit(1)
    )
    if listening is None:
        return
    reading = await read_event(db, event)
    await resume_waiters(db, event.tenant_id, reading.wait_keys)
    if not reading.matches:
        return
    triggers = {match.trigger for match in reading.matches}
    rules = (
        await db.scalars(
            sa.select(AutomationRule)
            .where(
                AutomationRule.tenant_id == event.tenant_id,
                AutomationRule.enabled.is_(True),
                AutomationRule.trigger.in_(sorted(triggers)),
                AutomationRule.published_version.is_not(None),
            )
            .limit(MAX_WORKFLOWS)
        )
    ).all()
    if not rules:
        return
    cause = automation_cause.get()
    for rule in rules:
        for match in reading.matches:
            if match.trigger != rule.trigger:
                continue
            await _start(db, event, rule, match, cause)
    await db.flush()


async def _start(db: AsyncSession, event: OutboxEvent, rule: AutomationRule, match, cause) -> None:  # type: ignore[no-untyped-def]
    if any(
        isinstance(row, AutomationExecution) and row.rule_id == rule.id and row.event_id == event.id
        for row in db.new
    ):
        return
    # Also makes replaying an event through a repair tool safe.
    exists = await db.scalar(
        sa.select(AutomationExecution.id).where(
            AutomationExecution.tenant_id == event.tenant_id,
            AutomationExecution.rule_id == rule.id,
            AutomationExecution.event_id == event.id,
        )
    )
    if exists:
        return
    version = await db.scalar(
        sa.select(AutomationWorkflowVersion).where(
            AutomationWorkflowVersion.rule_id == rule.id,
            AutomationWorkflowVersion.number == rule.published_version,
        )
    )
    if version is None:
        return
    depth = cause.depth + 1 if cause else automation_depth.get()
    ancestry = [*cause.ancestry, str(cause.rule_id)] if cause else []
    status, reason = "QUEUED", None
    if str(rule.id) in ancestry:
        status, reason = "SKIPPED", "LOOP_DETECTED"
    elif depth > MAX_DEPTH:
        status, reason = "SKIPPED", "DEPTH_LIMIT"
    else:
        recent = await db.scalar(
            sa.select(sa.func.count(AutomationExecution.id)).where(
                AutomationExecution.tenant_id == event.tenant_id,
                AutomationExecution.rule_id == rule.id,
                AutomationExecution.subject_key == match.subject_key,
                AutomationExecution.status != "SKIPPED",
                AutomationExecution.created_at >= utc_now() - timedelta(hours=24),
            )
        )
        if int(recent or 0) >= REPEAT_LIMIT:
            status, reason = "SKIPPED", "REPEAT_LIMIT"
    now = utc_now()
    db.add(
        AutomationExecution(
            tenant_id=event.tenant_id,
            rule_id=rule.id,
            event_id=event.id,
            rule_version=version.number,
            version_id=version.id,
            trigger=match.trigger,
            snapshot={
                "subject": match.subject,
                "facts": match.facts,
                # V2 readers look for these at the top level.
                **{k: match.subject.get(k) for k in ("order_id", "customer_id")},
            },
            status=status,
            last_error=reason,
            cursor=version.plan.get("entry"),
            depth=depth,
            ancestry=ancestry,
            parent_execution_id=cause.execution_id if cause else None,
            subject_key=match.subject_key[:80],
            next_attempt_at=now,
            finished_at=now if status == "SKIPPED" else None,
        )
    )


async def entry_holds(
    db: AsyncSession, version: AutomationWorkflowVersion, execution: AutomationExecution
) -> tuple[bool, list[dict]]:
    snapshot = execution.snapshot or {}
    facts = Facts(db, snapshot.get("subject") or snapshot, snapshot.get("facts") or {})
    return await evaluate(facts, version.definition.get("conditions"))
