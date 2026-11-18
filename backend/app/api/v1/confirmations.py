"""Customer confirmation of an order (V4 WP2.1).

``POST /v1/orders/{id}/confirmation`` starts one: ``REQUEST`` asks the customer
(the policy and the call queue take it from there), ``SELLER_CHAT`` records that
the customer already confirmed in the chat. Both are idempotent per order, so
the quick-order sheet's offline queue can replay them safely.

Behind ``confirmation_engine_enabled``; ``GET`` always answers and says whether
the engine is on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbSession, Principal, require_permission
from app.api.v1.orders import OrderServiceDep
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.confirmations.models import OrderConfirmation
from app.confirmations.service import ConfirmationService, latest_for_order, open_for_order
from app.core.errors import AppError, ErrorCode
from app.tenants.roles import Permission

router = APIRouter(prefix="/orders", tags=["confirmations"])

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
    if not await _enabled(db, principal):
        raise AppError("Customer confirmation is not enabled", code=ErrorCode.FEATURE_DISABLED)
    service = ConfirmationService(db, orders=orders)
    if payload.mode == "SELLER_CHAT":
        result = await service.resolve_in_chat(order_id, actor_id=principal.user_id)
    else:
        result = await service.request(order_id, requested_by=principal.user_id)
    await db.flush()
    return {"confirmation": confirmation_view(result.confirmation), "created": result.created}


__all__ = ["confirmation_view", "router"]
