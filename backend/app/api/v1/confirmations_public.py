"""The customer's confirm page (V4 WP2.3). Unauthenticated by design.

``GET /v1/public/confirmations/{token}`` only reads: SMS apps and link
previewers fetch links on their own, so opening one must change nothing.
``POST`` with ``{"result": "confirm" | "cancel"}`` records the answer, once.

Every reply has the same shape whatever the token: an unknown, expired, used
or closed link differs only in ``status`` (``OPEN``, ``ANSWERED`` or
``UNAVAILABLE``), and an unknown token reads exactly like an expired one.
Both routes are rate-limited per client address.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbSession, get_hasher, get_vault
from app.api.v1.integration_webhooks import _ip, _limit
from app.confirmations.links import TOKEN_MAX, LinkService, hash_token
from app.confirmations.models import ConfirmationLink, ConfirmationState, OrderConfirmation
from app.core.clock import utc_now
from app.db.tenancy import allow_cross_tenant
from app.messaging.receipts import as_shop
from app.orders.models import Order, OrderItem

router = APIRouter(prefix="/public/confirmations", tags=["confirmations"])

#: Lines shown on the page; a longer order says "+N more" on the client.
ITEMS_SHOWN = 10
VIEWS_PER_MINUTE = 60
ANSWERS_PER_MINUTE = 10


class LinkAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: Literal["confirm", "cancel"]


def _blank(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "result": None,
        "shop_name": None,
        "items": [],
        "item_count": 0,
        "cod_amount_paisa": None,
        "phone_masked": None,
        "expires_at": None,
    }


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex"


async def _resolve(db: DbSession, token: str) -> ConfirmationLink | None:
    if not token or len(token) > TOKEN_MAX:
        return None
    with allow_cross_tenant("confirmation link resolution"):
        return await db.scalar(
            sa.select(ConfirmationLink).where(ConfirmationLink.token_hash == hash_token(token))
        )


async def _shop_name(db: DbSession, row: OrderConfirmation) -> str | None:
    from app.tenants.models import Tenant

    tenant = await db.get(Tenant, row.tenant_id)
    return tenant.name if tenant is not None else None


async def _view(
    db: DbSession, link: ConfirmationLink, row: OrderConfirmation | None, now: datetime
) -> dict[str, Any]:
    if row is None:
        return _blank("UNAVAILABLE")
    if link.used_at is not None:
        return {**_blank("ANSWERED"), "result": link.result, "shop_name": await _shop_name(db, row)}
    if row.confirmation_state is not ConfirmationState.SMS_LINK_SENT or link.expires_at <= now:
        return _blank("UNAVAILABLE")
    order = await db.get(Order, row.order_id)
    items = (
        await db.scalars(
            sa.select(OrderItem)
            .where(OrderItem.order_id == row.order_id)
            .order_by(OrderItem.position, OrderItem.id)
        )
    ).all()
    return {
        "status": "OPEN",
        "result": None,
        "shop_name": await _shop_name(db, row),
        "items": [
            {
                "name": item.product_name,
                "variant": item.variant_label,
                "quantity": item.quantity,
            }
            for item in items[:ITEMS_SHOWN]
        ],
        "item_count": len(items),
        "cod_amount_paisa": row.cod_paisa_snapshot,
        "phone_masked": order.customer_phone_masked if order is not None else None,
        "expires_at": link.expires_at.isoformat(),
    }


@router.get("/{token}", summary="What the customer is asked to confirm (reads only)")
async def show_link(token: str, request: Request, response: Response, db: DbSession) -> Any:
    await _limit("confirmation-link-view", _ip(request), VIEWS_PER_MINUTE)
    _private(response)
    link = await _resolve(db, token)
    if link is None:
        return _blank("UNAVAILABLE")
    async with as_shop(db, link.tenant_id, "confirmation:link_view"):
        row = await db.get(OrderConfirmation, link.confirmation_id)
        return await _view(db, link, row, utc_now())


@router.post("/{token}", summary="The customer's answer, once")
async def answer_link(
    token: str, payload: LinkAnswer, request: Request, response: Response, db: DbSession
) -> Any:
    await _limit("confirmation-link-answer", _ip(request), ANSWERS_PER_MINUTE)
    _private(response)
    found = await _resolve(db, token)
    if found is None:
        return _blank("UNAVAILABLE")
    async with as_shop(db, found.tenant_id, "confirmation:link_answer"):
        row = await db.scalar(
            sa.select(OrderConfirmation)
            .where(OrderConfirmation.id == found.confirmation_id)
            .with_for_update()
        )
        link = await db.scalar(
            sa.select(ConfirmationLink).where(ConfirmationLink.id == found.id).with_for_update()
        )
        if link is None:
            return _blank("UNAVAILABLE")
        now = utc_now()
        if (
            row is not None
            and link.used_at is None
            and link.expires_at > now
            and row.confirmation_state is ConfirmationState.SMS_LINK_SENT
        ):
            from app.customers.service import CustomerService
            from app.orders.service import OrderService

            hasher = get_hasher()
            orders = OrderService(
                db, customers=CustomerService(db, hasher=hasher, vault=get_vault())
            )
            await LinkService(db, orders=orders, hasher=hasher).answer(
                row, link, confirm=payload.result == "confirm", now=now
            )
        return await _view(db, link, row, now)


__all__ = ["router"]
