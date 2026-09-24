"""Demand forecasts, reorder suggestions, forecast accuracy and the cash outlook (V3.6).

Who may do what, enforced here:

* read forecasts, reorder suggestions and accuracy: ``product.view``;
* turn a suggestion into a DRAFT purchase order: ``procurement.manage``
  (nothing is ever ordered from here; a person reviews and orders the draft);
* read the cash outlook: ``money.view``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Principal, require_permission
from app.core.clock import business_date
from app.core.errors import ConflictError, NotFoundError
from app.forecasting.engine import (
    DEFAULT_COVER_DAYS,
    HISTORY_DAYS,
    MIN_IN_STOCK_DAYS,
    MIN_UNITS,
    SERVICE_Z,
    Confidence,
)
from app.forecasting.service import ForecastService, ItemForecast
from app.procurement.service import ProcurementService
from app.tenants.roles import Permission

router = APIRouter(prefix="/forecasting", tags=["forecasting"])
Viewer = Annotated[Principal, Depends(require_permission(Permission.PRODUCT_VIEW))]
Manager = Annotated[Principal, Depends(require_permission(Permission.PROCUREMENT_MANAGE))]
MoneyViewer = Annotated[Principal, Depends(require_permission(Permission.MONEY_VIEW))]
CoverDays = Annotated[int, Query(ge=1, le=120)]

#: The rules, returned with every forecast so the screen can say how it works.
METHOD = {
    "history_days": HISTORY_DAYS,
    "min_in_stock_days": MIN_IN_STOCK_DAYS,
    "min_units": MIN_UNITS,
    "service_z": SERVICE_Z,
}


def _row(row: ItemForecast) -> dict[str, Any]:
    result = row.result
    return {
        "product_id": row.item.product_id,
        "variant_id": row.item.variant_id,
        "name": row.item.name,
        "variant_name": row.item.variant_name,
        "sku": row.item.sku,
        "confidence": str(result.confidence),
        "in_stock_days": result.in_stock_days,
        "units_sold": result.units,
        "rate_per_day": None if result.rate is None else round(result.rate, 2),
        "recent_rate_per_day": None if result.recent_rate is None else round(result.recent_rate, 2),
        "long_rate_per_day": None if result.long_rate is None else round(result.long_rate, 2),
        "forecast_30_days": result.predicted_units(30),
        "on_hand": result.on_hand,
        "incoming": result.incoming,
        "lead_time_days": result.lead_time_days,
        "lead_time_source": str(row.lead.source),
        "lead_time_samples": row.lead.samples,
        "supplier_id": row.lead.supplier_id,
        "supplier_name": row.lead.supplier_name,
        "cover_days": result.cover_days,
        "safety_stock": result.safety_stock,
        "reorder_point": result.reorder_point,
        "suggested_quantity": result.suggested_quantity,
        "days_of_cover": result.days_of_cover,
        "stockout_on": result.stockout_on,
        "at_risk": result.at_risk,
    }


def _order(row: ItemForecast) -> tuple[Any, ...]:
    result = row.result
    return (
        not result.at_risk,
        result.stockout_on or business_date() + timedelta(days=10_000),
        -(result.rate or 0),
        row.item.name.lower(),
        row.item.variant_name or "",
    )


@router.get("/demand")
async def demand(
    db: DbSession,
    actor: Viewer,
    filter: Literal["all", "at_risk", "insufficient"] = "all",
    cover_days: CoverDays = DEFAULT_COVER_DAYS,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    rows = await ForecastService(db).forecasts(cover_days=cover_days)
    counts = {
        "all": len(rows),
        "at_risk": sum(1 for r in rows if r.result.at_risk),
        "insufficient": sum(1 for r in rows if r.result.confidence is Confidence.INSUFFICIENT),
    }
    if filter == "at_risk":
        rows = [r for r in rows if r.result.at_risk]
    elif filter == "insufficient":
        rows = [r for r in rows if r.result.confidence is Confidence.INSUFFICIENT]
    rows.sort(key=_order)
    return {
        "items": [_row(r) for r in rows[offset : offset + limit]],
        "total": len(rows),
        "counts": counts,
        "cover_days": cover_days,
        "method": METHOD,
        "can_draft": actor.can(Permission.PROCUREMENT_MANAGE),
    }


@router.get("/demand/item")
async def demand_item(
    db: DbSession,
    actor: Viewer,
    product_id: uuid.UUID,
    variant_id: uuid.UUID | None = None,
    cover_days: CoverDays = DEFAULT_COVER_DAYS,
) -> dict[str, Any]:
    today = business_date()
    rows = await ForecastService(db).forecasts(
        today=today, cover_days=cover_days, product_id=product_id, variant_id=variant_id
    )
    row = next((r for r in rows if r.item.variant_id == variant_id), None)
    if row is None:
        raise NotFoundError("Item not found")
    # Completed days only: the history ends yesterday.
    first = today - timedelta(days=len(row.history.daily_units))
    return {
        **_row(row),
        "history": [
            {"date": first + timedelta(days=i), "units": units, "in_stock": ok}
            for i, (units, ok) in enumerate(
                zip(row.history.daily_units, row.history.in_stock, strict=True)
            )
        ],
        "method": METHOD,
        "can_draft": actor.can(Permission.PROCUREMENT_MANAGE),
    }


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    #: Defaults to the current suggestion; the seller may change it.
    quantity: int | None = Field(default=None, ge=1, le=100_000)


@router.post("/draft-purchase-order", status_code=201)
async def draft_purchase_order(db: DbSession, actor: Manager, body: DraftInput) -> dict[str, Any]:
    """A DRAFT purchase order for one item. Never ordered: a person reviews it."""
    quantity = body.quantity
    if quantity is None:
        rows = await ForecastService(db).forecasts(
            product_id=body.product_id, variant_id=body.variant_id
        )
        row = next((r for r in rows if r.item.variant_id == body.variant_id), None)
        if row is None:
            raise NotFoundError("Item not found")
        quantity = row.result.suggested_quantity or 0
        if quantity <= 0:
            raise ConflictError("Nothing to reorder", details={"code": "NO_SUGGESTION"})
    po, reason = await ProcurementService(db, actor.user_id).draft_for_item(
        body.product_id, body.variant_id, quantity, source="SELLER"
    )
    if po is None:
        raise ConflictError("No draft was created", details={"code": reason})
    return {"purchase_order_id": po.id, "number": po.number, "quantity": quantity}


@router.get("/accuracy")
async def accuracy(db: DbSession, actor: Viewer) -> dict[str, Any]:
    return await ForecastService(db).accuracy()


@router.get("/cash")
async def cash(db: DbSession, actor: MoneyViewer) -> dict[str, Any]:
    return await ForecastService(db).cash_outlook()
