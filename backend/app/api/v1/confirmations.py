"""Customer confirmation of an order (V4 WP2.1).

``POST /v1/orders/{id}/confirmation`` starts one: ``REQUEST`` asks the customer
(the policy and the call queue take it from there), ``SELLER_CHAT`` records that
the customer already confirmed in the chat. Both are idempotent per order, so
the quick-order sheet's offline queue can replay them safely.

Behind ``confirmation_engine_enabled``; ``GET`` always answers and says whether
the engine is on.

WP2.3: ``GET /v1/confirmations/queue`` is the seller's call queue and
``POST /v1/orders/{id}/confirmation/{action}`` records what the seller did
(``call_now`` hands back the number to dial; see :mod:`app.confirmations.manual`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbSession, Principal, require_permission
from app.api.v1.customers import CustomerServiceDep
from app.api.v1.orders import OrderServiceDep
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.confirmations.manual import (
    ManualCallService,
    ManualOutcome,
    QueueEntry,
    QuietHours,
    queue,
    voice_available,
)
from app.confirmations.models import ConfirmationAttempt, OrderConfirmation
from app.confirmations.service import ConfirmationService, latest_for_order, open_for_order
from app.core.clock import utc_now
from app.core.errors import AppError, ErrorCode
from app.delivery_rules.service import DeliveryRulesService
from app.tenants.roles import Permission

router = APIRouter(prefix="/orders", tags=["confirmations"])
queue_router = APIRouter(prefix="/confirmations", tags=["confirmations"])

Viewer = Annotated[Principal, Depends(require_permission(Permission.ORDER_VIEW))]
Operator = Annotated[Principal, Depends(require_permission(Permission.ORDER_WRITE))]


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ``REQUEST`` = confirm with the customer; ``SELLER_CHAT`` = the customer
    #: already confirmed in the chat.
    mode: Literal["REQUEST", "SELLER_CHAT"]


class ConfirmationView(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    state: str
    decision: str | None
    reason_code: str | None
    #: What the policy read (counts, band, thresholds) for the "why" display.
    facts: dict[str, Any] | None
    method: str | None
    attempt_count: int
    next_action_at: datetime | None
    resolved_at: datetime | None
    order_outcome: str | None
    superseded_by: uuid.UUID | None
    created_at: datetime


def confirmation_view(row: OrderConfirmation) -> dict[str, Any]:
    return ConfirmationView(
        id=row.id,
        order_id=row.order_id,
        state=row.state,
        decision=row.decision,
        reason_code=row.reason_code,
        facts=row.facts,
        method=row.method,
        attempt_count=row.attempt_count,
        next_action_at=row.next_action_at,
        resolved_at=row.resolved_at,
        order_outcome=row.order_outcome,
        superseded_by=row.superseded_by,
        created_at=row.created_at,
    ).model_dump(mode="json")


async def _enabled(db: DbSession, principal: Principal) -> bool:
    return await FeatureFlagService(db).is_enabled(
        FlagKey.CONFIRMATION_ENGINE_ENABLED, tenant_id=principal.require_tenant()
    )


async def _require_enabled(db: DbSession, principal: Principal) -> None:
    if not await _enabled(db, principal):
        raise AppError("Customer confirmation is not enabled", code=ErrorCode.FEATURE_DISABLED)


def _call_view(attempt: ConfirmationAttempt | None) -> dict[str, Any] | None:
    if attempt is None:
        return None
    return {
        "attempt_no": attempt.attempt_no,
        "status": attempt.status,
        "started_at": attempt.started_at.isoformat(),
        "ended_at": attempt.ended_at.isoformat() if attempt.ended_at is not None else None,
    }


def _entry_view(entry: QueueEntry) -> dict[str, Any]:
    order = entry.order
    return {
        "confirmation": confirmation_view(entry.confirmation),
        "order": {
            "id": str(order.id),
            "order_number": order.order_number,
            "status": order.status,
            "customer_name": order.customer_name,
            "customer_phone_masked": order.customer_phone_masked,
            "cod_amount_paisa": order.cod_amount_paisa,
            "created_at": order.created_at.isoformat(),
        },
        "last_call": _call_view(entry.last_call),
        "manual_calls": entry.manual_calls,
    }


@router.get("/{order_id}/confirmation", summary="The order's customer confirmation")
async def get_confirmation(
    order_id: uuid.UUID, db: DbSession, principal: Viewer, orders: OrderServiceDep
) -> dict[str, Any]:
    order = await orders.get(order_id)  # 404 for another shop's order
    row = await open_for_order(db, order.id) or await latest_for_order(db, order.id)
    return {
        "enabled": await _enabled(db, principal),
        "confirmation": confirmation_view(row) if row is not None else None,
    }


@router.post("/{order_id}/confirmation", summary="Confirm the order with the customer")
async def start_confirmation(
    order_id: uuid.UUID,
    payload: ConfirmationRequest,
    db: DbSession,
    principal: Operator,
    orders: OrderServiceDep,
) -> dict[str, Any]:
    await _require_enabled(db, principal)
    service = ConfirmationService(db, orders=orders)
    if payload.mode == "SELLER_CHAT":
        result = await service.resolve_in_chat(order_id, actor_id=principal.user_id)
    else:
        result = await service.start(order_id, requested_by=principal.user_id)
    await db.flush()
    return {"confirmation": confirmation_view(result.confirmation), "created": result.created}


class ConfirmationAction(StrEnum):
    CALL_NOW = "call_now"
    MARK_CONFIRMED = "mark_confirmed"
    MARK_CANCELLED = "mark_cancelled"
    MARK_NO_ANSWER = "mark_no_answer"


_OUTCOMES = {
    ConfirmationAction.MARK_CONFIRMED: ManualOutcome.CONFIRMED,
    ConfirmationAction.MARK_CANCELLED: ManualOutcome.CANCELLED,
    ConfirmationAction.MARK_NO_ANSWER: ManualOutcome.NO_ANSWER,
}


class ConfirmationActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ``call_now`` only: dial during the shop's quiet hours (audited).
    override_quiet: bool = False


@router.post("/{order_id}/confirmation/{action}", summary="Record the seller's call")
async def confirmation_action(
    order_id: uuid.UUID,
    action: ConfirmationAction,
    db: DbSession,
    principal: Operator,
    orders: OrderServiceDep,
    customers: CustomerServiceDep,
    payload: ConfirmationActionRequest | None = None,
) -> dict[str, Any]:
    await _require_enabled(db, principal)
    body = payload or ConfirmationActionRequest()
    service = ManualCallService(db, orders=orders, customers=customers)
    actor = principal.user_id
    if action is ConfirmationAction.CALL_NOW:
        started = await service.call_now(
            order_id, actor_id=actor, override_quiet=body.override_quiet
        )
        await db.flush()
        return {
            "confirmation": confirmation_view(started.confirmation),
            "call": _call_view(started.attempt),
            "phone": started.phone,
            "quiet_override": started.quiet_override,
        }
    row = await service.record(order_id, _OUTCOMES[action], actor_id=actor)
    await db.flush()
    return {"confirmation": confirmation_view(row)}


@queue_router.get("/queue", summary="The seller's call queue")
async def call_queue(db: DbSession, principal: Operator) -> dict[str, Any]:
    await _require_enabled(db, principal)
    tenant_id = principal.require_tenant()
    rules = await DeliveryRulesService(db).get(tenant_id)
    voice_on = await voice_available(db, tenant_id)
    return {
        "voice_available": voice_on,
        "quiet_hours": QuietHours.from_rules(rules).as_dict(utc_now()),
        "items": [_entry_view(entry) for entry in await queue(db, voice_on=voice_on)],
    }


__all__ = ["confirmation_view", "queue_router", "router"]
