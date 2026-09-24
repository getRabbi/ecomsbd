from __future__ import annotations

import uuid
from contextvars import ContextVar
from datetime import timedelta

import sqlalchemy as sa
from pydantic import ValidationError as SchemaError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hasher, get_vault
from app.automation.models import AutomationExecution, AutomationRule, AutomationTask
from app.automation.schemas import CONFIGS, TRIGGERS, RuleInput
from app.common.outbox import OutboxEvent
from app.core.clock import utc_now
from app.core.errors import ConflictError, ValidationError
from app.customers.crm import CrmService
from app.customers.crm_models import CustomerFollowUp, CustomerTag
from app.customers.models import Customer
from app.customers.service import CustomerService
from app.messaging import service as messaging
from app.messaging.models import Channel, Conversation, Message
from app.notifications.models import Notification, NotificationCategory, NotificationKind, Severity
from app.notifications.service import NotificationService
from app.orders.models import Order, OrderChannel, OrderStatus
from app.tenants.models import TenantUser
from app.tenants.roles import Permission, has_permission

automation_depth: ContextVar[int] = ContextVar("automation_depth", default=0)


async def validate_rule(db: AsyncSession, body: RuleInput) -> dict:
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
    if body.action in {"ADD_TAG", "REMOVE_TAG"}:
        tag = await messaging.required(db, CustomerTag, uuid.UUID(config["tag_id"]))
        if tag.archived:
            raise ValidationError("Choose an active tag")
    if body.action == "CREATE_FOLLOWUP" and config["assignee_id"]:
        member = await db.scalar(
            sa.select(TenantUser).where(
                TenantUser.user_id == uuid.UUID(config["assignee_id"]),
                TenantUser.is_active.is_(True),
            )
        )
        if member is None or not has_permission(member.role, Permission.ORDER_WRITE):
            raise ValidationError("Choose an active operational member")
    if body.action == "SEND_TEMPLATE":
        # Order events only ever send order updates: marketing has its own
        # consent and goes through campaigns, never through a rule.
        found = next(
            (
                t
                for t in await messaging.templates(db, purpose="TRANSACTIONAL")
                if t["key"] == config["template_key"]
            ),
            None,
        )
        if found is None or found["channel"] != config["channel"]:
            raise ValidationError("Choose a configured transactional template")
        if body.enabled:
            channel = await db.scalar(sa.select(Channel).where(Channel.kind == config["channel"]))
            if not messaging.capability(config["channel"])[0] or not channel or not channel.enabled:
                raise ConflictError(
                    "Configure the official messaging channel first",
                    details={"blocker": "CHANNEL_DISABLED"},
                )
    return config


def matches(conditions: list[dict], facts: dict) -> bool:
    for condition in conditions:
        actual, expected, op = facts.get(condition["field"]), condition["value"], condition["op"]
        if actual is None:
            return False
        if (op == "eq" and actual != expected) or (op == "ne" and actual == expected):
            return False
        if op in {"gte", "lte"}:
            if type(actual) is not int or type(expected) is not int:
                return False
            if (op == "gte" and actual < expected) or (op == "lte" and actual > expected):
                return False
    return True


async def schedule_event(db: AsyncSession, event: OutboxEvent) -> None:
    if automation_depth.get() > 0 or event.tenant_id is None or event.topic not in TRIGGERS:
        return
    rules = (
        await db.scalars(
            sa.select(AutomationRule)
            .where(
                AutomationRule.tenant_id == event.tenant_id,
                AutomationRule.enabled.is_(True),
                AutomationRule.trigger == event.topic,
            )
            .limit(50)
        )
    ).all()
    if not rules:
        return
    try:
        order_id = uuid.UUID(event.payload["order_id"])
    except (ValueError, KeyError, TypeError):
        return
    order = await db.scalar(
        sa.select(Order).where(
            Order.id == order_id, Order.tenant_id == event.tenant_id, Order.deleted_at.is_(None)
        )
    )
    if order is None or order.customer_id is None:
        return
    facts = {
        "status": event.payload.get("to", str(order.status)),
        "channel": str(order.channel),
        "cod_amount_paisa": order.cod_amount_paisa,
    }
    for rule in rules:
        if any(
            isinstance(row, AutomationExecution)
            and row.rule_id == rule.id
            and row.event_id == event.id
            for row in db.new
        ):
            continue
        # Also makes replaying an event through a repair tool safe.
        exists = await db.scalar(
            sa.select(AutomationExecution.id).where(
                AutomationExecution.tenant_id == event.tenant_id,
                AutomationExecution.rule_id == rule.id,
                AutomationExecution.event_id == event.id,
            )
        )
        if exists:
            continue
        matched = matches(rule.conditions, facts)
        db.add(
            AutomationExecution(
                tenant_id=event.tenant_id,
                rule_id=rule.id,
                event_id=event.id,
                rule_version=rule.version,
                snapshot={
                    "action": rule.action,
                    "config": rule.config,
                    "order_id": str(order.id),
                    "customer_id": str(order.customer_id),
                    "facts": facts,
                },
                status="PENDING" if matched else "SKIPPED",
                last_error=None if matched else "CONDITIONS_NOT_MET",
                next_attempt_at=utc_now(),
            )
        )


async def perform(db: AsyncSession, execution: AutomationExecution) -> str | None:
    snapshot = execution.snapshot
    action, config = snapshot["action"], snapshot["config"]
    order_id, customer_id = uuid.UUID(snapshot["order_id"]), uuid.UUID(snapshot["customer_id"])
    order = await messaging.required(db, Order, order_id)
    await messaging.required(db, Customer, customer_id)
    if order.customer_id != customer_id:
        raise ConflictError("Order customer changed")
    customers = CustomerService(db, hasher=get_hasher(), vault=get_vault())
    crm = CrmService(db, customers)
    row: Message | CustomerFollowUp | Notification | AutomationTask | None
    if action == "SEND_TEMPLATE":
        conversation = await db.scalar(
            sa.select(Conversation).where(
                Conversation.customer_id == customer_id, Conversation.channel == config["channel"]
            )
        )
        if conversation is None:
            raise ConflictError("No customer consent", details={"blocker": "CONSENT_REQUIRED"})
        row = await messaging.queue_message(
            db,
            conversation_id=conversation.id,
            order_id=order_id,
            template_key=config["template_key"],
            locale=config["locale"],
            idempotency_key=f"automation:{execution.id}",
        )
        return str(row.id)
    if action == "CREATE_FOLLOWUP":
        row = await crm.create_followup(
            customer_id,
            config["text"],
            execution.created_at + timedelta(hours=config["due_hours"]),
            uuid.UUID(config["assignee_id"]) if config["assignee_id"] else None,
        )
        return str(row.id)
    if action in {"ADD_TAG", "REMOVE_TAG"}:
        await crm.bulk_tag([customer_id], uuid.UUID(config["tag_id"]), action == "REMOVE_TAG")
        return config["tag_id"]
    if action == "SELLER_NOTIFICATION":
        row = await NotificationService(db).notify(
            kind=NotificationKind.AUTOMATION,
            severity=Severity.ACTION,
            title=config["title_en"],
            body=config["text_en"],
            dedupe_key=f"automation:{execution.id}",
            entity_type="order",
            entity_id=order_id,
            category=NotificationCategory.CRM,
            audience=Permission.ORDER_WRITE,
            payload={"params": config},
        )
        return str(row.id) if row else None
    if action == "CREATE_TASK":
        row = AutomationTask(
            execution_id=execution.id,
            order_id=order_id,
            customer_id=customer_id,
            text_en=config["text_en"],
            text_bn=config["text_bn"],
            due_at=execution.created_at + timedelta(hours=config["due_hours"]),
        )
        db.add(row)
        await db.flush()
        return str(row.id)
    # No eval, arbitrary HTTP, ledger, status-change or reconciliation action.
    raise ValidationError("Unsupported automation action")
