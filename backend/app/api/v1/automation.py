import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import DbSession, Principal, require_permission
from app.automation.models import (
    AutomationAttempt,
    AutomationExecution,
    AutomationRule,
    AutomationTask,
)
from app.automation.schemas import ACTIONS, TRIGGERS, RuleInput
from app.automation.service import validate_rule
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.errors import ConflictError
from app.customers.crm_models import CustomerTag
from app.messaging.service import required, templates
from app.orders.models import OrderChannel, OrderStatus
from app.tenants.roles import Permission

router = APIRouter(prefix="/automation", tags=["automation rules"])
Reader = Annotated[Principal, Depends(require_permission(Permission.CUSTOMER_VIEW))]
Manager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]
Operator = Annotated[Principal, Depends(require_permission(Permission.ORDER_WRITE))]


class ToggleInput(BaseModel):
    enabled: bool


class TaskInput(BaseModel):
    completed: bool


def rule_view(row: AutomationRule) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in ("id", "name", "trigger", "conditions", "action", "config", "enabled", "version")
    }


@router.get("/catalog")
async def catalog(db: DbSession, _: Reader) -> dict[str, Any]:
    tags = (
        await db.scalars(
            sa.select(CustomerTag)
            .where(CustomerTag.archived.is_(False))
            .order_by(CustomerTag.name)
            .limit(200)
        )
    ).all()
    return {
        "triggers": TRIGGERS,
        "actions": ACTIONS,
        "condition_fields": ["status", "channel", "cod_amount_paisa"],
        "statuses": [str(value) for value in OrderStatus],
        "channels": [str(value) for value in OrderChannel],
        "tags": [{"id": row.id, "name": row.name} for row in tags],
        "templates": await templates(db),
    }


@router.get("/rules")
async def rules(db: DbSession, _: Reader) -> dict[str, Any]:
    return {
        "items": [
            rule_view(row)
            for row in (
                await db.scalars(
                    sa.select(AutomationRule).order_by(AutomationRule.created_at.desc())
                )
            ).all()
        ]
    }


@router.post("/rules", status_code=201)
async def create_rule(body: RuleInput, db: DbSession, actor: Manager) -> dict[str, Any]:
    await lock_shop(db)
    if (await db.scalar(sa.select(sa.func.count()).select_from(AutomationRule)) or 0) >= 50:
        raise ConflictError("At most 50 rules per shop")
    config = await validate_rule(db, body)
    row = AutomationRule(
        **{**body.model_dump(mode="json"), "config": config}, created_by=actor.user_id
    )
    db.add(row)
    await db.flush()
    return rule_view(row)


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: uuid.UUID, body: RuleInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    await lock_shop(db)
    row = await required(db, AutomationRule, rule_id)
    config = await validate_rule(db, body)
    for key, value in {**body.model_dump(mode="json"), "config": config}.items():
        setattr(row, key, value)
    row.version += 1
    row.created_by = actor.user_id
    await db.flush()
    return rule_view(row)


@router.patch("/rules/{rule_id}")
async def toggle_rule(
    rule_id: uuid.UUID, body: ToggleInput, db: DbSession, _: Manager
) -> dict[str, Any]:
    await lock_shop(db)
    row = await required(db, AutomationRule, rule_id)
    await validate_rule(
        db,
        RuleInput(
            **{
                **{
                    key: getattr(row, key)
                    for key in ("name", "trigger", "conditions", "action", "config")
                },
                "enabled": body.enabled,
            }
        ),
    )
    if row.enabled != body.enabled:
        row.enabled = body.enabled
        row.version += 1
    await db.flush()
    return rule_view(row)


@router.get("/executions")
async def history(
    db: DbSession, _: Reader, rule_id: uuid.UUID | None = None, offset: int = Query(0, ge=0)
) -> dict[str, Any]:
    query = sa.select(AutomationExecution)
    if rule_id:
        query = query.where(AutomationExecution.rule_id == rule_id)
    rows = (
        await db.scalars(
            query.order_by(AutomationExecution.created_at.desc()).offset(offset).limit(50)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "rule_id": row.rule_id,
                "order_id": row.snapshot["order_id"],
                "action": row.snapshot["action"],
                "status": row.status,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "result_id": row.result_id,
                "created_at": row.created_at,
            }
            for row in rows
        ]
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
            {"id": row.id, "status": row.status, "error": row.error, "created_at": row.created_at}
            for row in rows
        ]
    }


@router.post("/executions/{execution_id}/retry")
async def retry(execution_id: uuid.UUID, db: DbSession, _: Manager) -> dict[str, Any]:
    await lock_shop(db)
    row = await required(db, AutomationExecution, execution_id)
    if row.status != "FAILED":
        raise ConflictError("Only exhausted transient failures can be retried")
    rule = await required(db, AutomationRule, row.rule_id)
    if not rule.enabled or rule.version != row.rule_version:
        raise ConflictError("Rule is disabled or has changed")
    row.status, row.attempts, row.next_attempt_at = "PENDING", 0, utc_now()
    await db.flush()
    return {"id": row.id, "status": row.status}


@router.get("/tasks")
async def tasks(db: DbSession, _: Reader, offset: int = Query(0, ge=0)) -> dict[str, Any]:
    rows = (
        await db.scalars(
            sa.select(AutomationTask)
            .order_by(AutomationTask.completed_at, AutomationTask.due_at)
            .offset(offset)
            .limit(50)
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
