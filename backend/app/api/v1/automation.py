"""Automation: V2 rules and V3.4 workflows, runs, recipes and the retry centre.

Who can do what (enforced here, never only in a client):

* reading workflows, runs and metrics: anyone who can see orders;
* building, editing, publishing, switching on/off, test runs and recipes:
  ``automation.manage`` (owner and manager);
* retrying or cancelling a run: ``order.write`` (operational staff). A retried
  step still runs with the publisher's authority, and the engine re-checks that
  authority before every step.
"""

import uuid
from datetime import timedelta
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import DbSession, Principal, require_permission
from app.automation import recipes as workflow_recipes
from app.automation.facts import Facts, evaluate
from app.automation.models import (
    AutomationAttempt,
    AutomationExecution,
    AutomationRule,
    AutomationStepRun,
    AutomationTask,
    AutomationWorkflowVersion,
)
from app.automation.schemas import (
    ACTION_NEEDS,
    ACTIONS,
    FIELDS,
    SUBJECT_PROVIDES,
    TRIGGER_SUBJECTS,
    TRIGGERS,
    WAIT_EVENTS,
    WORKFLOW_ACTIONS,
    PreviewInput,
    PublishInput,
    RecipeInput,
    RuleInput,
    TestRunInput,
    WorkflowDefinition,
    WorkflowInput,
    compile_plan,
    structural_problems,
)
from app.automation.service import (
    MAX_WORKFLOWS,
    VALUES,
    publish,
    rule_definition,
    validate_definition,
    validate_rule,
)
from app.common.audit import AuditAction, record_audit
from app.common.operation_lock import lock_shop
from app.consignments.models import Consignment
from app.core.clock import ensure_utc, utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.couriers.models import CourierAccount
from app.customers.crm import CrmService
from app.customers.crm_models import CustomerTag
from app.customers.models import Customer
from app.customers.service import CustomerService
from app.messaging.models import Conversation
from app.messaging.service import required, templates
from app.orders.models import Order, OrderChannel, OrderStatus
from app.products.models import Product
from app.tenants.roles import Permission

router = APIRouter(prefix="/automation", tags=["automation"])
Reader = Annotated[Principal, Depends(require_permission(Permission.ORDER_VIEW))]
Manager = Annotated[Principal, Depends(require_permission(Permission.AUTOMATION_MANAGE))]
Operator = Annotated[Principal, Depends(require_permission(Permission.ORDER_WRITE))]

PAGE = 50
ACTIVE = ("QUEUED", "WAITING", "RUNNING")


class ToggleInput(BaseModel):
    enabled: bool


class TaskInput(BaseModel):
    completed: bool


# ------------------------------------------------------------------- views ---


def rule_view(row: AutomationRule) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in ("id", "name", "trigger", "conditions", "action", "config", "enabled", "version")
    }


def workflow_view(row: AutomationRule, published: dict | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "trigger": row.trigger,
        "enabled": row.enabled,
        "published_version": row.published_version,
        "recipe_key": row.recipe_key,
        "draft": row.draft,
        "has_unpublished_changes": row.published_version is None
        or (published is not None and published != row.draft),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def execution_view(row: AutomationExecution, names: dict[uuid.UUID, str] | None = None) -> dict:
    snapshot = row.snapshot or {}
    subject = snapshot.get("subject") or {}
    started, finished = row.started_at, row.finished_at
    return {
        "id": row.id,
        "rule_id": row.rule_id,
        "workflow_name": (names or {}).get(row.rule_id),
        "version": row.rule_version,
        "trigger": row.trigger or snapshot.get("trigger"),
        "event_id": row.event_id,
        "order_id": subject.get("order_id") or snapshot.get("order_id"),
        "customer_id": subject.get("customer_id") or snapshot.get("customer_id"),
        "action": snapshot.get("action"),
        "status": row.status,
        "current_step": row.cursor,
        "attempts": row.attempts,
        "retryable": bool(row.retryable) if row.status == "FAILED" else None,
        "last_error": row.last_error,
        "result_id": row.result_id,
        "depth": row.depth,
        "source": row.source,
        "next_attempt_at": row.next_attempt_at if row.status in ACTIVE else None,
        "waiting_for": row.wait_key.rsplit(":", 1)[-1] if row.wait_key else None,
        "started_at": started,
        "finished_at": finished,
        "duration_seconds": int((ensure_utc(finished) - ensure_utc(started)).total_seconds())
        if started and finished
        else None,
        "created_at": row.created_at,
    }


async def _workflow(db: DbSession, workflow_id: uuid.UUID, *, lock: bool = False) -> AutomationRule:
    query = sa.select(AutomationRule).where(AutomationRule.id == workflow_id)
    row = await db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise NotFoundError()
    return row


async def _published(db: DbSession, row: AutomationRule) -> AutomationWorkflowVersion | None:
    if row.published_version is None:
        return None
    return await db.scalar(
        sa.select(AutomationWorkflowVersion).where(
            AutomationWorkflowVersion.rule_id == row.id,
            AutomationWorkflowVersion.number == row.published_version,
        )
    )


async def _audit(db: DbSession, action: AuditAction, row_id: uuid.UUID, **context: Any) -> None:
    await record_audit(
        db, action, entity_type="automation_workflow", entity_id=row_id, context=context or None
    )


# ----------------------------------------------------------------- catalog ---


@router.get("/catalog")
async def catalog(db: DbSession, actor: Reader) -> dict[str, Any]:
    tags = (
        await db.scalars(
            sa.select(CustomerTag)
            .where(CustomerTag.archived.is_(False))
            .order_by(CustomerTag.name)
            .limit(200)
        )
    ).all()
    couriers = (
        await db.scalars(
            sa.select(CourierAccount.provider).where(CourierAccount.status == "CONNECTED")
        )
    ).all()
    return {
        # V2 rule builder fields.
        "triggers": TRIGGERS,
        "actions": ACTIONS,
        "condition_fields": ["status", "channel", "cod_amount_paisa"],
        "statuses": [str(value) for value in OrderStatus],
        "channels": [str(value) for value in OrderChannel],
        # V3.4 workflow builder.
        "workflow_triggers": [
            {"key": key, "subject": subject} for key, subject in TRIGGER_SUBJECTS.items()
        ],
        "workflow_actions": [{"key": key, "needs": ACTION_NEEDS[key]} for key in WORKFLOW_ACTIONS],
        "fields": [
            {
                "key": key,
                "needs": needs,
                "kind": kind,
                "ops": sorted(ops),
                "values": sorted(VALUES.get(key, ())),
            }
            for key, (needs, kind, ops) in FIELDS.items()
        ],
        "subjects": {key: sorted(value) for key, value in SUBJECT_PROVIDES.items()},
        "wait_events": [{"key": key, "subject": subject} for key, subject in WAIT_EVENTS.items()],
        "tags": [{"id": row.id, "name": row.name} for row in tags],
        "templates": await templates(db, purpose="TRANSACTIONAL"),
        "couriers": sorted({str(c).lower() for c in couriers}),
        "limits": {"workflows": MAX_WORKFLOWS, "steps": 30, "max_delay_minutes": 30 * 24 * 60},
        "can_manage": actor.can(Permission.AUTOMATION_MANAGE),
        "can_operate": actor.can(Permission.ORDER_WRITE),
    }


# ------------------------------------------------------------- V2 rules ---


@router.get("/rules")
async def rules(db: DbSession, _: Reader) -> dict[str, Any]:
    return {
        "items": [
            rule_view(row)
            for row in (
                await db.scalars(
                    sa.select(AutomationRule)
                    .order_by(AutomationRule.created_at.desc())
                    .limit(MAX_WORKFLOWS)
                )
            ).all()
        ]
    }


async def _count_guard(db: DbSession) -> None:
    if (
        await db.scalar(sa.select(sa.func.count()).select_from(AutomationRule)) or 0
    ) >= MAX_WORKFLOWS:
        raise ConflictError(f"At most {MAX_WORKFLOWS} workflows per shop")


@router.post("/rules", status_code=201)
async def create_rule(body: RuleInput, db: DbSession, actor: Manager) -> dict[str, Any]:
    await lock_shop(db)
    await _count_guard(db)
    config = await validate_rule(db, body)
    row = AutomationRule(
        **{**body.model_dump(mode="json"), "config": config, "enabled": False},
        created_by=actor.user_id,
    )
    db.add(row)
    await db.flush()
    await publish(db, row, rule_definition(body, config), actor=actor.user_id, enable=body.enabled)
    await _audit(db, AuditAction.AUTOMATION_WORKFLOW_CREATED, row.id, kind="RULE")
    return rule_view(row)


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: uuid.UUID, body: RuleInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    await lock_shop(db)
    row = await required(db, AutomationRule, rule_id)
    config = await validate_rule(db, body)
    row.name = body.name
    await publish(db, row, rule_definition(body, config), actor=actor.user_id, enable=body.enabled)
    await _audit(
        db, AuditAction.AUTOMATION_WORKFLOW_PUBLISHED, row.id, version=row.published_version
    )
    return rule_view(row)


@router.patch("/rules/{rule_id}")
async def toggle_rule(
    rule_id: uuid.UUID, body: ToggleInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    row = await required(db, AutomationRule, rule_id)
    await _toggle(db, row, body.enabled, actor)
    return rule_view(row)


async def _toggle(db: DbSession, row: AutomationRule, enabled: bool, actor: Principal) -> None:
    await lock_shop(db)
    if enabled:
        version = await _published(db, row)
        if version is None:
            raise ConflictError("Publish the workflow first", details={"blocker": "NOT_PUBLISHED"})
        from app.automation.service import publisher_of

        await validate_definition(
            db,
            version.definition,
            publisher=await publisher_of(db, version.published_by),
            enabling=True,
        )
    if row.enabled == enabled:
        return
    row.enabled = enabled
    row.version += 1
    if not enabled:
        # Runs in flight stop now rather than at their next step. A courier
        # booking already sent (RUNNING) is left to finish and be recorded.
        runs = (
            await db.scalars(
                sa.select(AutomationExecution)
                .where(
                    AutomationExecution.rule_id == row.id,
                    AutomationExecution.status.in_(["QUEUED", "WAITING"]),
                )
                .limit(1000)
            )
        ).all()
        for run in runs:
            run.status, run.last_error = "CANCELLED", "WORKFLOW_DISABLED"
            run.finished_at, run.wait_key = utc_now(), None
    await db.flush()
    await _audit(
        db,
        AuditAction.AUTOMATION_WORKFLOW_ENABLED
        if enabled
        else AuditAction.AUTOMATION_WORKFLOW_DISABLED,
        row.id,
    )


# ------------------------------------------------------------ workflows ---


@router.get("/workflows")
async def workflows(db: DbSession, _: Reader, offset: int = Query(0, ge=0)) -> dict[str, Any]:
    rows = (
        await db.scalars(
            sa.select(AutomationRule)
            .order_by(AutomationRule.created_at.desc())
            .offset(offset)
            .limit(PAGE)
        )
    ).all()
    ids = [row.id for row in rows]
    since = utc_now() - timedelta(days=7)
    counts: dict[uuid.UUID, dict[str, int]] = {}
    published: dict[uuid.UUID, dict] = {}
    if ids:
        # One grouped query for every card's numbers: no per-workflow queries.
        for rule_id, status, count in (
            await db.execute(
                sa.select(
                    AutomationExecution.rule_id,
                    AutomationExecution.status,
                    sa.func.count(AutomationExecution.id),
                )
                .where(
                    AutomationExecution.rule_id.in_(ids), AutomationExecution.created_at >= since
                )
                .group_by(AutomationExecution.rule_id, AutomationExecution.status)
            )
        ).all():
            counts.setdefault(rule_id, {})[status] = int(count)
        for rule_id, definition in (
            await db.execute(
                sa.select(AutomationWorkflowVersion.rule_id, AutomationWorkflowVersion.definition)
                .join(
                    AutomationRule,
                    sa.and_(
                        AutomationRule.id == AutomationWorkflowVersion.rule_id,
                        AutomationRule.published_version == AutomationWorkflowVersion.number,
                    ),
                )
                .where(AutomationRule.id.in_(ids))
            )
        ).all():
            published[rule_id] = definition
    return {
        "items": [
            {**workflow_view(row, published.get(row.id)), "runs_7d": counts.get(row.id, {})}
            for row in rows
        ],
        "next_offset": offset + PAGE if len(rows) == PAGE else None,
    }


def _check_structure(definition: WorkflowDefinition) -> None:
    problems = structural_problems(definition)
    if problems:
        raise ValidationError("This workflow is not complete", details={"problems": problems})


@router.post("/workflows", status_code=201)
async def create_workflow(body: WorkflowInput, db: DbSession, actor: Manager) -> dict[str, Any]:
    await lock_shop(db)
    await _count_guard(db)
    _check_structure(body.definition)
    draft = body.definition.model_dump(mode="json", by_alias=True, exclude_none=True)
    row = AutomationRule(
        name=body.name,
        trigger=body.definition.trigger,
        conditions=[],
        action="WORKFLOW",
        config={},
        enabled=False,
        created_by=actor.user_id,
        draft=draft,
    )
    db.add(row)
    await db.flush()
    await _audit(db, AuditAction.AUTOMATION_WORKFLOW_CREATED, row.id, kind="WORKFLOW")
    return workflow_view(row)


@router.get("/workflows/{workflow_id}")
async def workflow(workflow_id: uuid.UUID, db: DbSession, _: Reader) -> dict[str, Any]:
    row = await _workflow(db, workflow_id)
    version = await _published(db, row)
    versions = (
        await db.execute(
            sa.select(
                AutomationWorkflowVersion.number,
                AutomationWorkflowVersion.created_at,
                AutomationWorkflowVersion.published_by,
            )
            .where(AutomationWorkflowVersion.rule_id == row.id)
            .order_by(AutomationWorkflowVersion.number.desc())
            .limit(20)
        )
    ).all()
    return {
        **workflow_view(row, version.definition if version else None),
        "published": version.definition if version else None,
        "versions": [
            {"number": n, "published_at": at, "published_by": by} for n, at, by in versions
        ],
    }


@router.put("/workflows/{workflow_id}")
async def update_workflow(
    workflow_id: uuid.UUID, body: WorkflowInput, db: DbSession, _: Manager
) -> dict[str, Any]:
    """Save the draft. Nothing already running, or published, changes."""
    await lock_shop(db)
    row = await _workflow(db, workflow_id, lock=True)
    _check_structure(body.definition)
    row.name = body.name
    row.draft = body.definition.model_dump(mode="json", by_alias=True, exclude_none=True)
    await db.flush()
    await _audit(db, AuditAction.AUTOMATION_WORKFLOW_CHANGED, row.id)
    return workflow_view(row, (v.definition if (v := await _published(db, row)) else None))


@router.post("/workflows/{workflow_id}/publish")
async def publish_workflow(
    workflow_id: uuid.UUID, body: PublishInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    await lock_shop(db)
    row = await _workflow(db, workflow_id, lock=True)
    if not row.draft:
        raise ValidationError("Nothing to publish")
    was_enabled = row.enabled
    version = await publish(db, row, row.draft, actor=actor.user_id, enable=body.enable)
    await _audit(db, AuditAction.AUTOMATION_WORKFLOW_PUBLISHED, row.id, version=version.number)
    if body.enable != was_enabled:
        await _audit(
            db,
            AuditAction.AUTOMATION_WORKFLOW_ENABLED
            if body.enable
            else AuditAction.AUTOMATION_WORKFLOW_DISABLED,
            row.id,
        )
    return workflow_view(row, version.definition)


@router.patch("/workflows/{workflow_id}")
async def toggle_workflow(
    workflow_id: uuid.UUID, body: ToggleInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    row = await _workflow(db, workflow_id, lock=True)
    await _toggle(db, row, body.enabled, actor)
    return workflow_view(row, (v.definition if (v := await _published(db, row)) else None))


# ------------------------------------------------------- preview / test ---


async def _sample_subject(db: DbSession, trigger: str, body: PreviewInput) -> dict[str, Any]:
    kind = TRIGGER_SUBJECTS[trigger]
    if kind == "order":
        query = sa.select(Order).where(Order.deleted_at.is_(None))
        query = (
            query.where(Order.id == body.order_id)
            if body.order_id
            else query.order_by(Order.created_at.desc())
        )
        order = await db.scalar(query.limit(1))
        if order is None:
            raise NotFoundError("No order to test with", details={"blocker": "NO_SAMPLE"})
        return {
            "order_id": str(order.id),
            "customer_id": str(order.customer_id) if order.customer_id else None,
        }
    if kind == "customer":
        people = sa.select(Customer.id).where(Customer.deleted_at.is_(None))
        people = (
            people.where(Customer.id == body.customer_id)
            if body.customer_id
            else people.order_by(Customer.created_at.desc())
        )
        customer = await db.scalar(people.limit(1))
        if customer is None:
            raise NotFoundError("No customer to test with", details={"blocker": "NO_SAMPLE"})
        return {"customer_id": str(customer)}
    if kind == "product":
        product = await db.scalar(
            sa.select(Product.id).where(Product.low_stock_threshold.is_not(None)).limit(1)
        )
        return {"product_id": str(product) if product else None, "variant_id": None}
    return {}


async def _would(db: DbSession, step: dict, subject: dict) -> tuple[str, str | None]:
    """Read-only look at whether an action could go ahead right now."""
    action, config = step["action"], step.get("config") or {}
    order_id = subject.get("order_id")
    if action == "SEND_TEMPLATE":
        conversation = await db.scalar(
            sa.select(Conversation).where(
                Conversation.customer_id == uuid.UUID(subject["customer_id"])
                if subject.get("customer_id")
                else sa.false(),
                Conversation.channel == config.get("channel"),
            )
        )
        if conversation is None or not conversation.consent:
            return "SKIP", "CONSENT_REQUIRED"
        if conversation.undeliverable_at is not None:
            return "SKIP", "UNDELIVERABLE"
    if action == "BOOK_COURIER" and order_id:
        parcel = await db.scalar(
            sa.select(Consignment.status)
            .where(
                Consignment.order_id == uuid.UUID(order_id),
                Consignment.status.not_in(["CANCELLED", "FAILED"]),
            )
            .limit(1)
        )
        if parcel is not None:
            return "SKIP", "BOOKING_UNKNOWN" if parcel in {
                "BOOKING",
                "BOOKING_UNKNOWN",
            } else "ALREADY_BOOKED"
    return "RUN", None


@router.post("/workflows/{workflow_id}/preview")
async def preview(
    workflow_id: uuid.UUID, body: PreviewInput, db: DbSession, _: Manager
) -> dict[str, Any]:
    """What the draft would do for a real order or customer, without doing it.

    Only reads. Waits are assumed to end with their event; the timeout path is
    reported alongside.
    """
    row = await _workflow(db, workflow_id)
    if not row.draft:
        raise ValidationError("Nothing to preview")
    definition = WorkflowDefinition.model_validate(row.draft)
    plan = compile_plan(definition)
    subject = await _sample_subject(db, definition.trigger, body)
    facts = Facts(db, subject)
    holds, trace = await evaluate(facts, row.draft.get("conditions"))
    steps: list[dict[str, Any]] = []
    node = plan["entry"] if holds else None
    while node is not None and len(steps) < 60:
        step = plan["steps"][node]
        entry: dict[str, Any] = {"id": node, "type": step["type"]}
        if step["type"] == "action":
            would, reason = await _would(db, step, subject)
            entry.update(action=step["action"], would=would, reason=reason)
            node = step.get("next")
        elif step["type"] == "delay":
            entry.update(
                would="WAIT",
                mode=step.get("mode"),
                minutes=step.get("minutes"),
                time=step.get("time"),
            )
            node = step.get("next")
        elif step["type"] == "wait_event":
            entry.update(
                would="WAIT_FOR",
                event=step["event"],
                timeout_minutes=step["timeout_minutes"],
                on_timeout=step.get("on_timeout", "continue"),
            )
            node = step.get("next")
        else:
            matched, branch_trace = await evaluate(facts, step.get("conditions"))
            entry.update(would="THEN" if matched else "ELSE", conditions=branch_trace)
            node = step.get("then_next") if matched else step.get("else_next")
        steps.append(entry)
    return {
        "subject": subject,
        "entry_conditions": {"matched": holds, "conditions": trace},
        "steps": steps,
        "side_effects": False,
    }


@router.post("/workflows/{workflow_id}/run", status_code=202)
async def test_run(
    workflow_id: uuid.UUID, body: TestRunInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    """A real run of the published version, for one chosen order or customer.

    It sends, books and tags for real, so it must be explicitly confirmed.
    """
    if not body.confirm:
        raise ValidationError(
            "A test run performs real actions; confirm it first",
            details={"blocker": "CONFIRMATION_REQUIRED"},
        )
    await lock_shop(db)
    row = await _workflow(db, workflow_id)
    version = await _published(db, row)
    if version is None or not row.enabled:
        raise ConflictError(
            "Publish and switch on the workflow first", details={"blocker": "NOT_PUBLISHED"}
        )
    if TRIGGER_SUBJECTS[version.trigger] not in {"order", "customer"}:
        raise ValidationError(
            "Test runs need an order or customer trigger", details={"blocker": "NO_SAMPLE"}
        )
    if not (body.order_id or body.customer_id):
        raise ValidationError("Choose an order or customer", details={"blocker": "NO_SAMPLE"})
    subject = await _sample_subject(db, version.trigger, body)
    now = utc_now()
    run = AutomationExecution(
        rule_id=row.id,
        event_id=uuid.uuid4(),
        rule_version=version.number,
        version_id=version.id,
        trigger=version.trigger,
        snapshot={
            "subject": subject,
            "facts": {},
            **{k: subject.get(k) for k in ("order_id", "customer_id")},
        },
        status="QUEUED",
        cursor=version.plan.get("entry"),
        depth=0,
        ancestry=[],
        subject_key=f"test:{subject.get('order_id') or subject.get('customer_id')}"[:80],
        source="TEST",
        next_attempt_at=now,
    )
    db.add(run)
    await db.flush()
    await _audit(db, AuditAction.AUTOMATION_TEST_RUN, row.id, execution_id=str(run.id))
    return execution_view(run)


# ------------------------------------------------------------------ runs ---


@router.get("/executions")
async def history(
    db: DbSession,
    _: Reader,
    rule_id: uuid.UUID | None = None,
    workflow_id: uuid.UUID | None = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = sa.select(AutomationExecution)
    if rule_id or workflow_id:
        query = query.where(AutomationExecution.rule_id == (workflow_id or rule_id))
    if status:
        query = query.where(AutomationExecution.status == status.upper())
    rows = (
        await db.scalars(
            query.order_by(AutomationExecution.created_at.desc(), AutomationExecution.id)
            .offset(offset)
            .limit(PAGE)
        )
    ).all()
    names = (
        dict(
            (
                await db.execute(
                    sa.select(AutomationRule.id, AutomationRule.name).where(
                        AutomationRule.id.in_({row.rule_id for row in rows})
                    )
                )
            )
            .tuples()
            .all()
        )
        if rows
        else {}
    )
    return {
        "items": [execution_view(row, names) for row in rows],
        "next_offset": offset + PAGE if len(rows) == PAGE else None,
    }


@router.get("/executions/{execution_id}")
async def execution_detail(execution_id: uuid.UUID, db: DbSession, _: Reader) -> dict[str, Any]:
    row = await required(db, AutomationExecution, execution_id)
    rule = await db.scalar(sa.select(AutomationRule).where(AutomationRule.id == row.rule_id))
    version = (
        await db.scalar(
            sa.select(AutomationWorkflowVersion).where(
                AutomationWorkflowVersion.id == row.version_id
            )
        )
        if row.version_id
        else None
    )
    steps = (
        await db.scalars(
            sa.select(AutomationStepRun)
            .where(AutomationStepRun.execution_id == row.id)
            .order_by(AutomationStepRun.created_at, AutomationStepRun.id)
            .limit(100)
        )
    ).all()
    attempts = (
        await db.scalars(
            sa.select(AutomationAttempt)
            .where(AutomationAttempt.execution_id == row.id)
            .order_by(AutomationAttempt.created_at.desc())
            .limit(100)
        )
    ).all()
    plan_steps = (version.plan if version else {}).get("steps") or {}
    return {
        **execution_view(row, {rule.id: rule.name} if rule else None),
        "steps": [
            {
                "step_id": s.step_id,
                "kind": s.kind,
                "action": (plan_steps.get(s.step_id) or {}).get("action"),
                "event": (plan_steps.get(s.step_id) or {}).get("event"),
                "status": s.status,
                "outcome": s.outcome,
                "result_id": s.result_id,
                "started_at": s.created_at,
                "finished_at": s.finished_at,
            }
            for s in steps
        ],
        "history": [
            {
                "id": a.id,
                "step_id": a.step_id,
                "status": a.status,
                "error": a.error,
                "created_at": a.created_at,
            }
            for a in attempts
        ],
    }


@router.get("/executions/{execution_id}/attempts")
async def attempts(execution_id: uuid.UUID, db: DbSession, _: Reader) -> dict[str, Any]:
    await required(db, AutomationExecution, execution_id)
    rows = (
        await db.scalars(
            sa.select(AutomationAttempt)
            .where(AutomationAttempt.execution_id == execution_id)
            .order_by(AutomationAttempt.created_at.desc())
            .limit(50)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "step_id": row.step_id,
                "status": row.status,
                "error": row.error,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@router.post("/executions/{execution_id}/retry")
async def retry(execution_id: uuid.UUID, db: DbSession, _: Operator) -> dict[str, Any]:
    """Resume a failed run at the step that failed.

    Every step before it has a receipt and is not repeated; the failed step
    runs again under its own idempotency key.
    """
    await lock_shop(db)
    row = await db.scalar(
        sa.select(AutomationExecution)
        .where(AutomationExecution.id == execution_id)
        .with_for_update()
    )
    if row is None:
        raise NotFoundError()
    if row.status != "FAILED" or not row.retryable:
        raise ConflictError(
            "Only a retryable failure can be retried", details={"blocker": "NOT_RETRYABLE"}
        )
    rule = await required(db, AutomationRule, row.rule_id)
    if not rule.enabled:
        raise ConflictError(
            "The workflow is switched off", details={"blocker": "WORKFLOW_DISABLED"}
        )
    row.status, row.attempts, row.next_attempt_at = "QUEUED", 0, utc_now()
    row.last_error, row.finished_at, row.retryable = None, None, None
    await db.flush()
    await record_audit(
        db,
        AuditAction.AUTOMATION_EXECUTION_RETRIED,
        entity_type="automation_execution",
        entity_id=row.id,
        context={"step": row.cursor},
    )
    return {"id": row.id, "status": row.status}


@router.post("/executions/{execution_id}/cancel")
async def cancel(execution_id: uuid.UUID, db: DbSession, _: Operator) -> dict[str, Any]:
    await lock_shop(db)
    row = await db.scalar(
        sa.select(AutomationExecution)
        .where(AutomationExecution.id == execution_id)
        .with_for_update()
    )
    if row is None:
        raise NotFoundError()
    if row.status not in ("QUEUED", "WAITING"):
        raise ConflictError(
            "Only a queued or waiting run can be cancelled", details={"blocker": "NOT_CANCELLABLE"}
        )
    row.status, row.last_error = "CANCELLED", "CANCELLED_BY_SELLER"
    row.finished_at, row.wait_key = utc_now(), None
    await db.flush()
    await record_audit(
        db,
        AuditAction.AUTOMATION_EXECUTION_CANCELLED,
        entity_type="automation_execution",
        entity_id=row.id,
    )
    return {"id": row.id, "status": row.status}


@router.get("/metrics")
async def metrics(db: DbSession, _: Reader, days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """Operational facts for the period: runs, outcomes, duration, top failures."""
    since = utc_now() - timedelta(days=days)
    in_period = AutomationExecution.created_at >= since
    by_status = dict(
        (
            await db.execute(
                sa.select(AutomationExecution.status, sa.func.count(AutomationExecution.id))
                .where(in_period)
                .group_by(AutomationExecution.status)
            )
        )
        .tuples()
        .all()
    )
    durations = (
        await db.execute(
            sa.select(AutomationExecution.started_at, AutomationExecution.finished_at)
            .where(
                in_period,
                AutomationExecution.status == "SUCCEEDED",
                AutomationExecution.started_at.is_not(None),
                AutomationExecution.finished_at.is_not(None),
            )
            .order_by(AutomationExecution.created_at.desc())
            .limit(2000)
        )
    ).all()
    seconds = [
        (ensure_utc(finished) - ensure_utc(started)).total_seconds()
        for started, finished in durations
    ]
    failures = (
        await db.execute(
            sa.select(AutomationExecution.last_error, sa.func.count(AutomationExecution.id))
            .where(in_period, AutomationExecution.status == "FAILED")
            .group_by(AutomationExecution.last_error)
            .order_by(sa.func.count(AutomationExecution.id).desc())
            .limit(5)
        )
    ).all()
    waiting_now = await db.scalar(
        sa.select(sa.func.count(AutomationExecution.id)).where(
            AutomationExecution.status == "WAITING"
        )
    )
    return {
        "days": days,
        "executions": sum(int(v) for v in by_status.values()),
        "by_status": {k: int(v) for k, v in by_status.items()},
        "succeeded": int(by_status.get("SUCCEEDED", 0)),
        "failed": int(by_status.get("FAILED", 0)),
        "waiting": int(waiting_now or 0),
        "average_duration_seconds": int(sum(seconds) / len(seconds)) if seconds else None,
        "top_failures": [{"reason": reason, "count": int(count)} for reason, count in failures],
    }


# --------------------------------------------------------------- recipes ---


@router.get("/recipes")
async def recipe_list(db: DbSession, _: Reader) -> dict[str, Any]:
    return {"items": await workflow_recipes.catalog(db)}


@router.post("/recipes/{key}/install", status_code=201)
async def install_recipe(
    key: str, body: RecipeInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    """Create the recipe as an ordinary workflow, publish it and switch it on.

    If something it needs is missing (a courier, an enabled channel), it is kept
    as a draft and the blocker is returned; the seller fixes it and publishes.
    """
    recipe = workflow_recipes.RECIPES.get(key)
    if recipe is None:
        raise NotFoundError()
    if "provider" in recipe["needs"] and body.provider is None:
        raise ValidationError("Choose a courier", details={"blocker": "PROVIDER_REQUIRED"})
    await lock_shop(db)
    await _count_guard(db)
    tag_ids: dict[str, str] = {}
    needed = workflow_recipes.needs_tags(key)
    if needed:
        from app.api.deps import get_hasher, get_vault

        crm = CrmService(db, CustomerService(db, hasher=get_hasher(), vault=get_vault()))
        for name in needed:
            en, bn = workflow_recipes.TAGS[name]
            tag = await crm.create_tag(bn if body.locale == "bn" else en)
            tag_ids[name] = str(tag.id)
    definition = workflow_recipes.build(
        key,
        {
            "locale": body.locale,
            "provider": body.provider,
            "channel": body.channel,
            "tags": tag_ids,
        },
    )
    name = recipe["name"][1 if body.locale == "bn" else 0]
    parsed = WorkflowDefinition.model_validate(definition)
    row = AutomationRule(
        name=name[:100],
        trigger=parsed.trigger,
        conditions=[],
        action="WORKFLOW",
        config={},
        enabled=False,
        created_by=actor.user_id,
        draft=parsed.model_dump(mode="json", by_alias=True, exclude_none=True),
        recipe_key=key,
    )
    db.add(row)
    await db.flush()
    await _audit(db, AuditAction.AUTOMATION_WORKFLOW_CREATED, row.id, recipe=key)
    blocker: str | None = None
    try:
        async with db.begin_nested():
            await publish(db, row, row.draft or {}, actor=actor.user_id, enable=body.enable)
        await _audit(
            db, AuditAction.AUTOMATION_WORKFLOW_PUBLISHED, row.id, version=row.published_version
        )
    except ConflictError as exc:
        blocker = str((exc.details or {}).get("blocker") or exc.code)
    except ValidationError as exc:
        problems = (exc.details or {}).get("problems") or []
        blocker = str(problems[0]) if problems else exc.code
    return {**workflow_view(row), "blocker": blocker}


# ----------------------------------------------------------------- tasks ---


@router.get("/tasks")
async def tasks(db: DbSession, _: Reader, offset: int = Query(0, ge=0)) -> dict[str, Any]:
    rows = (
        await db.scalars(
            sa.select(AutomationTask)
            .order_by(AutomationTask.completed_at, AutomationTask.due_at)
            .offset(offset)
            .limit(PAGE)
        )
    ).all()
    return {
        "items": [
            {
                key: getattr(row, key)
                for key in (
                    "id",
                    "order_id",
                    "customer_id",
                    "purchase_order_id",
                    "product_id",
                    "assignee_id",
                    "text_en",
                    "text_bn",
                    "due_at",
                    "completed_at",
                )
            }
            for row in rows
        ]
    }


@router.patch("/tasks/{task_id}")
async def complete_task(
    task_id: uuid.UUID, body: TaskInput, db: DbSession, _: Operator
) -> dict[str, Any]:
    await lock_shop(db)
    row = await required(db, AutomationTask, task_id)
    row.completed_at = (row.completed_at or utc_now()) if body.completed else None
    await db.flush()
    return {"id": row.id, "completed_at": row.completed_at}
