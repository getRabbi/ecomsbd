"""Workflow actions: each one is a call into an existing service.

The list is closed. There is no ledger entry, payout or reconciliation
action, no stock write, no order-status change, no arbitrary HTTP and no
credential read here, by design (V3.4 section 4 and 16):

* messages go through the V3.3 transactional queue, which re-checks consent,
  opt-out, channel and template, with a step-scoped idempotency key;
* tags and follow-ups are the CRM's own;
* a courier booking goes through the V2 booking service and its
  BOOKING_UNKNOWN rules (see ``app.automation.jobs``);
* store pushes and sync retries are the V3.1/V3.2 integration queue, keyed by
  the same operation key the automatic sync uses.

Every performer returns ``(status, result_id, outcome)`` and raises the
application errors of the service it called when it cannot go ahead.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hasher, get_vault
from app.automation.models import AutomationExecution, AutomationTask
from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.customers.crm import CrmService
from app.customers.models import Customer
from app.customers.service import CustomerService
from app.integrations.models import IntegrationEvent
from app.messaging import service as messaging
from app.messaging.models import Conversation, Message
from app.notifications.models import NotificationCategory, NotificationKind, Severity
from app.notifications.service import NotificationService
from app.orders.models import Order, OrderStatus
from app.tenants.roles import Permission

__all__ = ["MESSAGE_LIMIT_PER_DAY", "Outcome", "perform"]

Outcome = tuple[str, str | None, str | None]

#: Automated order updates one customer can receive in 24 hours, across every
#: workflow. Order updates, not marketing, but still not a firehose.
MESSAGE_LIMIT_PER_DAY = 4
#: Automated retries of one failed integration problem.
SYNC_RETRY_LIMIT = 3

AUDIENCES = {
    "OPERATIONS": (Permission.ORDER_WRITE, NotificationCategory.CRM),
    "FINANCE": (Permission.MONEY_VIEW, NotificationCategory.MONEY),
    "INVENTORY": (Permission.INVENTORY_ADJUST, NotificationCategory.INVENTORY),
}


def _id(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def key_for(execution: AutomationExecution, step_id: str) -> str:
    """The idempotency key of one step of one run.

    V2 executions (one step, ``s1``) keep the key they always had.
    """
    return (
        f"automation:{execution.id}"
        if execution.version_id is None and step_id == "s1"
        else f"automation:{execution.id}:{step_id}"
    )


async def _order(db: AsyncSession, subject: dict[str, Any]) -> Order:
    order_id = _id(subject.get("order_id"))
    if order_id is None:
        raise ValidationError("This step needs an order", details={"blocker": "NO_ORDER"})
    order = await db.scalar(
        sa.select(Order).where(Order.id == order_id, Order.deleted_at.is_(None))
    )
    if order is None:
        raise NotFoundError("Order not found", details={"blocker": "ORDER_NOT_FOUND"})
    return order


async def _customer(db: AsyncSession, subject: dict[str, Any]) -> uuid.UUID:
    customer_id = _id(subject.get("customer_id"))
    if customer_id is None:
        raise ValidationError("This step needs a customer", details={"blocker": "NO_CUSTOMER"})
    found = await db.scalar(
        sa.select(Customer.id).where(Customer.id == customer_id, Customer.deleted_at.is_(None))
    )
    if found is None:
        raise NotFoundError("Customer not found", details={"blocker": "CUSTOMER_NOT_FOUND"})
    return customer_id


def _crm(db: AsyncSession) -> CrmService:
    return CrmService(db, CustomerService(db, hasher=get_hasher(), vault=get_vault()))


async def perform(
    db: AsyncSession,
    execution: AutomationExecution,
    step: dict[str, Any],
) -> Outcome:
    action, config, step_id = step["action"], step.get("config") or {}, step["id"]
    subject = execution.snapshot.get("subject") or execution.snapshot
    key = key_for(execution, step_id)

    if action == "SEND_TEMPLATE":
        order = await _order(db, subject)
        customer_id = await _customer(db, subject)
        if order.customer_id != customer_id:
            raise ConflictError("Order customer changed", details={"blocker": "CUSTOMER_CHANGED"})
        existing = await db.scalar(sa.select(Message.id).where(Message.idempotency_key == key))
        if existing is not None:
            return "SUCCEEDED", str(existing), None
        conversation = await db.scalar(
            sa.select(Conversation).where(
                Conversation.customer_id == customer_id, Conversation.channel == config["channel"]
            )
        )
        if conversation is None:
            return "SKIPPED", None, "CONSENT_REQUIRED"
        sent_today = await db.scalar(
            sa.select(sa.func.count(Message.id))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.customer_id == customer_id,
                Message.idempotency_key.like("automation:%"),
                Message.created_at >= utc_now() - timedelta(hours=24),
            )
        )
        if int(sent_today or 0) >= MESSAGE_LIMIT_PER_DAY:
            return "SKIPPED", None, "FREQUENCY_LIMIT"
        try:
            async with db.begin_nested():
                row = await messaging.queue_message(
                    db,
                    conversation_id=conversation.id,
                    order_id=order.id,
                    template_key=config["template_key"],
                    locale=config.get("locale", "bn"),
                    idempotency_key=key,
                )
        except ConflictError as exc:
            blocker = (exc.details or {}).get("blocker")
            if blocker:
                # Consent, opt-out, a disabled channel or an unusable template:
                # the message must not go, and that is not a workflow failure.
                return "SKIPPED", None, str(blocker)[:80]
            raise
        return "SUCCEEDED", str(row.id), None

    if action == "CREATE_FOLLOWUP":
        customer_id = await _customer(db, subject)
        followup = await _crm(db).create_followup(
            customer_id,
            config["text"],
            execution.created_at + timedelta(hours=config.get("due_hours", 24)),
            _id(config.get("assignee_id")),
        )
        return "SUCCEEDED", str(followup.id), None

    if action in {"ADD_TAG", "REMOVE_TAG"}:
        customer_id = await _customer(db, subject)
        await _crm(db).bulk_tag([customer_id], uuid.UUID(config["tag_id"]), action == "REMOVE_TAG")
        return "SUCCEEDED", config["tag_id"], None

    if action == "SELLER_NOTIFICATION":
        permission, category = AUDIENCES[config.get("audience", "OPERATIONS")]
        order_id = _id(subject.get("order_id"))
        notice = await NotificationService(db).notify(
            kind=NotificationKind.AUTOMATION,
            severity=Severity.ACTION,
            title=config["title_en"],
            body=config["text_en"],
            dedupe_key=key,
            entity_type="order" if order_id else None,
            entity_id=order_id,
            category=category,
            audience=permission,
            payload={"params": config},
        )
        return "SUCCEEDED", str(notice.id) if notice else None, None

    if action == "CREATE_TASK":
        order = await _order(db, subject)
        customer_id = await _customer(db, subject)
        task = AutomationTask(
            execution_id=execution.id,
            order_id=order.id,
            customer_id=customer_id,
            text_en=config["text_en"],
            text_bn=config["text_bn"],
            due_at=execution.created_at + timedelta(hours=config.get("due_hours", 24)),
        )
        db.add(task)
        await db.flush()
        return "SUCCEEDED", str(task.id), None

    if action == "PUSH_STORE_STATUS":
        from app.integrations import outbound

        order = await _order(db, subject)
        parcel = await db.scalar(
            sa.select(Consignment)
            .where(
                Consignment.order_id == order.id,
                Consignment.status.not_in(
                    [str(ConsignmentStatus.CANCELLED), str(ConsignmentStatus.FAILED)]
                ),
            )
            .order_by(Consignment.created_at.desc())
            .limit(1)
        )
        # The push the current state calls for, under the automatic sync's own
        # operation key: whichever asks second finds the first one's row.
        if parcel is not None and parcel.status in {"DELIVERED", "PARTIAL_DELIVERED"}:
            operation, op_key = "PUSH_DELIVERED", f"PUSH_DELIVERED:{parcel.id}"
            extra: dict[str, Any] = {"consignment_id": str(parcel.id)}
        elif parcel is not None and parcel.tracking_code:
            operation, op_key = "PUSH_FULFILLMENT", f"PUSH_FULFILLMENT:{parcel.id}"
            extra = {"consignment_id": str(parcel.id)}
        elif str(order.status) == OrderStatus.CANCELLED:
            operation, op_key, extra = "PUSH_CANCEL", f"PUSH_CANCEL:{order.id}", {}
        elif str(order.status) == OrderStatus.CONFIRMED:
            operation, op_key, extra = "PUSH_CONFIRM", f"PUSH_CONFIRM:{order.id}", {}
        else:
            return "SKIPPED", None, "NOTHING_TO_PUSH"
        queued = await outbound.queue_for_order(
            db, order.tenant_id, order.id, operation, key=op_key, payload=extra
        )
        if not queued:
            existing = await db.scalar(
                sa.select(IntegrationEvent.id).where(
                    IntegrationEvent.kind == "OUTBOUND", IntegrationEvent.delivery_id == op_key
                )
            )
            if existing is None:
                return "SKIPPED", None, "NO_CONNECTED_STORE"
            return "SUCCEEDED", str(existing), "ALREADY_QUEUED"
        return "SUCCEEDED", op_key[:80], None

    if action == "RETRY_INTEGRATION_SYNC":
        from app.integrations import service as integrations

        event_id = _id(subject.get("integration_event_id"))
        problem = (
            await db.scalar(
                sa.select(IntegrationEvent).where(IntegrationEvent.id == event_id).with_for_update()
            )
            if event_id
            else None
        )
        if problem is None:
            raise NotFoundError("Problem not found", details={"blocker": "PROBLEM_NOT_FOUND"})
        if problem.status != "FAILED":
            return "SUCCEEDED", str(problem.id), "ALREADY_RESOLVED"
        if problem.attempts >= SYNC_RETRY_LIMIT:
            return "SKIPPED", None, "RETRY_LIMIT_REACHED"
        await integrations.retry_event(db, problem)
        return "SUCCEEDED", str(problem.id), None

    if action == "SET_ORDER_LABEL":
        order = await _order(db, subject)
        meta = dict(order.metadata_json or {})
        labels = [str(x) for x in meta.get("automation_labels") or []]
        if config["label"] not in labels:
            if len(labels) >= 10:
                return "SKIPPED", None, "LABEL_LIMIT"
            meta["automation_labels"] = [*labels, config["label"]]
            order.metadata_json = meta
            await db.flush()
        return "SUCCEEDED", str(order.id), None

    if action == "TRIGGER_WEBHOOK":
        from app.public_api.models import WebhookDelivery, WebhookEndpoint

        endpoints = (
            await db.scalars(
                sa.select(WebhookEndpoint).where(WebhookEndpoint.enabled.is_(True)).limit(20)
            )
        ).all()
        event_id = uuid.uuid5(execution.id, step_id)
        queued = 0
        for endpoint in endpoints:
            if "automation.workflow" not in (endpoint.topics or []):
                continue
            exists = await db.scalar(
                sa.select(WebhookDelivery.id).where(
                    WebhookDelivery.endpoint_id == endpoint.id,
                    WebhookDelivery.event_id == event_id,
                )
            )
            if exists:
                queued += 1
                continue
            # Identifiers only: no customer data, no money, no credentials.
            data = {
                "workflow_id": str(execution.rule_id),
                "execution_id": str(execution.id),
                "label": config["label"],
                **{
                    k: subject[k]
                    for k in ("order_id", "product_id", "variant_id")
                    if subject.get(k)
                },
            }
            db.add(
                WebhookDelivery(
                    tenant_id=execution.tenant_id,
                    endpoint_id=endpoint.id,
                    event_id=event_id,
                    payload={
                        "id": str(event_id),
                        "type": "automation.workflow",
                        "version": "1",
                        "created_at": utc_now().isoformat(),
                        "shop_id": str(execution.tenant_id),
                        "data": data,
                    },
                    next_attempt_at=utc_now(),
                )
            )
            queued += 1
        await db.flush()
        if not queued:
            return "SKIPPED", None, "NO_WEBHOOK_ENDPOINT"
        return "SUCCEEDED", str(event_id), None

    # No eval, arbitrary HTTP, ledger, stock, status or reconciliation action.
    raise ValidationError("Unsupported automation action")
