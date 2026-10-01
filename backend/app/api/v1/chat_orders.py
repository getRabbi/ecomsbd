"""Chat-to-order: drafts read from Messenger and WhatsApp messages.

Everything here needs ``order.write``, the same as pasting a message into the
order parser: a draft carries the customer's phone and address as they wrote
them, for the person who will turn it into an order.

Confirming goes through ``OrderService.create``, the path ``POST /v1/orders``
uses, so stock, customers, money, risk and audit are the order domain's own.
The draft's id is the order's ``client_id``: confirming twice returns the same
order.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Principal, require_permission
from app.api.v1.commerce_schemas import OrderItemPayload
from app.api.v1.orders import OrderServiceDep, _to_detail, _to_duplicate_response
from app.chat_orders import service
from app.chat_orders.models import (
    COLLECTING,
    FINAL_STATES,
    NEEDS_INFO,
    READY_FOR_REVIEW,
    ChatAttention,
    ChatIdentity,
    ChatOrderDraft,
)
from app.customers.models import Customer
from app.integrations.models import IntegrationConnection
from app.tenants.roles import Permission

router = APIRouter(prefix="/chat-orders", tags=["chat-orders"])
Operator = Annotated[Principal, Depends(require_permission(Permission.ORDER_WRITE))]

_FILTERS: dict[str, tuple[str, ...]] = {
    "review": (READY_FOR_REVIEW, NEEDS_INFO),
    "ready": (READY_FOR_REVIEW,),
    "needs_info": (NEEDS_INFO,),
    "collecting": (COLLECTING,),
    "closed": FINAL_STATES,
}


class ConfirmPayload(BaseModel):
    """The order as the seller reviewed it. Same fields as ``POST /v1/orders``."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=6, max_length=24)
    items: list[OrderItemPayload] = Field(min_length=1)
    customer_name: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=1000)
    district: str | None = Field(default=None, max_length=80)
    area: str | None = Field(default=None, max_length=120)
    cod_amount_paisa: int | None = Field(default=None, ge=0)
    discount_paisa: int = Field(default=0, ge=0)
    delivery_fee_paisa: int = Field(default=0, ge=0)
    note: str | None = Field(default=None, max_length=4000)


async def _views(db: DbSession, drafts: list[ChatOrderDraft]) -> list[dict[str, Any]]:
    identity_ids = {d.identity_id for d in drafts}
    customer_ids = {d.customer_id for d in drafts if d.customer_id}
    connection_ids = {d.connection_id for d in drafts}
    identities = (
        {
            row.id: row
            for row in (
                await db.scalars(sa.select(ChatIdentity).where(ChatIdentity.id.in_(identity_ids)))
            ).all()
        }
        if identity_ids
        else {}
    )
    customers = (
        {
            row.id: row
            for row in (
                await db.scalars(sa.select(Customer).where(Customer.id.in_(customer_ids)))
            ).all()
        }
        if customer_ids
        else {}
    )
    connections = (
        {
            row.id: row
            for row in (
                await db.scalars(
                    sa.select(IntegrationConnection).where(
                        IntegrationConnection.id.in_(connection_ids)
                    )
                )
            ).all()
        }
        if connection_ids
        else {}
    )
    return [
        service.draft_view(
            draft,
            identities.get(draft.identity_id),
            customers.get(draft.customer_id) if draft.customer_id else None,
            connections.get(draft.connection_id),
        )
        for draft in drafts
    ]


@router.get("/summary")
async def summary(db: DbSession, _: Operator) -> dict[str, Any]:
    """Counts for the Inbox header, and whether any chat channel can feed it."""
    rows = await db.execute(
        sa.select(ChatOrderDraft.status, sa.func.count())
        .where(ChatOrderDraft.status.in_([READY_FOR_REVIEW, NEEDS_INFO]))
        .group_by(ChatOrderDraft.status)
    )
    counts: dict[str, int] = {str(state): int(count) for state, count in rows.all()}
    attention = await db.scalar(
        sa.select(sa.func.count()).select_from(ChatAttention).where(ChatAttention.status == "OPEN")
    )
    channels = (
        await db.execute(
            sa.select(IntegrationConnection.provider, IntegrationConnection.state).where(
                IntegrationConnection.provider.in_(["MESSENGER", "WHATSAPP"])
            )
        )
    ).all()
    return {
        "ready": counts.get(READY_FOR_REVIEW, 0),
        "needs_info": counts.get(NEEDS_INFO, 0),
        "attention": attention or 0,
        "channels": {
            provider: any(p == provider and s == "CONNECTED" for p, s in channels)
            for provider in ("MESSENGER", "WHATSAPP")
        },
    }


@router.get("")
async def list_drafts(
    db: DbSession,
    _: Operator,
    status: Literal["review", "ready", "needs_info", "collecting", "closed"] = "review",
    provider: Literal["MESSENGER", "WHATSAPP"] | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    query = sa.select(ChatOrderDraft).where(ChatOrderDraft.status.in_(_FILTERS[status]))
    if provider is not None:
        query = query.where(ChatOrderDraft.provider == provider)
    rows = list(
        (
            await db.scalars(
                query.order_by(ChatOrderDraft.last_message_at.desc(), ChatOrderDraft.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return {"items": await _views(db, rows), "next_offset": offset + len(rows)}


@router.get("/attention")
async def attention(
    db: DbSession,
    _: Operator,
    status: Literal["open", "done"] = "open",
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    rows = (
        await db.scalars(
            sa.select(ChatAttention)
            .where(ChatAttention.status == ("OPEN" if status == "open" else "DONE"))
            .order_by(ChatAttention.message_at.desc(), ChatAttention.id)
            .offset(offset)
            .limit(50)
        )
    ).all()
    customer_ids = {row.customer_id for row in rows if row.customer_id}
    customers = (
        {
            row.id: row
            for row in (
                await db.scalars(sa.select(Customer).where(Customer.id.in_(customer_ids)))
            ).all()
        }
        if customer_ids
        else {}
    )
    return {
        "items": [
            service.attention_view(row, customers.get(row.customer_id) if row.customer_id else None)
            for row in rows
        ],
        "next_offset": offset + len(rows),
    }


@router.post("/attention/{attention_id}/resolve")
async def resolve_attention(
    attention_id: uuid.UUID, db: DbSession, actor: Operator
) -> dict[str, Any]:
    row = await service.resolve_attention(db, attention_id, actor.user_id)
    return service.attention_view(row, None)


@router.get("/{draft_id}")
async def detail(draft_id: uuid.UUID, db: DbSession, _: Operator) -> dict[str, Any]:
    draft = await service.get_draft(db, draft_id)
    return (await _views(db, [draft]))[0]


@router.post("/{draft_id}/ignore")
async def ignore(draft_id: uuid.UUID, db: DbSession, actor: Operator) -> dict[str, Any]:
    draft = await service.get_draft(db, draft_id, lock=True)
    await service.ignore(db, draft, actor.user_id)
    return (await _views(db, [draft]))[0]


@router.post("/{draft_id}/confirm")
async def confirm(
    draft_id: uuid.UUID,
    payload: ConfirmPayload,
    db: DbSession,
    actor: Operator,
    orders: OrderServiceDep,
) -> dict[str, Any]:
    draft = await service.get_draft(db, draft_id, lock=True)
    result = await service.confirm(db, draft, payload, orders, actor.user_id)
    await db.refresh(result.order, attribute_names=["items"])
    return {
        "order": _to_detail(result.order).model_dump(mode="json"),
        "duplicate_check": _to_duplicate_response(result.duplicates).model_dump(mode="json")
        if result.duplicates
        else None,
        "replayed": result.replayed,
        "draft": (await _views(db, [draft]))[0],
    }
